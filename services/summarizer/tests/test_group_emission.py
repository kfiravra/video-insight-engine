"""Per-group assembly + emission on the split path (pipeline-1min 3.6).

``GroupLanding`` drives what the split member streams after each landed call:
previews of the tabs whose requested fields all landed (non-final, links and
attachments stripped, at their plan-order position, never the overview, a
moment tab or the quiz); ``assembled_tab_events`` previews what the final
assembly adds or changes. The quiz start: ``test_group_emission_quiz.py``.
``assemble_response`` runs as production code except where a test needs a
canned response.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.pipeline_types import PlanResult
from src.services.pipeline.context import PipelineContext
from src.services.pipeline.extraction_groups import GroupOutcome, GroupStatus
from src.services.pipeline.field_groups import ExtractionGroup
from src.services.pipeline.field_requests import FieldRequest
from src.services.pipeline.phases import enrichment as enrichment_phase
from src.services.pipeline.phases import group_emission
from src.services.pipeline.phases.group_emission import (
    GroupLanding,
    assembled_tab_events,
    ready_tab_events,
)
from src.services.pipeline.pipeline_helpers import PipelineTimer

_TABS = [
    {"id": "overview", "label": "Overview", "component": "overview", "dataSource": "overview"},
    {"id": "concepts", "label": "Concepts", "component": "flash_deck", "dataSource": "learning.concepts"},
    {"id": "takeaways", "label": "Takeaways", "component": "checklist", "dataSource": "learning.takeaways"},
    {"id": "moments", "label": "Moments", "component": "moment_track", "dataSource": "learning.timestamps"},
    {"id": "quiz", "label": "Quiz", "component": "quiz_arena", "dataSource": "enrichment.quiz"},
]  # fmt: skip
_DATA: dict[str, list[Any]] = {
    "concepts": [
        {"name": f"concept {i}", "definition": f"what concept {i} means in practice"}
        for i in range(1, 5)
    ],
    "takeaways": ["ship small changes often", "measure before optimizing", "write the test first"],
    "timestamps": [
        {"time": f"{i}:00", "seconds": 60 * i, "label": f"moment {i}"} for i in (1, 2, 3)
    ],
    "keyPoints": ["a point the concepts tab can fall back to", "another point", "a third point"],
    "ingredients": [{"name": "eggs"}, {"name": "flour"}],
    "snippets": [
        {"filename": "a.py", "language": "python", "code": "x = 1", "explanation": "demo"}
    ],
}
_ASSEMBLY_LOGGER = "src.services.pipeline.assembly.core"


def _field(path: str, *tab_ids: str, visual: bool = False) -> FieldRequest:
    domain, name = path.split(".", 1)
    return FieldRequest(domain, name, "list", "planned", tab_ids, 10, 3, 50, visual)


_CONCEPTS = _field("learning.concepts", "concepts")
_TAKEAWAYS = _field("learning.takeaways", "takeaways")
_TIMESTAMPS = _field("learning.timestamps", "moments")
_FIELDS = (_CONCEPTS, _TAKEAWAYS, _TIMESTAMPS)


def _group(group_id: str, *fields: FieldRequest) -> ExtractionGroup:
    tab_ids = tuple(dict.fromkeys(t for f in fields for t in f.tab_ids))
    return ExtractionGroup(group_id, fields, group_id == "visual", 0, tab_ids)


def _outcome(group: ExtractionGroup, status: GroupStatus = "ok", chunk: int = 0) -> GroupOutcome:
    data: dict[str, Any] = {}
    for f in group.fields:
        data.setdefault(f.domain, {})[f.field] = _DATA.get(f.field, [])
    report = {"dropped": {}, "defaulted": 0}
    payload = data if status == "ok" else None
    return GroupOutcome(group, chunk, payload, report, status, 1, 0)  # type: ignore[arg-type]


def _ctx(tabs: list[dict] | None = None, tags: tuple[str, ...] = ("learning",)) -> PipelineContext:
    plan_tabs = _TABS if tabs is None else tabs
    ctx = PipelineContext("vsid", "yt", {}, MagicMock(), MagicMock(), PipelineTimer())
    ctx.plan_result = PlanResult.model_validate(
        {"contentTags": list(tags), "primaryTag": tags[0], "tabs": plan_tabs, "confidence": 0.9}
    )
    ctx.triage_dict = {"contentTags": list(tags), "primaryTag": tags[0], "tabs": plan_tabs}
    ctx.video_data = SimpleNamespace(  # type: ignore[assignment]
        title="A video", channel="A channel", duration=600, chapters=None
    )
    return ctx


def _payloads(events: list[str]) -> list[dict[str, Any]]:
    return [json.loads(event.removeprefix("data: ")) for event in events]


class _Run:
    """One split run's landing: feeds outcomes in order, keeps what each one streamed."""

    def __init__(self, ctx: PipelineContext, groups: list[ExtractionGroup], chunks: int = 1):
        self.ctx = ctx
        self.landing = GroupLanding.for_run(ctx, [g for g in groups if not g.visual], chunks)
        self.outcomes: list[GroupOutcome] = []

    def land(self, outcome: GroupOutcome, fields: tuple[FieldRequest, ...] = _FIELDS) -> list[dict]:
        self.outcomes.append(outcome)
        return _payloads(self.landing.events(outcome, self.outcomes, fields))


