"""merge_rank — one Haiku call dedupes and ranks a long video's lists (output-quality 3.1).

A chunked run's field groups write every list once per chunk, and the code merge
(``group_merge``) can drop a repeat only when its name matches exactly: a point made
across a chunk boundary survives twice under two names, the same content sits in two
tabs, ``learning.keyIdeas`` carries each chunk's own section labels, and assembly's caps
keep items by richness, not by what the video argues (L16, L20).

**When:** ``MERGE_RANK_ENABLED`` on a run of more than one chunk, at text-groups-done —
the moment the hero starts (``phases/group_emission.GroupLanding``) — over the text
groups' merge: the lists are final by then (the visual group is cut from fields no text
group asked for). One chunk, the flag off or ``EXTRACTION_PARALLEL=false`` → no call and
today's bytes.

**The call:** ``LLM_MEMORY_MODEL`` (Haiku 5.5; stage / span ``merge_rank``, ledger
feature ``summarize:merge_rank``, timing step ``merge_rank_call``), JSON mode. The input
is code-rendered (``merge_rank_render``: the outline + every list a planned tab reads,
each item under an id); the answer is ids only::

    {"duplicates": [{"keep": "K9", "drop": ["Q3", "S4"]}], "rank": ["K7", "K1"]}

The key ideas' sections are code's (``merge_rank_apply``: the outline section holding each
idea's stamp): asked for them, the call placed none of S13's 33 ideas where its stamp
lies (p3i3).

12 s an attempt and one wrapper retry, the whole stage inside 25 s (the deadline cancels
the attempt in flight, so it is recorded here, as quiz v2's); a reply that is no JSON
object is asked for once more inside the same deadline, a refusal never. S02 (83 min):
≈ 9 K tokens in, ≈ 0.5 K out ≈ $0.001 (Haiku 5.5 $0.10 / $0.50 per MTok); 4–6 s, behind
the hero and the quiz.

**Reading the answer:** an unknown id goes; a group whose keep an earlier group dropped
goes; an id two groups claim stays with the first; a keep never drops itself; the rank
keeps the ids of the ``[rank]`` lists. ``merge_rank_apply``
then edits the FINAL merge in ``split_extraction._complete`` (guards, rank cut, sections),
before ``extraction_complete``. The hero and the quiz read the extraction as it was at
text-groups-done — they start at the same moment, and a dropped twin leaves its kept
twin on the page.

Never raises: a call that does not answer drops and cuts nothing — the key ideas are
still sectioned, which is code — and a bug here leaves the code merge as it is; the
outcome is recorded as ``pipeline.extraction._mergeRank``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from llm_common.context import llm_feature_var

from ...config import settings
from ...utils.json_parsing import parse_json_response
from ...utils.llm_retry import call_llm_with_retry
from ..llm_model_rules import LLMRefusalError
from .merge_rank_apply import OK, DuplicateGroup, MergeRankAnswer, apply_merge_rank
from .merge_rank_render import MergeRankRequest, RankedItem, RenderInput, build_request
from .pipeline_timing import record_llm_failure, timed_step
from .prompt_builder import PROMPTS_DIR, load_prompt_text

if TYPE_CHECKING:
    from ..llm import LLMService
    from .context import PipelineContext
    from .extraction_groups import SplitRun

logger = logging.getLogger(__name__)

PROMPT_PATH = PROMPTS_DIR / "merge_rank.txt"
MERGE_RANK_STAGE = "merge_rank"
MERGE_RANK_FEATURE = "summarize:merge_rank"
MERGE_RANK_CALL_STEP = "merge_rank_call"
# The answer is ids only: ≈ 0.5 K tokens on S02's 113 items (≤ 1.5 K by design).
MERGE_RANK_MAX_TOKENS = 2000
MERGE_RANK_ATTEMPT_TIMEOUT_S = 12.0
MERGE_RANK_TOTAL_TIMEOUT_S = 25.0
MERGE_RANK_MAX_RETRIES = 1

SKIPPED = "skipped"  # no planned tab reads a list
FAILED = "failed"  # no reply (provider error after the wrapper's retry)
TIMEOUT = "timeout"
REFUSED = "refused"
NOT_JSON = "not_json"  # twice no JSON object
CRASHED = "crashed"


# ─── Reading the answer ───


def _ids(value: object) -> list[str]:
    return [v.strip() for v in value if isinstance(v, str)] if isinstance(value, list) else []


def read_duplicates(raw: object, known: Mapping[str, RankedItem]) -> tuple[DuplicateGroup, ...]:
    """The answer's groups that hold (module docstring), in the answer's order; a keep
    named by two groups gathers both groups' drops."""
    groups: dict[str, list[str]] = {}
    dropped: set[str] = set()
    for entry in raw if isinstance(raw, list) else []:
        keep = entry.get("keep") if isinstance(entry, Mapping) else None
        keep = keep.strip() if isinstance(keep, str) else ""
        if keep not in known or keep in dropped:
            continue
        drops = [d for d in dict.fromkeys(_ids(entry.get("drop"))) if d in known and d != keep]
        fresh = [d for d in drops if d not in dropped and d not in groups]
        if fresh:
            groups.setdefault(keep, []).extend(fresh)
            dropped.update(fresh)
    return tuple(DuplicateGroup(keep, tuple(drops)) for keep, drops in groups.items())


