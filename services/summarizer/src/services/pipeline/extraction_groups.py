"""Firing the split extraction's group calls — warm-first, under one semaphore (pipeline-1min 3.4).

Per chunk, text1 (the group holding the first planned tab) warms the shared
prefix and is the ONLY streamed call: Anthropic makes a cache entry readable
once the response writing it has begun, so text1's first chunk releases the
chunk's other groups, which then read blocks 1 + 2 instead of writing them
again. No chunk within ``GROUP_RELEASE_FALLBACK_S`` releases them anyway; a
text1 that fails before its first chunk releases them at once. The visual
group waits for frames-done, is re-cut from what no text group asked for
(nothing left → not fired), then waits like the rest. Every call holds a slot
of the run's ``EXTRACTION_PARALLEL_BATCHES`` semaphore; the text1 calls are
started first, so they take the first slots. Outcomes stream back as they land.

A landed answer is validated and cut to the group's own keys
(``group_merge.validate_group``, 3.5) before it is reported; one that says the
group has nothing here (``{}``, its keys ``null``) lands empty. A lost answer —
not a JSON object, no key of the group, content validation dropped, or a
warm-up stream that raised or brought nothing — is asked for ONCE more (3.8),
unstreamed (a broken stream can never cost a group) and with no retry budget
of its own; the outcome records why it ``retried``. A group still without data
(or with no answer after its own retry, or that raised) is a ``failed``
outcome — today's chunked-batch handling: the other groups still land, the
caller decides what an all-failed run means (``group_failures`` names what a
failed group costs).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from llm_common.context import llm_feature_var

from ...config import settings
from ...models.domain_validation import ValidationReport
from ...utils.json_parsing import parse_json_response, strip_markdown_fences
from ...utils.llm_retry import call_llm_with_retry
from ..llm_model_rules import LLMRefusalError
from .extraction_chunks import ExtractionChunk
from .extraction_group_prompt import GroupJob, build_group_messages
from .extraction_prompt import ExtractionPrompt
from .field_groups import ExtractionGroup
from .group_failures import answer_lost
from .group_merge import validate_group
from .pipeline_timing import span_output_tokens

if TYPE_CHECKING:
    from ..llm import LLMService

logger = logging.getLogger(__name__)

# Released without text1's first chunk after this long (Kfir round 3: "or 1.5 s").
GROUP_RELEASE_FALLBACK_S = 1.5
GROUP_MAX_TOKENS = 16384
# A group call's time budget follows its estimated answer (Σ outputWeight ×
# expect) at a pace below the extraction model's measured one (Sonnet 5.5
# ≈ 160 tok/s in the 4.0d A/B round 2, Haiku 4.5 83–91), plus the request's
# fixed cost; the old flat 120 s stays the floor, 300 s the ceiling.
GROUP_TIMEOUT_FLOOR_S = 120.0
GROUP_TIMEOUT_CAP_S = 300.0
_TIMEOUT_TOKENS_PER_S = 60.0
_TIMEOUT_OVERHEAD_S = 30.0
GROUP_MAX_RETRIES = 1
# The one more call an unusable answer gets: a single attempt (3.8).
UNUSABLE_ANSWER_RETRIES = 0
# How many of a rejected answer's top-level keys the warning lists.
_LOGGED_KEYS = 20

GroupStatus = Literal["ok", "failed"]
# Why a group was asked once more: its answer was not a JSON object ("parse"),
# validation kept none of the content it held ("empty"), or the warm-up's
# stream raised or brought nothing back ("stream").
RetryCause = Literal["parse", "empty", "stream"]
# The visual group cut at frames-done from the fields no text group asked for.
VisualCut = Callable[[frozenset[str]], Awaitable[tuple[ExtractionGroup, GroupJob] | None]]


@dataclass(frozen=True)
class GroupCall:
    """One group over one chunk — one LLM call."""

    group: ExtractionGroup
    chunk: ExtractionChunk
    job: GroupJob

    @property
    def feature(self) -> str:
        """``llm_usage.feature`` — cost per video by group."""
        return f"summarize:extraction:{self.group.group_id}"

    @property
    def stage_name(self) -> str:
        """The span (``extraction_text1``; chunk k ≥ 1 adds ``_c{k}``)."""
        suffix = f"_c{self.chunk.index}" if self.chunk.index else ""
        return f"extraction_{self.group.group_id}{suffix}"


@dataclass(frozen=True)
class GroupOutcome:
    """A landed group call: ``data`` = its validated own keys, domain-keyed (None when it
    failed); ``validation`` = what validating them dropped or defaulted; ``retried`` =
    why its first answer was unusable (None when it was not asked again)."""

    group: ExtractionGroup
    chunk: int
    data: dict[str, Any] | None
    validation: ValidationReport
    status: GroupStatus
    wall_ms: int
    output_tokens: int
    retried: RetryCause | None = None


@dataclass(frozen=True)
class SplitRun:
    """Everything the calls of one run share."""

    llm_service: LLMService
    prompt: ExtractionPrompt
    job: GroupJob  # the text groups' job inputs (plan-done tabs)
    chunks: tuple[ExtractionChunk, ...]
    text_groups: tuple[ExtractionGroup, ...]
    content_tags: tuple[str, ...]  # which model validates a group's domain
    modifiers: tuple[str, ...]
    # The group-job template, read once at plan-done (a registry miss must not
    # cost every call a fetch); None = each call reads it.
    job_template: str | None = None


# ─── One call ───


def group_timeout(group: ExtractionGroup) -> float:
    """Seconds one call of ``group`` may take — streamed warm-up and extra call included."""
    estimate = group.est_output_tokens / _TIMEOUT_TOKENS_PER_S + _TIMEOUT_OVERHEAD_S
    return min(max(estimate, GROUP_TIMEOUT_FLOOR_S), GROUP_TIMEOUT_CAP_S)


def _empty_object(text: str) -> bool:
    """A bare ``{}`` answer (``parse_json_response`` returns ``{}`` for unparseable text too)."""
    try:
        return json.loads(text) == {}
    except ValueError:
        return False


def _parsed(raw: str, call: GroupCall) -> dict[str, Any] | None:
    text = strip_markdown_fences(raw)
    data = parse_json_response(text)
    if data or _empty_object(text):
        return data
    logger.warning("[%s] group answer is not a JSON object (len=%d)", call.stage_name, len(raw))
    return None


@dataclass(frozen=True)
class _Answer:
    """One call's answer read for its group: the validated own keys — ``{}`` when it says
    the group has nothing here, None when nothing usable came back."""

    data: dict[str, Any] | None
    report: ValidationReport | None
    unusable: RetryCause | None  # why it is worth asking for once more


_NO_ANSWER = _Answer(None, None, None)


def _read(raw: str | None, call: GroupCall, run: SplitRun) -> _Answer:
    """``raw`` validated and cut to the group's keys. No answer at all is not "unusable":
    the call's own retry is already spent."""
    if raw is None:
        return _NO_ANSWER
    parsed = _parsed(raw, call)
    if parsed is None:
        return _Answer(None, None, "parse")
    data, report = validate_group(parsed, call.group, run.content_tags, run.modifiers)
    if data:
        return _Answer(data, report, None)
    if not answer_lost(parsed, call.group, report):
        return _Answer({}, report, None)  # e.g. a later chunk's window without the content
    logger.warning(
        "[%s] group answer kept none of its keys (answer keys: %s)",
        call.stage_name,
        sorted(parsed)[:_LOGGED_KEYS],
    )
    return _Answer(None, report, "empty")