def _ids(payloads: list[dict]) -> list[str]:
    return [p["id"] for p in payloads]


@pytest.fixture
async def quiz_call():
    """The quiz LLM boundary; the quiz tasks a test started are cancelled and gathered."""
    with patch.object(enrichment_phase, "enrich_quiz", AsyncMock(return_value=None)) as mock:
        yield mock
        started = [t for t in asyncio.all_tasks() if t.get_name().startswith("quiz_")]
        for task in started:
            task.cancel()
        await asyncio.gather(*started, return_exceptions=True)


# ─── Which tabs stream ───


class TestReadyTabs:
    async def test_should_emit_a_tab_only_when_all_its_fields_landed(self, quiz_call) -> None:
        text1, text2 = _group("text1", _CONCEPTS), _group("text2", _TAKEAWAYS)
        run = _Run(_ctx(), [text1, text2])

        first = run.land(_outcome(text1))

        assert "takeaways" not in _ids(first)

    async def test_should_emit_the_tab_once_its_last_field_lands(self, quiz_call) -> None:
        text1, text2 = _group("text1", _CONCEPTS), _group("text2", _TAKEAWAYS)
        run = _Run(_ctx(), [text1, text2])
        run.land(_outcome(text1))

        second = run.land(_outcome(text2))

        assert "takeaways" in _ids(second)

    async def test_should_wait_for_every_field_a_tab_reads(self, quiz_call) -> None:
        fallback = _field("learning.keyPoints", "concepts")
        fields = (_CONCEPTS, fallback, _TAKEAWAYS, _TIMESTAMPS)
        text1, text2 = _group("text1", _CONCEPTS), _group("text2", fallback)
        run = _Run(_ctx(), [text1, text2])

        first = run.land(_outcome(text1), fields)

        assert "concepts" not in _ids(first)

    async def test_should_emit_per_group_tabs_non_final(self, quiz_call) -> None:
        run = _Run(_ctx(), [_group("text1", *_FIELDS)])

        sent = run.land(_outcome(_group("text1", *_FIELDS)))

        assert {p["final"] for p in sent} == {False}

    async def test_should_never_emit_the_overview_a_moment_tab_or_the_quiz(self, quiz_call) -> None:
        text1 = _group("text1", *_FIELDS)
        run = _Run(_ctx(), [text1])

        sent = run.land(_outcome(text1))

        assert sorted(_ids(sent)) == ["concepts", "takeaways"]

    async def test_should_position_a_tab_by_its_plan_order(self, quiz_call) -> None:
        text1 = _group("text1", *_FIELDS)
        run = _Run(_ctx(), [text1])

        sent = run.land(_outcome(text1))

        assert [(p["id"], p["position"]) for p in sent] == [("concepts", 1), ("takeaways", 2)]

    async def test_should_keep_a_tabs_position_when_an_earlier_planned_tab_lands_later(
        self, quiz_call
    ) -> None:
        text1, text2 = _group("text1", _TAKEAWAYS), _group("text2", _CONCEPTS)
        run = _Run(_ctx(), [text1, text2])

        first = run.land(_outcome(text1))

        assert [(p["id"], p["position"]) for p in first] == [("takeaways", 2)]

    async def test_should_not_resend_a_tab_when_an_earlier_planned_tab_lands(
        self, quiz_call
    ) -> None:
        text1, text2 = _group("text1", _TAKEAWAYS), _group("text2", _CONCEPTS)
        run = _Run(_ctx(), [text1, text2])
        run.land(_outcome(text1))

        second = run.land(_outcome(text2))

        assert _ids(second) == ["concepts"]

    async def test_should_not_resend_a_tab_the_client_holds_unchanged(self, quiz_call) -> None:
        text1, text2 = _group("text1", _CONCEPTS, _TAKEAWAYS), _group("text2", _TIMESTAMPS)
        run = _Run(_ctx(), [text1, text2])
        run.land(_outcome(text1))

        assert run.land(_outcome(text2)) == []

    async def test_should_count_a_failed_groups_fields_as_landed(self, quiz_call) -> None:
        text1, text2 = _group("text1", _CONCEPTS), _group("text2", _TAKEAWAYS)
        run = _Run(_ctx(), [text1, text2])
        run.land(_outcome(text1))

        run.land(_outcome(text2, status="failed"))

        assert run.landing.landed() == {_CONCEPTS.path, _TAKEAWAYS.path}

    async def test_should_wait_for_every_chunks_call_of_a_group(self, quiz_call) -> None:
        text1 = _group("text1", *_FIELDS)
        run = _Run(_ctx(), [text1], chunks=2)

        first = run.land(_outcome(text1, chunk=0))

        assert first == []

    async def test_should_emit_once_the_last_chunk_landed(self, quiz_call) -> None:
        text1 = _group("text1", *_FIELDS)
        run = _Run(_ctx(), [text1], chunks=2)
        run.land(_outcome(text1, chunk=0))

        second = run.land(_outcome(text1, chunk=1))

        assert sorted(_ids(second)) == ["concepts", "takeaways"]

    async def test_should_mark_the_ledger_per_group_for_the_run(self, quiz_call) -> None:
        ctx = _ctx()

        _Run(ctx, [_group("text1", *_FIELDS)])

        assert ctx.tab_ledger.groups_pending is True

    async def test_should_list_a_per_group_tab_the_final_response_drops_as_withdrawn(
        self, quiz_call
    ) -> None:
        ctx = _ctx()
        run = _Run(ctx, [_group("text1", *_FIELDS)])
        run.land(_outcome(_group("text1", *_FIELDS)))

        withdrawn = ctx.tab_ledger.withdrawn([{"id": "overview"}, {"id": "concepts"}])

        assert withdrawn == ["takeaways"]


