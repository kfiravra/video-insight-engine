"""The hero rewrite wired into the run (output-quality 2.2, D1).

With ``HERO_REWRITE_ENABLED`` the synthesis slot of assembly's late group awaits the
hero the split extraction started at text-groups-done (or makes the call itself on
``EXTRACTION_PARALLEL=false``) instead of calling synthesis; the hero's meta replaces
memory's, the 2.0 scrub runs over it, and once the tabs are final the stored doc and
the last ``synthesis_complete`` carry the code-counted ``readingMinutes``. A hero
that gave nothing, or the flag off, runs today's synthesis. The hero LLM call
(``hero_rewrite_for_run``), the synthesis call and the frame fill are patched.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import settings
from src.models.hero_types import HeroRewriteResult
from src.models.memory_types import MemoryResult
from src.models.pipeline_types import SynthesisResult
from src.models.sse_events import SSEEventValidationError, validate_sse_event
from src.routes.cached_response import resolve_synthesis
from src.services.pipeline.hero_rewrite import hero_meta
from src.services.pipeline.phases import assembly as phase
from src.services.pipeline.phases import split_extraction
from src.services.pipeline.phases import synthesis as synthesis_phase
from src.services.pipeline.phases.group_emission import GroupLanding
from src.services.pipeline.synthesis import build_synthesis_dict
from tests import test_split_extraction as split_member
from tests.test_group_emission import _CONCEPTS, _TABS, _TAKEAWAYS, _group, _outcome
from tests.test_group_emission import _ctx as _landing_ctx
from tests.test_phase_assembly_cache import _build_ctx

_MEMORY = MemoryResult(tldr="Memory tldr.", takeaways=["m1", "m2", "m3"])
_SYNTHESIS = SynthesisResult.model_validate(
    {"masterSummary": "The full summary.", "seoDescription": "SEO."}
)
_HERO = HeroRewriteResult.model_validate(
    {
        "tldr": "Hand-written code no longer pays.",
        "brief": (
            "It opens on a bet. The Visual Moments tab shows every frame. It ends on a promise."
        ),
        "keyTakeaways": [
            {"text": "Agents write the code now.", "timestamp": 60},
            {"text": "Review is the job.", "timestamp": 3725},
        ],
        "worthItFor": "Leads deciding how far to let agents in.",
        "skipIf": "You want a how-to: no code is written on stage.",
        "seoDescription": "A keynote on agents writing the code.",
    }
)
_SCRUBBED_BRIEF = "It opens on a bet. It ends on a promise."
_HERO_KEYS = {"brief", "worthItFor", "skipIf", "readingMinutes"}
# 1 000 words of tab content + 2 labels + ≈ 42 hero words → 1 044 / 238 wpm → 5 minutes.
_CONTENT_WORDS = 1000


def _tabs() -> list[dict]:
    return [
        {"id": "overview", "component": "overview", "label": "Overview", "props": {"data": {}}},
        {"id": "moments", "component": "moment_track", "label": "Moments", "props": {"items": []}},
        {
            "id": "facts",
            "component": "info_grid",
            "label": "Facts",
            "props": {"items": [{"text": "word " * _CONTENT_WORDS}]},
        },
    ]


def _ctx(**overrides: Any) -> Any:
    ctx = _build_ctx()
    ctx.memory = _MEMORY
    ctx.video_memory = "<video_memory></video_memory>"
    ctx.synthesis_dict = {}
    ctx.triage.primary_tag = "learning"
    ctx.llm_service = MagicMock()
    ctx.extraction_data = {"learning": {"keyIdeas": [{"claim": "Agents write it", "seconds": 60}]}}
    # The plan named a filmstrip assembly never shipped: the hero may not name it.
    gallery = {"id": "gallery", "label": "Visual Moments", "component": "video_filmstrip"}
    ctx.triage_dict = {"tabs": [*_tabs(), gallery]}
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


def _events(chunks: list[str]) -> list[tuple[str, dict]]:
    parsed = []
    for chunk in chunks:
        body = chunk.strip().removeprefix("data: ")
        if body != "[DONE]":
            payload = json.loads(body)
            parsed.append((payload.pop("event"), payload))
    return parsed


class _Calls:
    """The run's two hero writers at their boundaries."""

    def __init__(self, hero: HeroRewriteResult | None) -> None:
        self.hero = AsyncMock(return_value=hero)
        self.synthesize = AsyncMock(return_value=_SYNTHESIS)