def _outcome(
    call: GroupCall, answer: _Answer, started: float, retried: RetryCause | None
) -> GroupOutcome:
    return GroupOutcome(
        group=call.group,
        chunk=call.chunk.index,
        data=answer.data,
        validation=answer.report or {"dropped": {}, "defaulted": 0},
        status="ok" if answer.data is not None else "failed",
        wall_ms=int((time.monotonic() - started) * 1000),
        output_tokens=span_output_tokens(call.stage_name),
        retried=retried,
    )


async def _ask(
    call: GroupCall, run: SplitRun, retries: int, on_first_chunk: Callable[[], None] | None
) -> str | None:
    return await call_llm_with_retry(
        run.llm_service,
        build_group_messages(run.prompt, call.group, call.chunk, call.job, run.job_template),
        max_tokens=GROUP_MAX_TOKENS,
        timeout=group_timeout(call.group),
        max_retries=retries,
        stage_name=call.stage_name,
        json_mode=True,
        model_override=settings.get_stage_model("extraction"),
        on_first_chunk=on_first_chunk,
        # A refused warm-up must not pass for a broken stream (asked once more).
        propagate_refusal=on_first_chunk is not None,
    )


async def _first_answer(
    call: GroupCall, run: SplitRun, on_first_chunk: Callable[[], None] | None
) -> _Answer:
    """The first call's answer. A warm-up stream that raises or brings nothing back is
    worth one more call too ("stream"); a plain call's error goes to ``_guarded``. A
    refused warm-up releases the waiting groups and is no answer: asking again would
    send it to the model that refused (4.0b)."""
    if on_first_chunk is None:
        return _read(await _ask(call, run, GROUP_MAX_RETRIES, None), call, run)
    try:
        raw = await _ask(call, run, GROUP_MAX_RETRIES, on_first_chunk)
    except LLMRefusalError as e:
        logger.warning("[%s] warm-up refused: %s", call.stage_name, e)
        on_first_chunk()
        return _NO_ANSWER
    except Exception:
        logger.exception("[%s] streamed group call failed", call.stage_name)
        return _Answer(None, None, "stream")
    if raw is None:
        return _Answer(None, None, "stream")
    return _read(raw, call, run)


