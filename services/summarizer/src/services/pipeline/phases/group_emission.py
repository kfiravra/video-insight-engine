"""Per-group assembly + emission on the split path (pipeline-1min 3.6).

As each extraction group lands, the response is assembled over a deep copy of
the extraction merged so far (A17: assembly writes thumbnails into the items it
is handed), with memory's hero and no quiz, and every planned tab whose
requested fields have all landed — ok or failed, so a failed group never holds
a tab back for ever — streams as a **preview** (A1): non-final, without its
cross-tab links and attachments (they exist only in the finished response and
arrive with the closing re-emit), at the position its plan order gives it —
the estimate the provisional Moments tab uses — so a re-send means new
content. Held back: the overview (built from every tab and synthesis), the
moment tabs (framed by the moment fill, as today) and the quiz (no answer yet).

After the final assembly, ``assembled_tab_events`` previews whatever the
client still lacks — tabs assembly added on its own (backfills, the
filmstrip, the minimum-3 fallbacks), tabs the last groups or the frames
changed, and the overview — at their exact positions. Every tab's single
final emission follows from the late group or the closing re-emit.

A partial assembly logs only its errors: its drop and missing-data warnings
describe fields still in flight (the final assembly logs the real ones). One
filter on the assembly loggers drops them while ``_PARTIAL_ASSEMBLY`` is set
in the current context, so another run's assembly logs as usual.

The quiz starts from here too (``ctx.quiz_task``, B.6): once every text group
holding a field of a ``quizDomains`` domain has landed — the visual group when
no text group holds one — over the extraction merged so far. A strip-only quiz
(no quiz tab) also waits until frames-done can no longer drop its host.

So does the hero rewrite (``ctx.hero_task``, output-quality 2.2, D1): once every
text group landed on every chunk (text-groups-done), over the extraction merged so
far, reading the run's cached blocks and — on a chunked run — every chunk's
scalars (``group_merge.chunk_scalars``, 3.2); assembly's synthesis slot awaits it. On a
chunked run merge_rank starts beside it (``ctx.merge_rank_task``, 3.1) over the same
merge; the split extraction applies its answer to the final merge.
"""

from __future__ import annotations

import copy
import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.config import settings
from src.models.sse_events import SSEEventValidationError
from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.enrichment import QUIZ_COMPONENT, QUIZ_DATA_SOURCE
from src.services.pipeline.group_merge import chunk_scalars, merge_groups
from src.services.pipeline.merge_rank import start_merge_rank_task
from src.services.pipeline.phases.enrichment import start_quiz_task
from src.services.pipeline.phases.moments import video_meta
from src.services.pipeline.phases.synthesis import start_hero_task
from src.services.pipeline.quote_verify import run_quote_index
from src.services.pipeline.reconcile_pass import MAX_TABS
from src.services.pipeline.synthesis import build_synthesis_dict
from src.services.pipeline.tab_ledger import tab_id
from src.shared_config.domain_config import quiz_enrichment

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext
    from src.services.pipeline.extraction_groups import GroupOutcome, SplitRun
    from src.services.pipeline.field_groups import ExtractionGroup
    from src.services.pipeline.field_requests import FieldRequest

logger = logging.getLogger(__name__)

_OVERVIEW = "overview"
_MOMENTS = "moment_track"
# Framed by the moment fill / answered by the late quiz: never previewed.
_LATE_COMPONENTS = frozenset({_MOMENTS, QUIZ_COMPONENT})
# Not previewed while groups land: the overview is built from every tab.
_HELD_PER_GROUP = frozenset({_OVERVIEW, *_LATE_COMPONENTS})
# Built over the whole response: only the closing re-emit carries them.
_FINAL_ONLY_KEYS = ("crossTabLinks", "attachments")

# ─── Partial-assembly logs ───

_ASSEMBLY_LOGGER_PREFIX = "src.services.pipeline.assembly."
# The modules whose loggers assemble_response and its passes write to.
_ASSEMBLY_MODULES = (
    "core",
    "dedupe",
    "promotion",
    "density",
    "attachments",
    "assemblers_overhaul",
    "item_timestamps",
    "moment_fallback",
)
_PARTIAL_ASSEMBLY: ContextVar[bool] = ContextVar("partial_assembly", default=False)