def read_rank(raw: object, request: MergeRankRequest) -> tuple[str, ...]:
    """The answer's rank over the ``[rank]`` lists' ids, each id once."""
    ranked = {spec.path for spec in request.lists if spec.cap is not None}
    known = request.items
    return tuple(dict.fromkeys(i for i in _ids(raw) if i in known and known[i].path in ranked))


def read_answer(data: Mapping[str, Any], request: MergeRankRequest) -> MergeRankAnswer:
    return MergeRankAnswer(
        OK,
        request,
        duplicates=read_duplicates(data.get("duplicates"), request.items),
        rank=read_rank(data.get("rank"), request),
    )


# ─── The call ───


def merge_rank_prompt(request: MergeRankRequest) -> str:
    """Data first (the video, the outline, the lists), the rules last."""
    return f"{request.text}\n\n{load_prompt_text(PROMPT_PATH).strip()}\n"


async def _ask(llm_service: LLMService, prompt: str, deadline: float) -> str | None:
    """The reply, bounded by the stage's ``deadline`` (loop time; recorded here: the
    deadline cancels the attempt in flight, so neither the provider nor the retry wrapper
    sees it)."""
    model = settings.get_stage_model(MERGE_RANK_STAGE)
    started = time.monotonic()
    try:
        async with asyncio.timeout_at(deadline):
            return await call_llm_with_retry(
                llm_service,
                prompt,
                max_tokens=MERGE_RANK_MAX_TOKENS,
                timeout=MERGE_RANK_ATTEMPT_TIMEOUT_S,
                max_retries=MERGE_RANK_MAX_RETRIES,
                stage_name=MERGE_RANK_STAGE,
                json_mode=True,
                use_fast_model=True,
                model_override=model,
                propagate_refusal=True,
            )
    except TimeoutError as e:
        record_llm_failure(
            span=MERGE_RANK_STAGE,
            model=model or llm_service.fast_model,
            error=e,
            start_monotonic=started,
            attempt=0,
        )
        raise


async def _reply(llm_service: LLMService, prompt: str) -> tuple[str, dict[str, Any]]:
    """(status, the answer's object): one more call when a reply is no JSON object."""
    deadline = asyncio.get_running_loop().time() + MERGE_RANK_TOTAL_TIMEOUT_S
    try:
        for attempt in (1, 2):
            raw = await _ask(llm_service, prompt, deadline)
            if raw is None:
                return FAILED, {}
            data = parse_json_response(raw)
            if data:
                return OK, data
            logger.warning("merge_rank: reply %d is no JSON object (len=%d)", attempt, len(raw))
    except TimeoutError:
        logger.warning("merge_rank: no answer within %.0f s", MERGE_RANK_TOTAL_TIMEOUT_S)
        return TIMEOUT, {}
    except LLMRefusalError as e:
        logger.warning("merge_rank refused (no retry): %s", e)
        return REFUSED, {}
    return NOT_JSON, {}


