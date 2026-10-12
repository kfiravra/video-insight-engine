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
(``group_merge.validate_group``, 3.5) before it is reported. A group call that
fails (no answer after its retry, unparseable JSON, an answer holding none of
its keys, an error) is a ``failed`` outcome — today's chunked-batch handling:
the other groups still land, the caller decides what an all-failed run means.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from llm_common.context import llm_feature_var

from ...config import settings
from ...models.domain_validation import ValidationReport
from ...utils.json_parsing import parse_json_response, strip_markdown_fences
from ...utils.llm_retry import call_llm_with_retry
from .extraction_chunks import ExtractionChunk
from .extraction_group_prompt import GroupJob, build_group_messages
from .extraction_prompt import ExtractionPrompt
from .field_groups import ExtractionGroup
from .group_merge import validate_group
from .pipeline_timing import span_output_tokens

if TYPE_CHECKING:
    from ..llm import LLMService

logger = logging.getLogger(__name__)

# Released without text1's first chunk after this long (Kfir round 3: "or 1.5 s").
GROUP_RELEASE_FALLBACK_S = 1.5
GROUP_MAX_TOKENS = 16384
GROUP_TIMEOUT_S = 120.0
GROUP_MAX_RETRIES = 1
# How many of a rejected answer's top-level keys the warning lists.
_LOGGED_KEYS = 20

GroupStatus = Literal["ok", "failed"]
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
    failed); ``validation`` = what validating them dropped or defaulted."""

    group: ExtractionGroup
    chunk: int
    data: dict[str, Any] | None
    validation: ValidationReport
    status: GroupStatus
    wall_ms: int
    output_tokens: int


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


# ─── One call ───


def _parsed(raw: str | None, call: GroupCall) -> dict[str, Any] | None:
    if not raw:
        return None
    data = parse_json_response(strip_markdown_fences(raw))
    if not isinstance(data, dict) or not data:
        logger.warning("[%s] group answer is not a JSON object (len=%d)", call.stage_name, len(raw))
        return None
    return data


def _outcome(
    call: GroupCall,
    data: dict[str, Any] | None,
    started: float,
    validation: ValidationReport | None = None,
) -> GroupOutcome:
    return GroupOutcome(
        group=call.group,
        chunk=call.chunk.index,
        data=data,
        validation=validation or {"dropped": {}, "defaulted": 0},
        status="ok" if data is not None else "failed",
        wall_ms=int((time.monotonic() - started) * 1000),
        output_tokens=span_output_tokens(call.stage_name),
    )


async def run_group_call(
    call: GroupCall, run: SplitRun, *, on_first_chunk: Callable[[], None] | None = None
) -> GroupOutcome:
    """One group call: its own feature + span, Haiku, JSON; streamed for a warm-up call.

    The answer is validated and cut to the group's own keys before it is reported.
    """
    llm_feature_var.set(call.feature)
    started = time.monotonic()
    raw = await call_llm_with_retry(
        run.llm_service,
        build_group_messages(run.prompt, call.group, call.chunk, call.job),
        max_tokens=GROUP_MAX_TOKENS,
        timeout=GROUP_TIMEOUT_S,
        max_retries=GROUP_MAX_RETRIES,
        stage_name=call.stage_name,
        json_mode=True,
        model_override=settings.get_stage_model("extraction"),
        on_first_chunk=on_first_chunk,
    )
    parsed = _parsed(raw, call)
    if parsed is None:
        return _outcome(call, None, started)
    data, report = validate_group(parsed, call.group, run.content_tags, run.modifiers)
    if not data:
        logger.warning(
            "[%s] group answer kept none of its keys — failed (answer keys: %s)",
            call.stage_name,
            sorted(parsed)[:_LOGGED_KEYS],
        )
        return _outcome(call, None, started, report)
    return _outcome(call, data, started, report)


async def _guarded(
    call: GroupCall, run: SplitRun, on_first_chunk: Callable[[], None] | None = None
) -> GroupOutcome:
    """``run_group_call``; an error it raises is a failed outcome (logged with its traceback)."""
    started = time.monotonic()
    try:
        return await run_group_call(call, run, on_first_chunk=on_first_chunk)
    except Exception:
        logger.exception("[%s] group call failed", call.stage_name)
        return _outcome(call, None, started)


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