class _PendingFieldsQuiet(logging.Filter):
    """Drops the assembly package's records below ERROR while a partial assembly runs."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not (
            _PARTIAL_ASSEMBLY.get()
            and record.levelno < logging.ERROR
            and record.name.startswith(_ASSEMBLY_LOGGER_PREFIX)
        )


def _install_partial_assembly_filter() -> None:
    quiet = _PendingFieldsQuiet()
    for module in _ASSEMBLY_MODULES:
        logging.getLogger(f"{_ASSEMBLY_LOGGER_PREFIX}{module}").addFilter(quiet)


_install_partial_assembly_filter()


# ─── Previews ───


def _preview(tab: dict) -> dict:
    return {key: value for key, value in tab.items() if key not in _FINAL_ONLY_KEYS}


def _plan_position(plan_tabs: Sequence[Mapping[str, Any]], wanted: str) -> int | None:
    """The overview's slot + the planned tabs before ``wanted`` that keep plan order
    (assembly puts the overview first and the quiz last); None when not planned."""
    before = 0
    for tab in plan_tabs:
        if str(tab.get("id", "")) == wanted:
            return 1 + before
        if tab.get("component") not in (_OVERVIEW, QUIZ_COMPONENT):
            before += 1
    return None


def _waits(fields: Sequence[FieldRequest]) -> dict[str, set[str]]:
    """Tab id → the requested field paths it waits for."""
    waits: dict[str, set[str]] = {}
    for request in fields:
        for waiting in request.tab_ids:
            waits.setdefault(waiting, set()).add(request.path)
    return waits


def _ready(tab: dict, waits: Mapping[str, set[str]], landed: frozenset[str]) -> bool:
    """A planned tab whose requested fields all landed (not the overview, a moment tab or
    the quiz)."""
    if tab.get("component") in _HELD_PER_GROUP or tab_id(tab) == _OVERVIEW:
        return False
    paths = waits.get(tab_id(tab))
    return bool(paths) and paths <= landed


def _partial_tabs(ctx: PipelineContext, merged: Mapping[str, Any]) -> list[dict]:
    """The response over what landed so far: no quiz, memory's hero (synthesis has not run)."""
    token = _PARTIAL_ASSEMBLY.set(True)
    try:
        assembled = assemble_response(
            triage=ctx.triage_dict,
            extraction=copy.deepcopy(dict(merged)),
            enrichment=None,
            synthesis=copy.deepcopy(build_synthesis_dict(ctx.memory, None)) or None,
            video_meta=video_meta(ctx),
            frames=ctx.scene_frames_for_assembly,
            gallery_frames=ctx.scene_frames_gallery,
            all_frames=ctx.scene_frames_all,
            frame_descriptions=ctx.frame_descriptions or None,
            quote_index=run_quote_index(ctx),
        )
    finally:
        _PARTIAL_ASSEMBLY.reset(token)
    return assembled.get("tabs", [])


def ready_tab_events(
    ctx: PipelineContext,
    fields: Sequence[FieldRequest],
    landed: frozenset[str],
    merged: Mapping[str, Any],
) -> list[str]:
    """A preview of every tab whose requested fields in ``fields`` all landed and whose
    content the client does not already hold (never the overview, a moment tab or the quiz)."""
    waits = _waits(fields)
    tabs = _partial_tabs(ctx, merged)
    plan_tabs = ctx.plan_result.tabs if ctx.plan_result else []
    ledger = ctx.tab_ledger
    ledger.track_response(tabs)
    events: list[str] = []
    for index, tab in enumerate(tabs):
        preview = _preview(tab)
        if not _ready(tab, waits, landed) or ledger.holds(preview):
            continue
        position = _plan_position(plan_tabs, tab_id(tab))
        events.append(ledger.event(preview, index if position is None else position, preview=True))
    return events


def _safe_ready_tab_events(
    ctx: PipelineContext,
    fields: Sequence[FieldRequest],
    landed: frozenset[str],
    merged: Mapping[str, Any],
) -> list[str]:
    """:func:`ready_tab_events`; nothing when a partial assembly fails — the closing
    re-emit still sends every tab. An SSE contract violation still raises."""
    try:
        return ready_tab_events(ctx, fields, landed, merged)
    except SSEEventValidationError:
        raise
    except Exception:  # noqa: BLE001 — a preview must never fail a run the final assembly can finish
        logger.exception(
            "Per-group tabs skipped — they still come with the closing re-emit",
            extra={"video_id": ctx.video_summary_id},
        )
        return []


def assembled_tab_events(ctx: PipelineContext) -> list[str]:
    """After the final assembly: a preview of each of its tabs the client lacks, at its
    exact position — the overview included; the moment tabs (the fill frames them)
    and the quiz (not in yet) excluded."""
    ledger = ctx.tab_ledger
    return [
        ledger.event(_preview(tab), position, preview=True)
        for position, tab in enumerate(ctx.assembled_tabs or [])
        if tab.get("component") not in _LATE_COMPONENTS and not ledger.holds(_preview(tab))
    ]


# ─── The member's landing state ───


def _quiz_owners(text_groups: Sequence[ExtractionGroup]) -> frozenset[str]:
    """The text groups holding a field of a ``quizDomains`` domain."""
    domains = set(quiz_enrichment()["quizDomains"])
    return frozenset(g.group_id for g in text_groups if any(f.domain in domains for f in g.fields))


def _plans_quiz_tab(tabs: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        t.get("component") == QUIZ_COMPONENT or t.get("dataSource") == QUIZ_DATA_SOURCE
        for t in tabs
    )