# ─── What a streamed tab carries ───


def _canned(*tabs: dict) -> Any:
    return MagicMock(return_value={"meta": {}, "tabs": list(tabs), "dropped": []})


_LINKED = {
    "id": "concepts",
    "label": "Concepts",
    "component": "flash_deck",
    "props": {"cards": []},
    "crossTabLinks": [{"targetTab": "takeaways", "label": "Takeaways"}],
    "attachments": [{"slot": "bottom", "component": "tip_callout", "props": {}}],
}


class TestStreamedPayload:
    def test_should_strip_links_and_attachments(self) -> None:
        ctx = _ctx()

        with patch.object(group_emission, "assemble_response", _canned(_LINKED)):
            [sent] = _payloads(ready_tab_events(ctx, _FIELDS, frozenset({_CONCEPTS.path}), {}))

        assert {"crossTabLinks", "attachments"} & set(sent) == set()

    def test_should_leave_the_assembled_tab_untouched(self) -> None:
        tab = dict(_LINKED)

        with patch.object(group_emission, "assemble_response", _canned(tab)):
            ready_tab_events(_ctx(), _FIELDS, frozenset({_CONCEPTS.path}), {})

        assert set(tab) == set(_LINKED)

    def test_should_assemble_over_a_deep_copy_of_the_merged_extraction(self) -> None:
        merged = {"learning": {"concepts": [{"name": "agent"}]}}

        def thumbnails(**kwargs: Any) -> dict:
            kwargs["extraction"]["learning"]["concepts"][0]["thumbnailUrl"] = "frame.jpg"
            return {"meta": {}, "tabs": [], "dropped": []}

        with patch.object(group_emission, "assemble_response", thumbnails):
            ready_tab_events(_ctx(), _FIELDS, frozenset({_CONCEPTS.path}), merged)

        assert merged == {"learning": {"concepts": [{"name": "agent"}]}}


