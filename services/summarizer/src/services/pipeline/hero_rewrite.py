"""The hero rewrite — one Sonnet call writes the page's hero (output-quality 2.1, D1).

Fired at text-groups-done on ``LLM_EXTRACTION_MODEL`` (stage ``hero_rewrite``,
ledger feature ``summarize:hero_rewrite``), it replaces the gpt-4o-mini synthesis:
memory's hero stays the ≤ 16 s preview and this one replaces it at ``complete``.

The request — the split extraction's cached blocks 1 (+ 2) and the hero job in
block 3, on the groups' model — is built in ``hero_job``; this module makes the
call, checks the answer (``hero_validate``) and persists it.

One retry, only where a second answer can change the page: a timeout, an answer
that is not JSON, one ``finalize`` cannot turn into a hero (a failing tldr / brief /
takeaway set) or one that would ship a D18 line empty is asked once more, the
problems appended to block 3 (blocks 1 / 2 stay byte-identical, so the retry reads
the cache too). What ``finalize`` repairs on its own — a set over the band, a brief
over 5 sentences, a failing brief sentence while 3 remain, a failing SEO line —
costs no second call. After the retry a failing optional line is dropped; no hero
gives None — the caller keeps memory's hero and today's synthesis. Never raises.

The page's reading time is a property of the shipped page, so code counts it
over the final hero + tabs (``reading_minutes``) once assembly is done.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from llm_common.context import llm_feature_var

from ...config import settings
from ...models.hero_types import HeroRewriteResult, HeroTakeaway
from ...utils.json_parsing import parse_json_response, strip_markdown_fences
from ...utils.llm_retry import call_llm_with_retry
from ..llm_messages import TextBlock, text_block
from ..llm_model_rules import LLMRefusalError
from .hero_compact import CompactExtraction, compact_for_hero, estimate_tokens
from .hero_job import GroupBlocks, HeroRequest, cached_blocks, hero_evidence, job_text
from .hero_validate import (
    HeroDraft,
    HeroEvidence,
    Problem,
    check_hero,
    drops_a_line,
    finalize,
    parse_draft,
)
from .memory import format_clock
from .quote_verify import QuoteIndex, run_quote_index

if TYPE_CHECKING:
    from ..llm import LLMService
    from .context import PipelineContext
    from .extraction_groups import SplitRun

logger = logging.getLogger(__name__)

HERO_FEATURE = "summarize:hero_rewrite"
HERO_STAGE = "hero_rewrite"
# The answer is ≈ 0.8–1.2 K tokens (thinking off: the groups' effort, ``between_tools``).
HERO_MAX_TOKENS = 2000
# ≈ 8–12 s expected (proposal §3.4); the same budget as memory's call.
HERO_TIMEOUT_S = 25.0
# Silent reading of English non-fiction by adults (Brysbaert 2019, 238 words a minute).
READING_WPM = 238

_NOT_JSON = "the previous answer was not a JSON object"
_OVERVIEW = "overview"


# ─── Call ───


@dataclass(frozen=True)
class _Attempt:
    """One answer: its draft and failed checks, or why there is none."""

    draft: HeroDraft | None
    problems: tuple[Problem, ...] = ()
    refused: bool = False
    not_json: bool = False

    def retry_notes(self) -> list[str]:
        """What the retry tells the model (a timeout repeats the request as it was)."""
        return [_NOT_JSON] if self.not_json else [p.message for p in self.problems]


async def _ask(llm_service: LLMService, blocks: list[TextBlock]) -> str | None:
    return await call_llm_with_retry(
        llm_service,
        blocks,
        max_tokens=HERO_MAX_TOKENS,
        timeout=HERO_TIMEOUT_S,
        max_retries=0,
        stage_name=HERO_STAGE,
        json_mode=True,
        model_override=settings.get_stage_model(HERO_STAGE),
        propagate_refusal=True,
    )


def _read_answer(raw: str, evidence: HeroEvidence) -> _Attempt:
    data = parse_json_response(strip_markdown_fences(raw))
    draft = parse_draft(data, evidence) if data else None
    if draft is None:
        logger.warning("Hero rewrite: answer is not a JSON object (len=%d)", len(raw))
        return _Attempt(None, not_json=True)
    return _Attempt(draft, tuple(check_hero(draft, evidence)))


async def _attempt(
    llm_service: LLMService,
    blocks: list[TextBlock],
    evidence: HeroEvidence,
) -> _Attempt:
    try:
        raw = await _ask(llm_service, blocks)
    except LLMRefusalError as e:
        logger.warning("Hero rewrite refused (no retry): %s", e)
        return _Attempt(None, refused=True)
    if raw is None:
        logger.warning("Hero rewrite: no answer (timeout or provider error)")
        return _Attempt(None)
    return _read_answer(raw, evidence)


def _final(attempt: _Attempt, evidence: HeroEvidence) -> HeroRewriteResult | None:
    if attempt.draft is None:
        return None
    return finalize(attempt.draft, attempt.problems, evidence.takeaway_count)


def _settled(attempt: _Attempt, result: HeroRewriteResult | None) -> bool:
    """No retry can improve the answer: refused, or a hero that keeps both D18 lines."""
    return attempt.refused or (result is not None and not drops_a_line(attempt.problems))


async def rewrite_hero(llm_service: LLMService, request: HeroRequest) -> HeroRewriteResult | None:
    """The hero from one call and at most one retry (module docstring); None when no
    valid hero came back."""
    compact = compact_for_hero(
        request.extraction, request.duration, quote_index=request.quote_index
    )
    if not compact.text:
        logger.info("Hero rewrite skipped: nothing was extracted")
        return None
    evidence = hero_evidence(request, compact)
    cached = cached_blocks(request)
    _log_request(cached, compact, evidence)
    first = await _attempt(llm_service, [*cached, text_block(job_text(request, compact))], evidence)
    _log_problems(first)
    result = _final(first, evidence)
    if _settled(first, result):
        return result
    retry = text_block(job_text(request, compact, first.retry_notes()))
    second = await _attempt(llm_service, [*cached, retry], evidence)
    _log_problems(second)
    return _final(second, evidence) or result


def _log_request(
    cached: list[TextBlock], compact: CompactExtraction, evidence: HeroEvidence
) -> None:
    moved = sum(evidence.stamps.get(k) != v for k, v in compact.stamps.items())
    logger.info(
        "Hero rewrite: blocks=%s, extraction ≈ %d tokens, %d ids (%d moved to their quote)",
        "1+2" if len(cached) == 2 else "1" if cached else "job only",
        estimate_tokens(compact.text),
        len(compact.stamps),
        moved,
    )


def _log_problems(attempt: _Attempt) -> None:
    if attempt.problems:
        logger.warning(
            "Hero rewrite: %d check(s) failed: %s",
            len(attempt.problems),
            "; ".join(p.message for p in attempt.problems),
        )


async def run_hero_rewrite(
    llm_service: LLMService, request: HeroRequest
) -> HeroRewriteResult | None:
    """``rewrite_hero`` under its own ledger feature; None on any failure (never raises)."""
    token = llm_feature_var.set(HERO_FEATURE)
    try:
        result = await rewrite_hero(llm_service, request)
    except Exception:  # noqa: BLE001 — the hero is optional; memory's stays on a bug here
        logger.exception("Hero rewrite crashed; memory's hero stays")
        return None
    finally:
        llm_feature_var.reset(token)
    logger.info("Hero rewrite: %s", "written" if result else "none — memory's hero stays")
    return result


# ─── The run's entry point (``phases/synthesis`` awaits it) ───


def page_tab_labels(tabs: Sequence[Mapping[str, Any]]) -> list[str]:
    """The labels of the planned tabs, overview out, first occurrence kept."""
    labels: list[str] = []
    for tab in tabs:
        label = tab.get("label")
        if tab.get("component") == _OVERVIEW or tab.get("id") == _OVERVIEW:
            continue
        if isinstance(label, str) and label.strip() and label not in labels:
            labels.append(label)
    return labels


def _run_quote_index(ctx: PipelineContext) -> QuoteIndex | None:
    """The run's quote index; None (the items' own stamps) when it cannot be built."""
    try:
        return run_quote_index(ctx)
    except Exception:  # noqa: BLE001 — moving stamps is a refinement; the hero stays without it
        logger.exception("Hero rewrite: no quote index — takeaways keep the items' stamps")
        return None


def hero_request(
    ctx: PipelineContext, extraction: Mapping[str, Any], run: SplitRun | None = None
) -> HeroRequest:
    """The request from the run: video, reconciled tabs, memory, the quote index; the
    split run's blocks."""
    video = ctx.video_data
    assert video is not None
    plan_tabs = ctx.plan_result.tabs if ctx.plan_result else ctx.triage.tabs if ctx.triage else []
    return HeroRequest(
        title=video.title,
        duration=video.duration or None,
        extraction=extraction,
        tab_labels=page_tab_labels(plan_tabs),
        video_memory=ctx.video_memory,
        blocks=GroupBlocks(run.prompt, run.chunks) if run is not None else None,
        quote_index=_run_quote_index(ctx),
    )