def _assembler() -> MagicMock:
    return MagicMock(side_effect=lambda **_kw: {"tabs": _tabs(), "meta": {}})


async def _run(
    ctx: Any, calls: _Calls, assemble: MagicMock | None = None
) -> list[tuple[str, dict]]:
    with (
        patch.object(phase, "send_video_status_background", MagicMock()),
        patch.object(phase, "assemble_response", assemble or _assembler()),
        patch.object(phase, "fill_moment_frames", AsyncMock(return_value=0)),
        patch.object(synthesis_phase, "hero_rewrite_for_run", calls.hero),
        patch.object(synthesis_phase, "synthesize", calls.synthesize),
        patch.object(phase.settings, "REDIS_ENABLED", False),
        patch.object(phase.settings, "QDRANT_ENABLED", False),
    ):
        return _events([chunk async for chunk in phase.run_phase_assembly(ctx)])


def _saved_meta(ctx: Any) -> dict:
    return ctx.repository.save_structured_result.call_args.args[1]["meta"]


def _supersets(events: list[tuple[str, dict]]) -> list[dict]:
    return [payload for name, payload in events if name == "synthesis_complete"]


@pytest.fixture
def hero_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "HERO_REWRITE_ENABLED", True)


@pytest.fixture
def hero_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "HERO_REWRITE_ENABLED", False)


# ─── The late group with the hero ───


@pytest.mark.usefixtures("hero_on")
class TestLateGroupWithTheHero:
    async def test_should_not_call_synthesis_when_the_hero_wrote(self) -> None:
        calls = _Calls(_HERO)

        await _run(_ctx(), calls)

        assert (calls.hero.await_count, calls.synthesize.await_count) == (1, 0)

    async def test_should_call_the_hero_over_the_whole_extraction_without_a_started_task(
        self,
    ) -> None:
        ctx, calls = _ctx(), _Calls(_HERO)

        await _run(ctx, calls)

        assert calls.hero.call_args.args == (ctx, ctx.extraction_data, None)

    async def test_should_await_the_started_hero_task_instead_of_calling(self) -> None:
        calls = _Calls(None)
        ctx = _ctx(hero_task=asyncio.create_task(AsyncMock(return_value=_HERO)()))

        await _run(ctx, calls)

        assert (calls.hero.await_count, _saved_meta(ctx)["skipIf"]) == (0, _HERO.skip_if)

    async def test_should_store_the_brief_as_the_master_summary(self) -> None:
        ctx = _ctx()

        await _run(ctx, _Calls(_HERO))

        meta = _saved_meta(ctx)
        assert meta["masterSummary"] == meta["brief"] == _SCRUBBED_BRIEF

    async def test_should_store_each_takeaway_as_a_string_closing_with_its_marker(self) -> None:
        ctx = _ctx()

        await _run(ctx, _Calls(_HERO))

        assert _saved_meta(ctx)["keyTakeaways"] == [
            "Agents write the code now. [1:00]",
            "Review is the job. [1:02:05]",
        ]

    async def test_should_store_both_lines(self) -> None:
        ctx = _ctx()

        await _run(ctx, _Calls(_HERO))

        meta = _saved_meta(ctx)
        assert (meta["worthItFor"], meta["skipIf"]) == (_HERO.worth_it_for, _HERO.skip_if)

    async def test_should_store_the_reading_minutes_counted_over_the_shipped_page(self) -> None:
        ctx = _ctx()

        await _run(ctx, _Calls(_HERO))

        assert _saved_meta(ctx)["readingMinutes"] == 5

    async def test_should_send_the_reading_minutes_on_the_last_superset(self) -> None:
        events = await _run(_ctx(), _Calls(_HERO))

        supersets = _supersets(events)
        assert ("readingMinutes" in supersets[0], supersets[-1]["readingMinutes"]) == (False, 5)

    async def test_should_send_the_scrubbed_brief_on_the_last_superset(self) -> None:
        events = await _run(_ctx(), _Calls(_HERO))

        assert _supersets(events)[-1]["brief"] == _SCRUBBED_BRIEF

    async def test_should_patch_the_overview_with_the_brief(self) -> None:
        events = await _run(_ctx(), _Calls(_HERO))

        overviews = [p for name, p in events if name == "tab_ready" and p["id"] == "overview"]
        assert overviews[-1]["props"]["data"]["masterSummary"] == _SCRUBBED_BRIEF

    async def test_should_emit_no_event_the_synthesis_path_does_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        hero_events = await _run(_ctx(), _Calls(_HERO))
        monkeypatch.setattr(settings, "HERO_REWRITE_ENABLED", False)

        synthesis_events = await _run(_ctx(), _Calls(_HERO))

        assert {name for name, _ in hero_events} == {name for name, _ in synthesis_events}