async def _second_answer(call: GroupCall, run: SplitRun, first: _Answer) -> _Answer:
    """One attempt, unstreamed; an error is no answer. Without a report of its own the
    answer keeps the first one's."""
    try:
        raw = await _ask(call, run, UNUSABLE_ANSWER_RETRIES, None)
    except Exception:
        logger.exception("[%s] the group's extra call failed", call.stage_name)
        raw = None
    second = _read(raw, call, run)
    return second if second.report is not None else replace(second, report=first.report)


async def run_group_call(
    call: GroupCall, run: SplitRun, *, on_first_chunk: Callable[[], None] | None = None
) -> GroupOutcome:
    """One group call: its own feature + span, the extraction model, JSON; a warm-up streams.

    The answer is validated and cut to the group's own keys before it is
    reported. A lost answer (``group_failures.answer_lost``; not a JSON object;
    a warm-up stream that raised or brought nothing) is asked for once more
    under the same span — one attempt, never streamed, so a broken stream
    cannot lose the group: at most two calls. A warm-up releases the groups
    waiting on it first instead of holding them through the retry.
    """
    llm_feature_var.set(call.feature)
    started = time.monotonic()
    first = await _first_answer(call, run, on_first_chunk)
    if first.unusable is None:
        return _outcome(call, first, started, None)
    if on_first_chunk is not None:
        on_first_chunk()
    logger.warning(
        "[%s] unusable group answer (%s) — asking once more, unstreamed",
        call.stage_name,
        first.unusable,
    )
    second = await _second_answer(call, run, first)
    return _outcome(call, second, started, first.unusable)


async def _guarded(
    call: GroupCall, run: SplitRun, on_first_chunk: Callable[[], None] | None = None
) -> GroupOutcome:
    """``run_group_call``; an error it raises is a failed outcome (logged with its traceback)."""
    started = time.monotonic()
    try:
        return await run_group_call(call, run, on_first_chunk=on_first_chunk)
    except Exception:
        logger.exception("[%s] group call failed", call.stage_name)
        return _outcome(call, _NO_ANSWER, started, None)


# ─── Release + slots ───


@dataclass(frozen=True)
class _Lanes:
    """The run's call slots and the queue every call task reports to (once)."""

    slots: asyncio.Semaphore
    landed: asyncio.Queue[GroupOutcome | None]