async def hero_rewrite_for_run(
    ctx: PipelineContext, extraction: Mapping[str, Any], run: SplitRun | None = None
) -> HeroRewriteResult | None:
    """The hero for this run over ``extraction`` (the text groups' merge); None = keep
    memory's hero + synthesis. ``run`` = the split run (None on ``EXTRACTION_PARALLEL=false``).
    Never raises, as ``run_hero_rewrite``: the synthesis it replaces never failed a run."""
    try:
        request = hero_request(ctx, extraction, run)
        llm_service = ctx.llm_service
    except Exception:  # noqa: BLE001 — the hero is optional; memory's + synthesis stay on a bug here
        logger.exception("Hero rewrite: the request could not be built; synthesis runs")
        return None
    return await run_hero_rewrite(llm_service, request)


# ─── Meta ───

READING_MINUTES_KEY = "readingMinutes"
# A closing "[m:ss]" the model wrote despite the prompt — code writes the stamp.
_TRAILING_MARKER_RE = re.compile(r"\s*\[(?:\d{1,2}:)?\d{1,3}:\d{2}\]\s*$")


def takeaway_line(takeaway: HeroTakeaway) -> str:
    """``"<claim> [m:ss]"`` (``[h:mm:ss]`` past an hour) — the closing marker memory's
    takeaways carry, so every reader keeps ``keyTakeaways: string[]`` and the page
    seeks to the cited item's second."""
    claim = _TRAILING_MARKER_RE.sub("", takeaway.text)
    return f"{claim} [{format_clock(takeaway.timestamp)}]"