@pytest.mark.usefixtures("hero_on")
class TestHeroFirst:
    """Memory left the hero empty: the synthesis slot runs before assembly."""

    async def test_should_assemble_with_the_heros_tldr(self) -> None:
        assemble = _assembler()

        await _run(_ctx(memory=None), _Calls(_HERO), assemble)

        assert assemble.call_args_list[0].kwargs["synthesis"]["tldr"] == _HERO.tldr

    async def test_should_carry_the_heros_own_keys_into_the_assembled_meta(self) -> None:
        ctx = _ctx(memory=None)

        await _run(ctx, _Calls(_HERO))

        assert _HERO_KEYS <= set(_saved_meta(ctx))


# ─── The synthesis path ───


class TestLateGroupWithoutTheHero:
    @pytest.mark.usefixtures("hero_off")
    async def test_should_call_synthesis_and_never_the_hero_when_the_flag_is_off(self) -> None:
        calls = _Calls(_HERO)

        await _run(_ctx(), calls)

        assert (calls.hero.await_count, calls.synthesize.await_count) == (0, 1)

    @pytest.mark.usefixtures("hero_off")
    async def test_should_store_no_hero_field_when_the_flag_is_off(self) -> None:
        ctx = _ctx()

        await _run(ctx, _Calls(_HERO))

        assert not _HERO_KEYS & set(_saved_meta(ctx))

    @pytest.mark.usefixtures("hero_on")
    async def test_should_fall_back_to_synthesis_when_the_hero_gave_none(self) -> None:
        ctx, calls = _ctx(), _Calls(None)

        await _run(ctx, calls)

        meta = _saved_meta(ctx)
        assert (calls.synthesize.await_count, meta["masterSummary"], "brief" in meta) == (
            1,
            "The full summary.",
            False,
        )


# ─── Text-groups-done starts the hero ───


def _split_run(*groups: Any, chunks: int = 1) -> Any:
    return SimpleNamespace(text_groups=groups, chunks=tuple(range(chunks)), prompt=None)


class _Landing:
    """A split run's landing with the hero call patched; started hero tasks are gathered."""

    def __init__(self, run: Any, chunks: int = 1) -> None:
        self.ctx = _landing_ctx([t for t in _TABS if t["id"] != "quiz"])
        self.run = run
        self.landing = GroupLanding.for_run(self.ctx, run.text_groups, chunks, run)
        self.outcomes: list[Any] = []

    def land(self, outcome: Any) -> None:
        self.outcomes.append(outcome)
        self.landing.events(outcome, self.outcomes, (_CONCEPTS, _TAKEAWAYS))


@pytest.fixture
async def hero_call() -> Any:
    with patch.object(synthesis_phase, "hero_rewrite_for_run", AsyncMock(return_value=_HERO)) as m:
        yield m
        started = [t for t in asyncio.all_tasks() if t.get_name().startswith("hero_")]
        await asyncio.gather(*started, return_exceptions=True)


@pytest.mark.usefixtures("hero_on")
class TestHeroStart:
    async def test_should_wait_until_every_text_group_landed_on_every_chunk(
        self, hero_call: AsyncMock
    ) -> None:
        text1, text2 = _group("text1", _CONCEPTS), _group("text2", _TAKEAWAYS)
        split = _Landing(_split_run(text1, text2, chunks=2), chunks=2)
        for outcome in (
            _outcome(text1, chunk=0),
            _outcome(text2, chunk=0),
            _outcome(text1, chunk=1),
        ):
            split.land(outcome)
        before = split.ctx.hero_task

        split.land(_outcome(text2, chunk=1))

        assert (before, split.ctx.hero_task is not None) == (None, True)

    async def test_should_hand_the_hero_the_merged_extraction_and_the_run(
        self, hero_call: AsyncMock
    ) -> None:
        text1, text2 = _group("text1", _CONCEPTS), _group("text2", _TAKEAWAYS)
        split = _Landing(_split_run(text1, text2))
        split.land(_outcome(text1))
        split.land(_outcome(text2))
        assert split.ctx.hero_task is not None

        await split.ctx.hero_task

        _ctx_arg, merged, run = hero_call.call_args.args
        assert (set(merged["learning"]), run) == ({"concepts", "takeaways"}, split.run)

    async def test_should_start_the_hero_once(self, hero_call: AsyncMock) -> None:
        text1 = _group("text1", _CONCEPTS, _TAKEAWAYS)
        split = _Landing(_split_run(text1, chunks=2), chunks=2)
        split.land(_outcome(text1, chunk=0))
        split.land(_outcome(text1, chunk=1))
        first = split.ctx.hero_task

        split.land(_outcome(text1, chunk=1))

        assert split.ctx.hero_task is first

    async def test_should_not_start_the_hero_without_the_split_run(
        self, hero_call: AsyncMock
    ) -> None:
        text1 = _group("text1", _CONCEPTS, _TAKEAWAYS)
        ctx = _landing_ctx([t for t in _TABS if t["id"] != "quiz"])
        landing = GroupLanding.for_run(ctx, [text1], 1)

        landing.events(_outcome(text1), [_outcome(text1)], (_CONCEPTS, _TAKEAWAYS))

        assert ctx.hero_task is None