class TestPartialAssemblyFailure:
    async def test_should_stream_nothing_when_the_partial_assembly_fails(self, quiz_call) -> None:
        text1 = _group("text1", *_FIELDS)
        run = _Run(_ctx(), [text1])

        with patch.object(group_emission, "assemble_response", MagicMock(side_effect=ValueError)):
            sent = run.land(_outcome(text1))

        assert sent == []

    async def test_should_log_the_failure_with_its_traceback(
        self, quiz_call, caplog: pytest.LogCaptureFixture
    ) -> None:
        text1 = _group("text1", *_FIELDS)
        run = _Run(_ctx(), [text1])

        with patch.object(group_emission, "assemble_response", MagicMock(side_effect=ValueError)):
            run.land(_outcome(text1))

        assert any(r.exc_info for r in caplog.records if r.name == group_emission.__name__)


class TestPartialAssemblyLogs:
    def test_should_keep_the_pending_fields_warnings_out_of_the_logs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)

        ready_tab_events(_ctx(), _FIELDS, frozenset({_CONCEPTS.path}), {"learning": {}})

        assert [r for r in caplog.records if r.name == _ASSEMBLY_LOGGER] == []

    def test_should_log_assembly_warnings_again_after_a_partial_assembly(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        ready_tab_events(_ctx(), _FIELDS, frozenset({_CONCEPTS.path}), {"learning": {}})

        logging.getLogger(_ASSEMBLY_LOGGER).warning("a final assembly's warning")

        assert "a final assembly's warning" in caplog.messages

    def test_should_keep_a_concurrent_final_assemblys_warnings(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        inside, release = threading.Event(), threading.Event()

        def blocking(**_kwargs: Any) -> dict:
            inside.set()
            release.wait(5)
            return {"meta": {}, "tabs": [], "dropped": []}

        with patch.object(group_emission, "assemble_response", blocking):
            args = (_ctx(), _FIELDS, frozenset({_CONCEPTS.path}), {})
            partial = threading.Thread(target=ready_tab_events, args=args)
            partial.start()
            inside.wait(5)
            logging.getLogger(_ASSEMBLY_LOGGER).warning("a final assembly's warning")
            release.set()
            partial.join(5)

        assert (inside.is_set(), "a final assembly's warning" in caplog.messages) == (True, True)


# ─── After the final assembly ───


def _assembled(*tabs: dict) -> PipelineContext:
    ctx = _ctx()
    ctx.tab_ledger.expect_groups()
    ctx.assembled_tabs = list(tabs)
    return ctx


_OVERVIEW_TAB = {"id": "overview", "label": "Overview", "component": "overview", "props": {}}
_MOMENT_TAB = {"id": "moments", "label": "Moments", "component": "moment_track", "props": {}}
_QUIZ_TAB = {"id": "quiz", "label": "Quiz", "component": "quiz_arena", "props": {}}


class TestAssembledTabEvents:
    def test_should_preview_the_overview(self) -> None:
        sent = _payloads(assembled_tab_events(_assembled(_OVERVIEW_TAB, _LINKED)))

        assert ("overview", 0, False) in [(p["id"], p["position"], p["final"]) for p in sent]

    def test_should_never_preview_a_moment_tab_or_the_quiz(self) -> None:
        ctx = _assembled(_OVERVIEW_TAB, _MOMENT_TAB, _QUIZ_TAB)

        assert _ids(_payloads(assembled_tab_events(ctx))) == ["overview"]

    def test_should_strip_links_and_attachments(self) -> None:
        [sent] = _payloads(assembled_tab_events(_assembled(_LINKED)))

        assert {"crossTabLinks", "attachments"} & set(sent) == set()

    def test_should_skip_a_tab_whose_preview_the_client_holds(self) -> None:
        ctx = _assembled(_OVERVIEW_TAB, _LINKED)
        preview = {k: v for k, v in _LINKED.items() if k not in ("crossTabLinks", "attachments")}
        ctx.tab_ledger.event(preview, 3, preview=True)

        assert _ids(_payloads(assembled_tab_events(ctx))) == ["overview"]

    def test_should_resend_a_tab_the_last_groups_changed(self) -> None:
        ctx = _assembled(_LINKED)
        ctx.tab_ledger.event({"id": "concepts", "props": {"cards": ["stale"]}}, 1, preview=True)

        [sent] = _payloads(assembled_tab_events(ctx))

        assert (sent["props"], sent["position"]) == ({"cards": []}, 0)