async def _release(warm: asyncio.Event) -> None:
    """Return at text1's first chunk, or after ``GROUP_RELEASE_FALLBACK_S`` without one."""
    try:
        await asyncio.wait_for(warm.wait(), GROUP_RELEASE_FALLBACK_S)
    except TimeoutError:
        logger.info(
            "Group calls released without the warm-up's first chunk after %.1fs",
            GROUP_RELEASE_FALLBACK_S,
        )


async def _warm_up(call: GroupCall, run: SplitRun, warm: asyncio.Event, lanes: _Lanes) -> None:
    outcome: GroupOutcome | None = None
    try:
        async with lanes.slots:
            outcome = await _guarded(call, run, on_first_chunk=warm.set)
    finally:
        warm.set()
        lanes.landed.put_nowait(outcome)


async def _released(call: GroupCall, run: SplitRun, warm: asyncio.Event, lanes: _Lanes) -> None:
    outcome: GroupOutcome | None = None
    try:
        await _release(warm)
        async with lanes.slots:
            outcome = await _guarded(call, run)
    finally:
        lanes.landed.put_nowait(outcome)


async def _cut(
    visual: asyncio.Task[tuple[ExtractionGroup, GroupJob] | None],
) -> tuple[ExtractionGroup, GroupJob] | None:
    """The shared frames-done cut (shielded: one chunk's cancel must not cancel it)."""
    try:
        return await asyncio.shield(visual)
    except Exception:
        logger.exception("Visual group cut failed — the visual group is not fired")
        return None


async def _visual(
    chunk: ExtractionChunk,
    run: SplitRun,
    visual: asyncio.Task[tuple[ExtractionGroup, GroupJob] | None],
    warm: asyncio.Event,
    lanes: _Lanes,
) -> None:
    outcome: GroupOutcome | None = None
    try:
        cut = await _cut(visual)
        if cut is not None:
            group, job = cut
            await _release(warm)
            async with lanes.slots:
                outcome = await _guarded(GroupCall(group, chunk, job), run)
    finally:
        lanes.landed.put_nowait(outcome)


# ─── The run ───


def _start(
    run: SplitRun,
    lanes: _Lanes,
    visual: asyncio.Task[tuple[ExtractionGroup, GroupJob] | None],
) -> list[asyncio.Task[None]]:
    """Every chunk's text1 first (they take the first slots), then the rest."""
    warm = {chunk.index: asyncio.Event() for chunk in run.chunks}
    lead, rest = run.text_groups[:1], run.text_groups[1:]
    first: list[Awaitable[None]] = []
    later: list[Awaitable[None]] = []
    for chunk in run.chunks:
        if lead:
            first.append(
                _warm_up(GroupCall(lead[0], chunk, run.job), run, warm[chunk.index], lanes)
            )
        else:
            warm[chunk.index].set()  # nothing to warm: the visual group goes at once
        later += [
            _released(GroupCall(g, chunk, run.job), run, warm[chunk.index], lanes) for g in rest
        ]
        later.append(_visual(chunk, run, visual, warm[chunk.index], lanes))
    return [asyncio.ensure_future(coro) for coro in [*first, *later]]


async def fire_groups(
    run: SplitRun, cut_visual: VisualCut
) -> AsyncGenerator[GroupOutcome | None, None]:
    """Fire every chunk × group; yield each outcome as it lands, ``None`` per unfired visual group.

    Yields exactly one item per call task (``len(chunks) × (text groups + 1)``).
    Stopping early (cancellation) cancels every call still running.
    """
    lanes = _Lanes(asyncio.Semaphore(max(1, settings.EXTRACTION_PARALLEL_BATCHES)), asyncio.Queue())
    fired = frozenset(f.path for group in run.text_groups for f in group.fields)
    visual = asyncio.ensure_future(cut_visual(fired))
    tasks = _start(run, lanes, visual)
    try:
        for _ in tasks:
            yield await lanes.landed.get()
    finally:
        for task in [visual, *tasks]:
            task.cancel()
        await asyncio.gather(visual, *tasks, return_exceptions=True)
