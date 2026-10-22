"""Phase-3 review fixes, area D: the moment fill deferred past ``complete`` (3.7).

- the deferred fill starts with the late group, so its seeks overlap the quiz and the
  hero once the 720p file lands; ``complete`` still does not wait for it (D3);
- the filmstrip decision counts the frames still to come, never a landed one twice (D3);
- the saved row records what the deferred fill was to add, what it added, and whether
  a filmstrip went on frames not landed yet (``pipeline.assembly.momentFill``, D2);
- the contract holds with the quiz pending and on a non-English run (D4).

Faked boundaries as in ``test_p3_7_late_moment_frames``: the 720p download, S3, the
ffmpeg seek, the LLM calls; assembly, the ledger and the fill run as production code.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from collections import Counter
from collections.abc import AsyncGenerator, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.services.pipeline.phases.late_moment_fill import LateMomentFill

from src.services.pipeline.assembly import moment_frame_fill as mff
from src.services.pipeline.phases import assembly as phase
from src.services.pipeline.phases import synthesis as synthesis_phase
from tests.test_p3_7_late_moment_frames import (
    _MOMENTS,
    _SYNTHESIS,
    _filmstrip,
    _framed,
    _Hires,
    _moment_emissions,
    _moment_tab,
    _names,
    _podcast_ctx,
    _s3,
    _saved,
)
from tests.test_phase_assembly_order import _events, _quiz_phase

Phase = Callable[[Any], AsyncGenerator[str, None]]


def _slow_quiz(seconds: float) -> Phase:
    """The quiz phase, landing ``seconds`` into the late group."""

    async def run_phase_enrichment(ctx: Any) -> AsyncGenerator[str, None]:
        await asyncio.sleep(seconds)
        async for event in _quiz_phase(ctx):
            yield event

    return run_phase_enrichment


async def _failing_quiz(_ctx: Any) -> AsyncGenerator[str, None]:
    await asyncio.sleep(0.02)
    raise RuntimeError("quiz call failed")
    yield  # pragma: no cover


async def _run(
    ctx: Any,
    *,
    quiz: Phase | None = None,
    land_after: float | None = 0.05,
    response: dict | None = None,
    timeline: list[str] | None = None,
) -> list[tuple[str, dict]]:
    """Run assembly. ``quiz``: the late quiz phase (none planned when None); ``response``:
    the assembled response (else production assembly); ``timeline``: event names and
    ``seek`` (one per ffmpeg seek) in the order they happened."""
    order = timeline if timeline is not None else []
    if land_after is not None and ctx.hires_video is not None:
        ctx.hires_video.land_after(land_after)

    async def _seek(_source: str, _second: int) -> bytes:
        order.append("seek")
        return b"jpeg"

    assemble = (lambda **_kw: copy.deepcopy(response)) if response else phase.assemble_response
    chunks: list[str] = []
    with (
        patch.object(phase, "needs_quiz", lambda *_a: quiz is not None),
        patch.object(phase, "run_phase_enrichment", quiz or _quiz_phase),
        patch.object(phase, "assemble_response", assemble),
        patch.object(phase, "send_video_status_background", MagicMock()),
        patch.object(synthesis_phase, "synthesize", AsyncMock(return_value=_SYNTHESIS)),
        patch.object(phase.settings, "REDIS_ENABLED", False),
        patch.object(phase.settings, "QDRANT_ENABLED", False),
        patch.object(phase.settings, "HERO_REWRITE_ENABLED", False),
        patch.object(phase.settings, "SSE_HEARTBEAT_SECONDS", 0.01),
        patch.object(mff, "s3_client", _s3()),
        patch.object(mff, "extract_frame", AsyncMock(side_effect=_seek)),
    ):
        async for chunk in phase.run_phase_assembly(ctx):
            chunks.append(chunk)
            order.extend(name for name, _ in _events([chunk]))
    return _events(chunks)


def _filmstrip_response(frameless: int, framed: int = 0) -> dict:
    """A response whose Moments (``m``) and a filmstrip both ship from assembly."""
    tabs = [
        {"id": "overview", "label": "Overview", "component": "overview", "props": {"data": {}}},
        _moment_tab("m", frameless, framed=framed),
        _filmstrip() | {"props": {"frames": [{"timestamp": 1, "thumbnailUrl": "https://f"}]}},
    ]
    return {"tabs": tabs, "meta": {}}


def _record(ctx: Any) -> dict | None:
    return _saved(ctx)["pipeline"]["assembly"].get("momentFill")


# ─── D3: the deferred fill's seeks overlap the late group ───


class TestFillOverlapsTheLateGroup:
    async def test_should_seek_the_moment_frames_before_complete_when_the_720p_lands_mid_group(
        self,
    ) -> None:
        timeline: list[str] = []

        await _run(
            _podcast_ctx(_Hires(on_disk=False)),
            quiz=_slow_quiz(0.2),
            land_after=0.02,
            timeline=timeline,
        )

        assert timeline.index("seek") < timeline.index("complete")

    async def test_should_still_send_the_moment_tab_final_after_complete_when_it_landed_mid_group(
        self,
    ) -> None:
        events = await _run(
            _podcast_ctx(_Hires(on_disk=False)), quiz=_slow_quiz(0.2), land_after=0.02
        )

        finals = [i for i, p in _moment_emissions(events) if p["final"]]
        assert [i > _names(events).index("complete") for i in finals] == [True]

    async def test_should_not_hold_complete_for_the_720p_when_the_late_group_ends_first(
        self,
    ) -> None:
        timeline: list[str] = []

        await _run(_podcast_ctx(_Hires(on_disk=False)), land_after=0.2, timeline=timeline)

        assert timeline.index("complete") < timeline.index("seek")

    async def test_should_cancel_the_deferred_fill_when_the_late_group_fails(self) -> None:
        ctx = _podcast_ctx(_Hires(on_disk=False))

        with pytest.raises(RuntimeError, match="quiz call failed"):
            await _run(ctx, quiz=_failing_quiz, land_after=None)
        await asyncio.sleep(0)

        running = [t for t in asyncio.all_tasks() if t.get_name().startswith("moment_fill_")]
        assert running == []


class TestFramesStillToCome:
    def _fill(self, task_done: bool = False) -> LateMomentFill:
        task = MagicMock()
        task.done = MagicMock(return_value=task_done)
        return LateMomentFill(task=task, pending={"m": 4}, framed={"m": 3})

    def test_should_count_only_the_frames_not_landed_yet_when_some_landed_mid_group(self) -> None:
        tab = _moment_tab("m", 2, framed=5)  # 2 of the 4 pending frames landed

        assert self._fill().still_to_come([tab]) == {"m": 2}

    def test_should_count_nothing_still_to_come_once_the_fill_ended(self) -> None:
        tab = _moment_tab("m", 2, framed=5)

        assert self._fill(task_done=True).still_to_come([tab]) == {}

    async def test_should_keep_the_filmstrip_when_frames_landed_mid_group_stay_below_the_shadow(
        self,
    ) -> None:
        ctx = _podcast_ctx(_Hires(on_disk=False))

        await _run(
            ctx,
            quiz=_slow_quiz(0.2),
            land_after=0.02,
            response=_filmstrip_response(frameless=4, framed=3),
        )

        assert [t["id"] for t in _saved(ctx)["tabs"]] == ["overview", "m", "frames-gallery"]


# ─── D2: the deferred fill's record ───


class TestMomentFillRecord:
    async def test_should_record_the_frames_pending_and_filled_when_the_fill_was_deferred(
        self,
    ) -> None:
        ctx = _podcast_ctx(_Hires(on_disk=False))

        await _run(ctx)

        assert _record(ctx) == {"pending": 2, "filled": 2, "filmstripDroppedOnPending": False}

    async def test_should_record_no_frame_filled_when_the_deferred_fill_found_no_720p_file(
        self,
    ) -> None:
        ctx = _podcast_ctx(_Hires(on_disk=False, path=None))

        await _run(ctx)

        assert _record(ctx) == {"pending": 2, "filled": 0, "filmstripDroppedOnPending": False}

    async def test_should_record_no_moment_fill_when_the_720p_was_on_disk(self) -> None:
        ctx = _podcast_ctx(_Hires(on_disk=True))

        await _run(ctx, land_after=None)

        assert "momentFill" not in _saved(ctx)["pipeline"]["assembly"]

    async def test_should_flag_a_filmstrip_dropped_on_pending_frames_when_the_fill_then_failed(
        self,
    ) -> None:
        ctx = _podcast_ctx(_Hires(on_disk=False, path=None))

        await _run(ctx, response=_filmstrip_response(frameless=8))

        assert _record(ctx) == {"pending": 8, "filled": 0, "filmstripDroppedOnPending": True}

    async def test_should_warn_when_a_filmstrip_went_on_frames_that_never_landed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        ctx = _podcast_ctx(_Hires(on_disk=False, path=None))

        with caplog.at_level(logging.WARNING, logger="src.services.pipeline.phases"):
            await _run(ctx, response=_filmstrip_response(frameless=8))

        assert any(
            "filmstrip" in r.getMessage() and "0/8" in r.getMessage() for r in caplog.records
        )

    async def test_should_not_flag_the_filmstrip_when_scene_frames_alone_shadow_it(self) -> None:
        ctx = _podcast_ctx(_Hires(on_disk=False, path=None))

        await _run(ctx, response=_filmstrip_response(frameless=3, framed=8))

        assert _record(ctx) == {"pending": 3, "filled": 0, "filmstripDroppedOnPending": False}


# ─── D4: the contract with the quiz pending, and on a non-English run ───


class TestDeferredFillWithTheQuizPending:
    async def test_should_send_each_saved_tab_final_once_when_the_quiz_was_pending(self) -> None:
        ctx = _podcast_ctx(_Hires(on_disk=False))

        events = await _run(ctx, quiz=_quiz_phase)

        finals = Counter(p["id"] for n, p in events if n == "tab_ready" and p["final"])
        assert finals == Counter(t["id"] for t in _saved(ctx)["tabs"])

    async def test_should_send_nothing_for_a_tab_after_its_final_when_the_quiz_was_pending(
        self,
    ) -> None:
        events = await _run(_podcast_ctx(_Hires(on_disk=False)), quiz=_quiz_phase)

        tab_events = [p for n, p in events if n == "tab_ready"]
        last_final = {p["id"]: i for i, p in enumerate(tab_events) if p["final"]}
        assert [p["id"] for i, p in enumerate(tab_events) if i > last_final[p["id"]]] == []

    async def test_should_send_the_moment_tab_final_and_framed_after_complete_with_the_quiz(
        self,
    ) -> None:
        events = await _run(_podcast_ctx(_Hires(on_disk=False)), quiz=_quiz_phase)

        final_at, final = next((i, p) for i, p in _moment_emissions(events) if p["final"])
        assert (final_at > _names(events).index("complete"), _framed(final)) == (True, 2)

    async def test_should_save_the_framed_moments_once_when_the_quiz_was_pending(self) -> None:
        ctx = _podcast_ctx(_Hires(on_disk=False))

        await _run(ctx, quiz=_quiz_phase)

        saved = next(t for t in _saved(ctx)["tabs"] if t["id"] == _MOMENTS)
        assert (ctx.repository.save_structured_result.call_count, _framed(saved)) == (1, 2)


def _hebrew_ctx() -> Any:
    ctx = _podcast_ctx(_Hires(on_disk=False))
    ctx.source_language_code = "he"
    return ctx


class TestDeferredFillNonEnglish:
    async def test_should_send_no_done_from_assembly_when_the_source_is_not_english(self) -> None:
        events = await _run(_hebrew_ctx())

        assert "done" not in _names(events)

    async def test_should_send_the_moment_tab_final_after_complete_when_the_source_is_hebrew(
        self,
    ) -> None:
        events = await _run(_hebrew_ctx())

        finals = [i for i, p in _moment_emissions(events) if p["final"]]
        assert [i > _names(events).index("complete") for i in finals] == [True]

    async def test_should_save_once_with_framed_moments_when_the_source_is_hebrew(self) -> None:
        ctx = _hebrew_ctx()

        await _run(ctx)

        saved = next(t for t in _saved(ctx)["tabs"] if t["id"] == _MOMENTS)
        assert (ctx.repository.save_structured_result.call_count, _framed(saved)) == (1, 2)

    async def test_should_leave_the_framed_moments_for_translation_when_the_source_is_hebrew(
        self,
    ) -> None:
        ctx = _hebrew_ctx()

        await _run(ctx)

        moments = next(t for t in ctx.assembled_tabs if t["id"] == _MOMENTS)
        assert _framed(moments) == 2