@dataclass(frozen=True)
class _TextDoneCalls:
    """The split run for each call text-groups-done starts now (None = not due)."""

    hero: SplitRun | None = None
    merge_rank: SplitRun | None = None

    def __bool__(self) -> bool:
        return self.hero is not None or self.merge_rank is not None

    def start(
        self,
        ctx: PipelineContext,
        merged: Mapping[str, Any],
        outcomes: Sequence[GroupOutcome],
    ) -> None:
        """Start the due calls over the same merge: neither reads the other's answer. The
        hero also reads a chunked run's scalars part by part (output-quality 3.2)."""
        if self.hero is not None:
            parts = chunk_scalars(outcomes, self.hero.chunks)
            start_hero_task(ctx, merged, self.hero, chunk_scalars=parts)
        if self.merge_rank is not None:
            start_merge_rank_task(ctx, merged, self.merge_rank)


@dataclass
class GroupLanding:
    """What landed so far on the split path; each landed call streams its ready tabs.

    A field has landed once every chunk's call of its group did (ok or failed).
    """

    ctx: PipelineContext
    calls_per_group: int  # the run's chunks
    quiz_owners: frozenset[str]  # empty → the visual group owns the quiz
    # The split run: its text groups gate the hero, its blocks feed it (None → no early hero).
    run: SplitRun | None = None
    group_calls: Counter[str] = field(default_factory=Counter)
    path_calls: Counter[str] = field(default_factory=Counter)
    # The landed fields the last partial assembly saw.
    assembled_over: frozenset[str] = frozenset()

    @classmethod
    def for_run(
        cls,
        ctx: PipelineContext,
        text_groups: Sequence[ExtractionGroup],
        chunks: int,
        run: SplitRun | None = None,
    ) -> GroupLanding:
        """The run's landing state; from here on no tab is final until every group landed."""
        ctx.tab_ledger.expect_groups()
        return cls(ctx, max(chunks, 1), _quiz_owners(text_groups), run)

    def _all_landed(self, group_id: str) -> bool:
        return self.group_calls[group_id] >= self.calls_per_group

    def _owners_landed(self, outcome: GroupOutcome) -> bool:
        if self.quiz_owners:
            return all(self._all_landed(owner) for owner in self.quiz_owners)
        return outcome.group.visual and self._all_landed(outcome.group.group_id)

    def _quiz_host_settled(self) -> bool:
        """A strip-only quiz (no quiz tab) shows on a sparse host: frames-done can no
        longer drop a deferred code tab that is that host. A deferred code requirement
        alone only adds a tab (no brief, never a sparse host) — unless the plan is full
        and that backfill trims a planned tab, which may be the host."""
        plan = self.ctx.plan_result
        if plan is None or _plans_quiz_tab(plan.tabs) or self.ctx.frames_done.is_set():
            return True
        full = len(plan.tabs) >= MAX_TABS  # no quiz tab here: every tab counts
        return not (plan.deferred_tabs or (plan.deferred_requirements and full))

    def _quiz_due(self, outcome: GroupOutcome) -> bool:
        return self._owners_landed(outcome) and self._quiz_host_settled()

    def _text_groups_done(self) -> SplitRun | None:
        """The split run once every text group landed on every chunk; else None."""
        run = self.run
        if run is None or not all(self._all_landed(g.group_id) for g in run.text_groups):
            return None
        return run

    def _text_done_calls(self) -> _TextDoneCalls:
        """The calls text-groups-done starts and has not started yet: the hero rewrite, and
        merge_rank on a chunked run."""
        run = self._text_groups_done()
        if run is None:
            return _TextDoneCalls()
        hero = settings.HERO_REWRITE_ENABLED and self.ctx.hero_task is None
        ranks = settings.MERGE_RANK_ENABLED and len(run.chunks) > 1
        ranks = ranks and self.ctx.merge_rank_task is None
        return _TextDoneCalls(run if hero else None, run if ranks else None)

    def landed(self) -> frozenset[str]:
        return frozenset(p for p, n in self.path_calls.items() if n >= self.calls_per_group)

    def events(
        self,
        outcome: GroupOutcome,
        outcomes: Sequence[GroupOutcome],
        fields: Sequence[FieldRequest],
    ) -> list[str]:
        """Count ``outcome`` in; start the quiz when its groups are in, the hero and
        merge_rank at text-groups-done; the tabs now ready.

        ``outcomes`` = every call landed so far (``outcome`` included);
        ``fields`` = what the run's tabs request now (frames-done may have
        re-pointed a tab). A quiz whose groups brought nothing usable is tried
        again as later groups land (else the late group makes the call). A
        call that completes no field (a long video's earlier chunks) changes
        no tab: nothing is merged or assembled for it unless the quiz, the
        hero or merge_rank is due.
        """
        self.group_calls[outcome.group.group_id] += 1
        self.path_calls.update(f.path for f in outcome.group.fields)
        landed = self.landed()
        grew = landed != self.assembled_over
        quiz_due = self.ctx.quiz_task is None and self._quiz_due(outcome)
        text_done = self._text_done_calls()
        if not grew and not quiz_due and not text_done:
            return []
        merged, _ = merge_groups(outcomes)
        if quiz_due:
            start_quiz_task(self.ctx, merged, self.run.chunks if self.run else ())
        text_done.start(self.ctx, merged, outcomes)
        if not grew:
            return []
        self.assembled_over = landed
        return _safe_ready_tab_events(self.ctx, fields, landed, merged)