@pytest.mark.usefixtures("hero_off")
async def test_should_not_start_the_hero_when_the_flag_is_off(hero_call: AsyncMock) -> None:
    text1 = _group("text1", _CONCEPTS, _TAKEAWAYS)
    split = _Landing(_split_run(text1))

    split.land(_outcome(text1))

    assert split.ctx.hero_task is None


# ─── A started hero nobody will await stops ───


async def _forever(*_args: object, **_kwargs: object) -> None:
    await asyncio.sleep(3600)


@pytest.mark.usefixtures("hero_on")
async def test_should_cancel_a_started_hero_when_the_split_member_raises() -> None:
    tabs = [t for t in split_member._LEARNING_TABS if t["component"] != "moment_track"]
    ctx = split_member._done(split_member._ctx(["learning"], tabs))

    with (
        patch.object(synthesis_phase, "hero_rewrite_for_run", AsyncMock(side_effect=_forever)),
        patch.object(split_extraction, "_complete", AsyncMock(side_effect=RuntimeError)),
    ):
        member = asyncio.create_task(split_member._drain(ctx, split_member._Groups()))
        await asyncio.gather(member, return_exceptions=True)
        assert ctx.hero_task is not None
        await asyncio.gather(ctx.hero_task, return_exceptions=True)

    assert ctx.hero_task.cancelled()


# ─── The contract ───


class TestContract:
    def test_hero_superset_should_pass_the_sse_contract(self) -> None:
        event = synthesis_phase.synthesis_event(hero_meta(_HERO, minutes=3))

        assert validate_sse_event("synthesis_complete", event) is not None

    def test_should_refuse_a_reading_time_under_a_minute(self) -> None:
        event = {**synthesis_phase.synthesis_event(hero_meta(_HERO)), "readingMinutes": 0}

        with pytest.raises(SSEEventValidationError):
            validate_sse_event("synthesis_complete", event)

    def test_should_leave_reading_minutes_out_until_it_is_counted(self) -> None:
        assert "readingMinutes" not in synthesis_phase.synthesis_event(hero_meta(_HERO))

    def test_should_send_only_the_four_fields_on_the_synthesis_path(self) -> None:
        event = synthesis_phase.synthesis_event(build_synthesis_dict(_MEMORY, _SYNTHESIS))

        assert set(event) == {"tldr", "keyTakeaways", "masterSummary", "seoDescription"}

    def test_should_replace_a_marker_the_model_wrote_with_the_cited_stamp(self) -> None:
        result = _HERO.model_copy(
            update={
                "key_takeaways": [_HERO.key_takeaways[0].model_copy(update={"text": "A [0:30]"})]
            }
        )

        assert hero_meta(result)["keyTakeaways"] == ["A [1:00]"]


class TestCachedServe:
    def test_should_relay_the_hero_fields_a_stored_meta_carries(self) -> None:
        meta = {**hero_meta(_HERO, minutes=4), "contentTags": ["learning"]}

        event = resolve_synthesis({"meta": meta})

        assert {key: event[key] for key in ("brief", "readingMinutes")} == {
            "brief": _HERO.brief,
            "readingMinutes": 4,
        }

    def test_should_send_the_four_fields_for_an_older_row(self) -> None:
        event = resolve_synthesis({"meta": {"tldr": "t", "masterSummary": "m"}})

        assert set(event) == {"tldr", "keyTakeaways", "masterSummary", "seoDescription"}
