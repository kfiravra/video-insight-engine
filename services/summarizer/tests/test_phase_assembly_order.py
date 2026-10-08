"""Assembly ordering after the phase-1 review (G03-1, G17-1/G19-1, G19-2, G18-3).

* the description analysis is awaited with a cap at assembly (no longer a
  phase-2 member) and its SSE event goes out before the tabs;
* the quiz never blocks tabs: it runs in the late group and its tab joins last,
  with every changed host re-sent;
* a failed memory makes synthesis run first, so the Key Info fallback fires;
* ``pipeline.assembly`` counts drops without the evidence skips it lists.

The LLM boundaries (``synthesize``, the quiz phase) and the frame fill are
patched; ``assemble_response`` runs as production code.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.memory_types import MemoryResult
from src.models.pipeline_types import SynthesisResult
from src.services.pipeline.assembly.core import REQUIREMENT_EVIDENCE_FALSE
from src.services.pipeline.phases import assembly as phase
from src.services.pipeline.phases import synthesis as synthesis_phase
from src.services.pipeline.phases.late_quiz import (
    carry_moment_props,
    positions_to_resend,
    withdrawn_tab_ids,
)
from src.services.pipeline.pipeline_helpers import sse_event
from src.services.video.description_analyzer import DescriptionAnalysis, Resource
from tests.test_phase_assembly_cache import _build_ctx

_QUESTIONS = [
    {"question": f"Q{i}?", "options": ["a", "b", "c", "d"], "correctIndex": 1, "explanation": "b"}
    for i in range(3)
]
_MEMORY = MemoryResult(tldr="Memory tldr.", takeaways=["m1", "m2", "m3"])
_SYNTHESIS = SynthesisResult.model_validate(
    {"masterSummary": "The full summary.", "seoDescription": "SEO."}
)


def _snippet(code: str) -> dict:
    return {"filename": f"{code}.py", "language": "python", "code": code, "explanation": "demo"}


def _tech_ctx() -> Any:
    ctx = _build_ctx()
    ctx.memory = _MEMORY
    ctx.llm_service = MagicMock()
    ctx.enrichment_data = None
    ctx.triage.primary_tag = "tech"
    ctx.triage_dict = {
        "contentTags": ["tech"],
        "primaryTag": "tech",
        "tabs": [
            {"id": "code", "label": "Code", "component": "code_playground",
             "dataSource": "tech.snippets", "goal": "Run it"},
            {"id": "quiz", "label": "Quiz", "component": "quiz_arena",
             "dataSource": "enrichment.quiz", "goal": "Check it"},
        ],
    }  # fmt: skip
    ctx.extraction_data = {"tech": {"snippets": [_snippet("x = 1"), _snippet("y = 2")]}}
    return ctx


def _events(chunks: list[str]) -> list[tuple[str, dict]]:
    parsed = []
    for chunk in chunks:
        body = chunk.strip().removeprefix("data: ")
        if body != "[DONE]":
            payload = json.loads(body)
            parsed.append((payload.pop("event"), payload))
    return parsed


async def _quiz_phase(ctx: Any):
    ctx.enrichment_data = {"quiz": _QUESTIONS}
    yield sse_event("enrichment_complete", ctx.enrichment_data)


async def _run(ctx: Any, synthesis: SynthesisResult = _SYNTHESIS, quiz: bool = False) -> list:
    with (
        patch.object(phase, "needs_quiz", lambda *_a: quiz),
        patch.object(phase, "run_phase_enrichment", _quiz_phase),
        patch.object(phase, "fill_moment_frames", AsyncMock(return_value=0)),
        patch.object(synthesis_phase, "synthesize", AsyncMock(return_value=synthesis)),
        patch.object(phase.settings, "REDIS_ENABLED", False),
        patch.object(phase.settings, "QDRANT_ENABLED", False),
    ):
        chunks = [chunk async for chunk in phase.run_phase_assembly(ctx)]
    return _events(chunks)


def _tab_ids(events: list[tuple[str, dict]]) -> list[str]:
    return [payload["id"] for name, payload in events if name == "tab_ready"]


def _saved(ctx: Any) -> dict:
    return ctx.repository.save_structured_result.call_args.args[1]


class TestLateQuiz:
    async def test_should_stream_the_other_tabs_before_the_quiz_lands(self) -> None:
        events = await _run(_tech_ctx(), quiz=True)

        quiz_at = [name for name, _ in events].index("enrichment_complete")
        assert {"overview", "code"} <= set(_tab_ids(events[:quiz_at]))
        assert "quiz" not in _tab_ids(events[:quiz_at])

    async def test_should_add_the_quiz_tab_last_once_it_lands(self) -> None:
        ctx = _tech_ctx()

        events = await _run(ctx, quiz=True)

        quiz_tab = next(p for n, p in events if n == "tab_ready" and p["id"] == "quiz")
        saved_tabs = _saved(ctx)["tabs"]
        assert (saved_tabs[-1]["id"], quiz_tab["position"]) == ("quiz", len(saved_tabs) - 1)

    async def test_should_re_send_a_host_that_took_the_quick_quiz(self) -> None:
        events = await _run(_tech_ctx(), quiz=True)

        quiz_at = [name for name, _ in events].index("enrichment_complete")
        resent = [p for n, p in events[quiz_at:] if n == "tab_ready" and p["id"] == "code"]
        assert [a["component"] for a in resent[-1]["attachments"]] == ["quick_quiz"]

    async def test_should_not_count_the_quiz_as_dropped_when_it_landed(self) -> None:
        ctx = _tech_ctx()

        await _run(ctx, quiz=True)

        dropped = _saved(ctx)["pipeline"]["assembly"]["droppedTabs"]
        assert all(entry["id"] != "quiz" for entry in dropped)

    async def test_should_report_the_final_tab_count_on_complete(self) -> None:
        ctx = _tech_ctx()

        events = await _run(ctx, quiz=True)

        complete = next(p for n, p in events if n == "complete")
        assert complete["tabCount"] == len(_saved(ctx)["tabs"])


class TestHeroFallback:
    """Regression: a failed memory left assembly without synthesis → no Key Info tab."""

    @staticmethod
    def _ctx() -> Any:
        ctx = _build_ctx()
        ctx.llm_service = MagicMock()
        ctx.triage.primary_tag = "learning"
        ctx.triage_dict = {"contentTags": ["learning"], "primaryTag": "learning", "tabs": []}
        return ctx

    async def test_should_add_key_info_from_synthesis_when_memory_failed(self) -> None:
        ctx = self._ctx()
        synthesis = SynthesisResult.model_validate(
            {"masterSummary": "S.", "tldr": "T.", "keyTakeaways": ["one", "two", "three"]}
        )

        await _run(ctx, synthesis=synthesis)

        assert "key_info" in [t["id"] for t in _saved(ctx)["tabs"]]

    async def test_should_send_the_hero_before_the_first_tab_when_memory_failed(self) -> None:
        events = await _run(self._ctx())

        names = [name for name, _ in events]
        assert names.index("synthesis_complete") < names.index("tab_ready")


class TestDescriptionAnalysis:
    @staticmethod
    def _with_description(task: asyncio.Future[None], analysis: object) -> Any:
        ctx = _tech_ctx()
        ctx.description_task = task
        ctx.description_analysis = analysis
        return ctx

    async def test_should_emit_the_description_analysis_before_the_tabs(self) -> None:
        done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        done.set_result(None)
        analysis = DescriptionAnalysis(resources=[Resource(name="Docs", url="https://x.dev")])

        events = await _run(self._with_description(done, analysis))

        names = [name for name, _ in events]
        assert names.index("description_analysis") < names.index("tab_ready")

    async def test_should_not_hold_the_tabs_for_a_stalled_description_analysis(self) -> None:
        stalled: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        ctx = self._with_description(stalled, None)

        with patch.object(phase, "DESCRIPTION_WAIT_SECONDS", 0.01):
            events = await asyncio.wait_for(_run(ctx), timeout=2.0)

        assert "tab_ready" in [name for name, _ in events] and not stalled.cancelled()


class TestDropAccounting:
    def test_should_list_evidence_skips_without_counting_them(self) -> None:
        skip = {"id": "", "component": "checklist", "reason": REQUIREMENT_EVIDENCE_FALSE}
        drop = {"id": "tips", "component": "info_grid", "reason": "assembler_returned_none"}
        ctx = SimpleNamespace(
            triage=SimpleNamespace(tabs=[{"id": "tips"}]),
            plan_result=SimpleNamespace(dropped_tabs=[], plan_fallback=False),
            video_summary_id="vsid",
        )

        accounting = phase._drop_accounting(ctx, {"tabs": [], "dropped": [drop, skip]})  # type: ignore[arg-type]

        assert (accounting["tabsDropped"], accounting["droppedTabs"]) == (1, [drop, skip])


class TestLateQuizHelpers:
    def test_should_resend_new_moved_and_changed_tabs_only(self) -> None:
        before = [{"id": "a", "v": 1}, {"id": "b", "v": 1}, {"id": "c", "v": 1}]
        after = [{"id": "a", "v": 1}, {"id": "c", "v": 1}, {"id": "b", "v": 2}, {"id": "q"}]

        assert positions_to_resend(before, after) == [1, 2, 3]

    def test_should_keep_the_moment_fill_frames(self) -> None:
        filled = {"items": [{"thumbnailUrl": "https://f/1.jpg"}]}
        before = [{"id": "m", "component": "moment_track", "props": filled}]
        after = [{"id": "m", "component": "moment_track", "props": {"items": [{}]}}]

        carry_moment_props(before, after)

        assert after[0]["props"] is filled

    def test_should_name_streamed_tabs_the_final_response_dropped(self) -> None:
        before = [{"id": "overview"}, {"id": "key_info"}]

        assert withdrawn_tab_ids(before, [{"id": "overview"}, {"id": "quiz"}]) == ["key_info"]