async def run_merge_rank(
    llm_service: LLMService, request: MergeRankRequest | None, no_request: str = SKIPPED
) -> MergeRankAnswer:
    """The checked answer — ``no_request`` as the status when there is no request; never
    raises."""
    if request is None:
        return MergeRankAnswer(no_request)
    started = time.monotonic()
    try:
        status, data = await _reply(llm_service, merge_rank_prompt(request))
        answer = read_answer(data, request) if status == OK else MergeRankAnswer(status, request)
    except Exception:  # noqa: BLE001 — merge_rank is optional; its lists stand on a bug here
        logger.exception("merge_rank crashed; no drop or rank cut (the sections still apply)")
        answer = MergeRankAnswer(CRASHED, request)
    return replace(answer, ms=int((time.monotonic() - started) * 1000))


# ─── The run (``phases/group_emission`` starts it, ``split_extraction`` applies it) ───


def _render_input(
    ctx: PipelineContext, extraction: Mapping[str, Any], run: SplitRun
) -> RenderInput:
    video, plan, memory = ctx.video_data, ctx.plan_result, ctx.memory
    duration = int(video.duration) if video is not None and video.duration else None
    return RenderInput(
        extraction=extraction,
        tabs=list(plan.tabs) if plan is not None else [],
        outline=list(memory.outline) if memory is not None else [],
        chunks=run.chunks,
        duration=duration,
        title=video.title if video is not None else "",
    )


def _request(
    ctx: PipelineContext, extraction: Mapping[str, Any], run: SplitRun
) -> tuple[MergeRankRequest | None, str]:
    """The request from the run, and the status a missing one records."""
    try:
        return build_request(_render_input(ctx, extraction, run)), SKIPPED
    except Exception:  # noqa: BLE001 — merge_rank is optional; the code merge stands on a bug here
        logger.exception("merge_rank: the input could not be rendered; the code merge stands")
        return None, CRASHED


async def _timed_merge_rank(
    llm_service: LLMService, request: MergeRankRequest | None, no_request: str
) -> MergeRankAnswer:
    with timed_step(MERGE_RANK_CALL_STEP):
        return await run_merge_rank(llm_service, request, no_request)


def start_merge_rank_task(
    ctx: PipelineContext, extraction: Mapping[str, Any], run: SplitRun
) -> None:
    """Start merge_rank now (text-groups-done on a chunked run) over ``extraction``. The
    input is rendered here, before any other started call runs; the task copies the
    context at creation, so its call carries its own ledger feature."""
    request, no_request = _request(ctx, extraction, run)
    token = llm_feature_var.set(MERGE_RANK_FEATURE)
    try:
        ctx.merge_rank_task = asyncio.create_task(
            _timed_merge_rank(ctx.llm_service, request, no_request),
            name=f"merge_rank_{ctx.video_summary_id}",
        )
    finally:
        llm_feature_var.reset(token)


def cancel_merge_rank_task(ctx: PipelineContext) -> None:
    """Stop a started call nobody will await (the run failed or was cancelled)."""
    task = getattr(ctx, "merge_rank_task", None)
    if task is not None and not task.done():
        task.cancel()


def _log_record(ctx: PipelineContext, record: Mapping[str, Any]) -> None:
    logger.info(
        "merge_rank: %s in %d ms — %d dropped, %d held, ranked %s, sectioned %s (video_id=%s)",
        record.get("status"),
        record.get("ms", 0),
        len(record.get("dropped") or []),
        len(record.get("held") or []),
        record.get("ranked") or "none",
        record.get("sectioned") or "none",
        ctx.video_summary_id,
    )


async def merge_rank_extraction(ctx: PipelineContext, extraction: dict[str, Any]) -> dict[str, Any]:
    """The final merge with the started call's answer applied (the sections alone when the
    call did not answer) — ``extraction`` itself when no call started or the edit failed;
    the outcome on ``ctx.merge_rank_record``."""
    task = getattr(ctx, "merge_rank_task", None)
    if task is None:
        return extraction
    try:
        merged, record = apply_merge_rank(extraction, await task)
    except Exception:  # noqa: BLE001 — merge_rank is optional; the code merge stands on a bug here
        logger.exception("merge_rank: the answer could not be applied; the code merge stands")
        ctx.merge_rank_record = {"status": CRASHED}
        return extraction
    ctx.merge_rank_record = record
    _log_record(ctx, record)
    return merged