def hero_meta(result: HeroRewriteResult, minutes: int | None = None) -> dict[str, Any]:
    """The meta fields the run persists; ``masterSummary`` = the brief for older readers.

    ``minutes`` = ``reading_minutes(meta, tabs)`` over the shipped page; until it is
    counted the key is absent (never null — the relay and the web read a number or nothing).
    """
    if minutes is None:
        minutes = result.reading_minutes
    meta: dict[str, Any] = {
        "tldr": result.tldr,
        "brief": result.brief,
        "masterSummary": result.brief,
        "keyTakeaways": [takeaway_line(takeaway) for takeaway in result.key_takeaways],
        "worthItFor": result.worth_it_for,
        "skipIf": result.skip_if,
        "seoDescription": result.seo_description,
    }
    if minutes is not None:
        meta[READING_MINUTES_KEY] = minutes
    return meta


# ─── Reading time ───

# Text the page never shows as prose: ids, media, layout keys, clocks — and the frame
# metadata items carry (``frameCaption`` / ``frameOcr`` / ``frameEvidence`` …).
_UNREAD_KEYS = frozenset(
    {
        "id",
        "s3Key",
        "thumbnailUrl",
        "url",
        "imageUrl",
        "videoId",
        "youtubeId",
        "emoji",
        "icon",
        "component",
        "dataSource",
        "variant",
        "type",
        "time",
    }
)
_FRAME_PREFIX = "frame"
_HERO_TEXT_KEYS = ("tldr", "brief", "worthItFor", "skipIf")


def _is_prose_key(key: str) -> bool:
    return key not in _UNREAD_KEYS and not key.startswith(_FRAME_PREFIX)


def _words_in(value: object) -> int:
    """Words of every prose string under ``value`` (URLs, unread and frame keys skipped)."""
    if isinstance(value, str):
        return 0 if value.startswith(("http://", "https://")) else len(value.split())
    if isinstance(value, Mapping):
        return sum(_words_in(v) for k, v in value.items() if _is_prose_key(str(k)))
    if isinstance(value, list):
        return sum(_words_in(item) for item in value)
    return 0


def _takeaway_text(takeaway: object) -> object:
    """A takeaway's words: its ``text``, or the string without its closing ``[m:ss]``."""
    if isinstance(takeaway, Mapping):
        return takeaway.get("text")
    return _TRAILING_MARKER_RE.sub("", takeaway) if isinstance(takeaway, str) else None


def reading_minutes(meta: Mapping[str, Any], tabs: Sequence[Mapping[str, Any]]) -> int:
    """Minutes to read the shipped page at ``READING_WPM``: the hero (tldr, brief,
    takeaways, both lines) + every tab's label, goal and props — the overview tab
    repeats the hero, so it is not counted twice. At least 1."""
    hero = sum(_words_in(meta.get(key)) for key in _HERO_TEXT_KEYS)
    takeaways = meta.get("keyTakeaways")
    hero += _words_in([_takeaway_text(t) for t in takeaways] if isinstance(takeaways, list) else [])
    content = sum(
        _words_in([tab.get("label"), tab.get("goal"), tab.get("props")])
        for tab in tabs
        if tab.get("component") != _OVERVIEW
    )
    return max(1, math.ceil((hero + content) / READING_WPM))
