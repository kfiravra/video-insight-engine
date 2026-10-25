"""Moment frames off the critical path (output-quality 3.7).

``complete`` goes out once the text output is done. A moment fill that would wait for
the run's 720p file still downloading runs after it: the moment tabs stream with the
other tabs, non-final, and their final ``tab_ready`` (framed, replace-by-id) follows
``complete``, before the save and ``done``. A 720p file already on disk keeps today's
order. Faked boundaries: the 720p download (``_Hires``), the S3 client and the ffmpeg
seek; assembly, the ledger and the fill run as production code.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.memory_types import MemoryResult
from src.models.pipeline_types import SynthesisResult
from src.services.pipeline.assembly import moment_frame_fill as mff
from src.services.pipeline.assembly.tab_references import drop_shadowed_filmstrips
from src.services.pipeline.phases import assembly as phase
from src.services.pipeline.phases import synthesis as synthesis_phase
from src.services.pipeline.phases.synthesis import settle_tab_references
from src.services.pipeline.tab_ledger import TabLedger
from tests.test_phase_assembly_cache import _build_ctx
from tests.test_phase_assembly_order import _events

_SYNTHESIS = SynthesisResult.model_validate({"masterSummary": "S.", "seoDescription": "SEO."})
_MOMENTS = "segments"


class _Hires:
    """A LocalHiresSource stand-in whose download lands when ``landed`` is set."""

    def __init__(self, *, on_disk: bool, path: Path | None = Path("/tmp/vie-hires/yt.mp4")):
        self.started = True
        self.closed = False
        self.read = False
        self._landed = asyncio.Event()
        self._path = path
        if on_disk:
            self._landed.set()

    @property
    def finished(self) -> bool:
        return self._landed.is_set()

    def land_after(self, seconds: float) -> None:
        asyncio.get_running_loop().call_later(seconds, self._landed.set)

    async def path(self) -> Path | None:
        await self._landed.wait()
        self.read = True
        return self._path

    async def close(self) -> None:
        self.closed = True


def _s3() -> MagicMock:
    s3 = MagicMock()
    s3.is_available = MagicMock(return_value=True)
    s3.exists = AsyncMock(return_value=False)
    s3.put_bytes = AsyncMock(return_value=None)
    s3.generate_presigned_url = MagicMock(side_effect=lambda key: f"https://s3/{key}")
    return s3


def _podcast_ctx(hires: _Hires | None) -> Any:
    """Assembly backfills podcast's required moment_track as ``segments`` (two frameless
    moments) beside the planned ``topics`` tab."""
    ctx = _build_ctx()
    ctx.memory = MemoryResult(tldr="Memory tldr.", takeaways=["m1", "m2", "m3"])
    ctx.llm_service = MagicMock()
    ctx.video_data.duration = 900
    ctx.triage.primary_tag = "podcast"
    ctx.triage_dict = {"contentTags": ["podcast"], "primaryTag": "podcast", "tabs": [
        {"id": "topics", "label": "Topics", "component": "info_grid",
         "dataSource": "podcast.topics", "goal": "Topics"},
    ]}  # fmt: skip
    ctx.extraction_data = {"podcast": {
        "segments": [{"title": "Intro", "summary": "Hi", "timestamp": 0},
                     {"title": "Deep dive", "summary": "Raft", "timestamp": 480}],
        "topics": [{"topic": "Raft", "detail": "Consensus"},
                   {"topic": "CAP", "detail": "Trade-offs"}],
    }}  # fmt: skip
    ctx.hires_video = hires
    return ctx


async def _run(ctx: Any, *, land_after: float | None = 0.05) -> list[tuple[str, dict]]:
    """Run assembly; a still-downloading 720p file lands ``land_after`` seconds in."""
    if land_after is not None and ctx.hires_video is not None:
        ctx.hires_video.land_after(land_after)
    with (
        patch.object(phase, "needs_quiz", lambda *_a: False),
        patch.object(phase, "send_video_status_background", MagicMock()),
        patch.object(synthesis_phase, "synthesize", AsyncMock(return_value=_SYNTHESIS)),
        patch.object(phase.settings, "REDIS_ENABLED", False),
        patch.object(phase.settings, "QDRANT_ENABLED", False),
        patch.object(phase.settings, "HERO_REWRITE_ENABLED", False),
        patch.object(phase.settings, "SSE_HEARTBEAT_SECONDS", 0.01),
        patch.object(mff, "s3_client", _s3()),
        patch.object(mff, "extract_frame", AsyncMock(return_value=b"jpeg")),
    ):
        chunks = [chunk async for chunk in phase.run_phase_assembly(ctx)]
    return _events(chunks)


def _names(events: list[tuple[str, dict]]) -> list[str]:
    return [name for name, _ in events]


def _moment_emissions(events: list[tuple[str, dict]]) -> list[tuple[int, dict]]:
    return [(i, p) for i, (n, p) in enumerate(events) if n == "tab_ready" and p["id"] == _MOMENTS]


def _framed(tab: dict) -> int:
    return sum(1 for item in tab["props"]["items"] if item.get("thumbnailUrl"))


def _saved(ctx: Any) -> dict:
    return ctx.repository.save_structured_result.call_args.args[1]


# ─── 720p still downloading: the fill runs after `complete` ───


class TestDeferredFill:
    async def test_should_send_complete_before_the_moment_tab_final_when_the_720p_downloads(
        self,
    ) -> None:
        events = await _run(_podcast_ctx(_Hires(on_disk=False)))

        final_at = next(i for i, p in _moment_emissions(events) if p["final"])
        assert _names(events).index("complete") < final_at

    async def test_should_stream_the_moment_tab_non_final_before_complete_when_the_720p_downloads(
        self,
    ) -> None:
        events = await _run(_podcast_ctx(_Hires(on_disk=False)))

        complete_at = _names(events).index("complete")
        early = [p["final"] for i, p in _moment_emissions(events) if i < complete_at]
        assert early == [False]

    async def test_should_send_the_moment_tab_framed_before_done_when_the_fill_was_deferred(
        self,
    ) -> None:
        events = await _run(_podcast_ctx(_Hires(on_disk=False)))

        final_at, final = next((i, p) for i, p in _moment_emissions(events) if p["final"])
        assert (_framed(final), final_at < _names(events).index("done")) == (2, True)

    async def test_should_send_every_other_tab_final_before_complete_when_the_fill_was_deferred(
        self,
    ) -> None:
        events = await _run(_podcast_ctx(_Hires(on_disk=False)))

        complete_at = _names(events).index("complete")
        early_finals = {p["id"] for n, p in events[:complete_at] if n == "tab_ready" and p["final"]}
        assert early_finals == {"overview", "topics"}

    async def test_should_send_each_saved_tab_final_once_when_the_fill_was_deferred(self) -> None:
        ctx = _podcast_ctx(_Hires(on_disk=False))

        events = await _run(ctx)

        finals = Counter(p["id"] for n, p in events if n == "tab_ready" and p["final"])
        assert finals == Counter(t["id"] for t in _saved(ctx)["tabs"])

    async def test_should_list_the_moment_tab_on_complete_when_the_fill_was_deferred(self) -> None:
        events = await _run(_podcast_ctx(_Hires(on_disk=False)))

        complete = next(p for n, p in events if n == "complete")
        assert complete["tabIds"] == ["overview", "topics", _MOMENTS]

    async def test_should_send_heartbeats_while_the_deferred_fill_waits_for_the_720p(self) -> None:
        events = await _run(_podcast_ctx(_Hires(on_disk=False)))

        names = _names(events)
        final_at = next(i for i, p in _moment_emissions(events) if p["final"])
        assert "heartbeat" in names[names.index("complete") : final_at]

    async def test_should_save_the_framed_moments_when_the_fill_was_deferred(self) -> None:
        ctx = _podcast_ctx(_Hires(on_disk=False))

        await _run(ctx)

        saved = next(t for t in _saved(ctx)["tabs"] if t["id"] == _MOMENTS)
        assert _framed(saved) == 2

    async def test_should_time_complete_at_text_done_and_done_after_the_fill_when_deferred(
        self,
    ) -> None:
        ctx = _podcast_ctx(_Hires(on_disk=False))
        ctx.timer.elapsed = MagicMock(side_effect=itertools.count(start=1))

        events = await _run(ctx)

        complete = next(p for n, p in events if n == "complete")["processingTimeMs"]
        done = next(p for n, p in events if n == "done")["processingTimeMs"]
        assert (complete < done, _saved(ctx)["processingTimeMs"]) == (True, done)

    async def test_should_free_the_720p_file_after_the_deferred_fill_read_it(self) -> None:
        hires = _Hires(on_disk=False)

        await _run(_podcast_ctx(hires))

        assert (hires.read, hires.closed) == (True, True)

    async def test_should_ship_glyph_plates_once_final_when_the_720p_download_failed(self) -> None:
        ctx = _podcast_ctx(_Hires(on_disk=False, path=None))

        events = await _run(ctx)

        finals = [p for _, p in _moment_emissions(events) if p["final"]]
        assert [_framed(p) for p in finals] == [0]


# ─── 720p on disk: today's order ───


class TestFillOnDisk:
    async def test_should_send_the_moment_tab_final_before_complete_when_the_720p_is_on_disk(
        self,
    ) -> None:
        events = await _run(_podcast_ctx(_Hires(on_disk=True)), land_after=None)

        complete_at = _names(events).index("complete")
        assert [(i < complete_at, p["final"]) for i, p in _moment_emissions(events)] == [
            (True, True)
        ]

    async def test_should_stream_the_moment_tab_framed_when_the_720p_is_on_disk(self) -> None:
        events = await _run(_podcast_ctx(_Hires(on_disk=True)), land_after=None)

        assert [_framed(p) for _, p in _moment_emissions(events)] == [2]

    async def test_should_defer_nothing_without_a_720p_source(self) -> None:
        events = await _run(_podcast_ctx(None), land_after=None)

        complete_at = _names(events).index("complete")
        assert [i < complete_at for i, _ in _moment_emissions(events)] == [True]


class TestSplitPath:
    async def test_should_stream_the_moment_tab_with_the_previews_when_the_fill_was_deferred(
        self,
    ) -> None:
        ctx = _podcast_ctx(_Hires(on_disk=False))
        ctx.tab_ledger.expect_groups()

        events = await _run(ctx)

        first = _moment_emissions(events)[0]
        assert (first[0] < _names(events).index("synthesis_complete"), first[1]["final"]) == (
            True,
            False,
        )


# ─── What the fill would add ───


def _moment_tab(tab_id: str, frameless: int, framed: int = 0) -> dict:
    items = [{"label": f"m{i}", "seconds": 60 * i} for i in range(frameless)]
    items += [
        {"label": "f", "seconds": 5000 + i, "thumbnailUrl": "https://f"} for i in range(framed)
    ]
    return {"id": tab_id, "component": "moment_track", "props": {"items": items}}


def _source(*, started: bool = True) -> SimpleNamespace:
    return SimpleNamespace(started=started, finished=False)


class TestPendingFill:
    def test_should_count_each_moment_tab_frameless_items_when_the_720p_is_on_its_way(
        self,
    ) -> None:
        tabs = [_moment_tab("a", 2, framed=3), {"id": "x", "component": "info_grid"}]

        with patch.object(mff, "s3_client", _s3()):
            assert mff.pending_fill(tabs, _source()) == {"a": 2}  # type: ignore[arg-type]

    def test_should_stop_at_the_fill_cap_in_tab_order_when_moments_exceed_it(self) -> None:
        tabs = [_moment_tab("a", 10), _moment_tab("b", 10)]

        with patch.object(mff, "s3_client", _s3()):
            assert mff.pending_fill(tabs, _source()) == {"a": 10, "b": 2}  # type: ignore[arg-type]

    def test_should_count_nothing_when_s3_is_unavailable(self) -> None:
        s3 = _s3()
        s3.is_available = MagicMock(return_value=False)

        with patch.object(mff, "s3_client", s3):
            assert mff.pending_fill([_moment_tab("a", 4)], _source()) == {}  # type: ignore[arg-type]

    def test_should_count_nothing_without_a_720p_source(self) -> None:
        with patch.object(mff, "s3_client", _s3()):
            assert mff.pending_fill([_moment_tab("a", 4)], None) == {}

    def test_should_count_nothing_when_too_few_moments_would_start_a_download(self) -> None:
        tabs = [_moment_tab("a", 2)]

        with patch.object(mff, "s3_client", _s3()):
            assert mff.pending_fill(tabs, _source(started=False)) == {}  # type: ignore[arg-type]

    async def test_should_count_what_the_fill_then_adds_when_the_720p_lands(self) -> None:
        tabs = [_moment_tab("a", 10), _moment_tab("b", 10)]
        handle = MagicMock(started=True, path=AsyncMock(return_value=Path("/tmp/v.mp4")))

        with (
            patch.object(mff, "s3_client", _s3()),
            patch.object(mff, "extract_frame", AsyncMock(return_value=b"jpeg")),
        ):
            pending = sum(mff.pending_fill(tabs, handle).values())
            filled = await mff.fill_moment_frames(tabs, "yt", handle)

        assert pending == filled


# ─── The ledger while the frames are pending ───


def _ledger() -> TabLedger:
    ledger = TabLedger()
    ledger.expect_late_changes(quiz=False, synthesis=False, frames=True)
    return ledger


def _payload(chunk: str) -> dict:
    return json.loads(chunk.removeprefix("data: "))


class TestLedgerFramesPending:
    def test_should_mark_a_moment_tab_non_final_while_frames_are_pending(self) -> None:
        assert _payload(_ledger().event(_moment_tab("m", 1), 1))["final"] is False

    def test_should_mark_other_tabs_final_while_only_frames_are_pending(self) -> None:
        tab = {"id": "t", "component": "info_grid", "props": {}}

        assert _payload(_ledger().event(tab, 0))["final"] is True

    def test_should_not_re_send_an_unchanged_moment_tab_while_frames_are_pending(self) -> None:
        ledger, tab = _ledger(), _moment_tab("m", 1)
        ledger.event(tab, 0)

        assert ledger.closing_events([tab]) == []

    def test_should_re_send_a_moved_moment_tab_non_final_while_frames_are_pending(self) -> None:
        ledger, tab = _ledger(), _moment_tab("m", 1)
        ledger.event(tab, 0)

        events = ledger.closing_events([{"id": "o", "component": "overview"}, tab])

        assert [(_payload(e)["id"], _payload(e)["final"]) for e in events] == [
            ("o", True),
            ("m", False),
        ]

    def test_should_send_the_moment_tab_final_once_the_frames_landed(self) -> None:
        ledger, tab = _ledger(), _moment_tab("m", 1)
        ledger.event(tab, 0)
        ledger.closing_events([tab])
        ledger.frames_landed()

        assert [_payload(e)["final"] for e in ledger.closing_events([tab])] == [True]


# ─── The filmstrip decision counts the frames still to come ───


def _filmstrip() -> dict:
    return {"id": "frames-gallery", "label": "Frames", "component": "video_filmstrip"}


class TestFilmstripDecision:
    def test_should_drop_the_filmstrip_when_pending_frames_make_the_moments_rich(self) -> None:
        tabs = [_moment_tab("m", 6, framed=3), _filmstrip()]

        drop_shadowed_filmstrips(tabs, {"m": 5})

        assert [t["id"] for t in tabs] == ["m"]

    def test_should_keep_the_filmstrip_when_pending_frames_fall_short(self) -> None:
        tabs = [_moment_tab("m", 6, framed=3), _filmstrip()]

        drop_shadowed_filmstrips(tabs, {"m": 4})

        assert [t["id"] for t in tabs] == ["m", "frames-gallery"]

    def test_should_count_pending_frames_only_for_their_own_moment_tab(self) -> None:
        tabs = [_moment_tab("a", 0, framed=5), _moment_tab("b", 4), _filmstrip()]

        drop_shadowed_filmstrips(tabs, {"b": 4})

        assert [t["id"] for t in tabs] == ["a", "b", "frames-gallery"]

    def test_should_drop_a_planned_filmstrip_at_settle_when_the_deferred_fill_frames_enough(
        self,
    ) -> None:
        tabs = [_moment_tab("m", 8), _filmstrip()]
        hero = {"tldr": "T", "masterSummary": "A run.", "keyTakeaways": [], "seoDescription": ""}
        ctx = SimpleNamespace(
            assembled_meta=dict(hero),
            assembled_tabs=tabs,
            triage_dict={"tabs": [{"id": "frames-gallery", "dataSource": "frames"}]},
            synthesis_dict=dict(hero),
            pending_moment_frames={"m": 8},
        )
        assembled: dict[str, Any] = {"tabs": tabs, "dropped": []}

        settle_tab_references(ctx, assembled)  # type: ignore[arg-type]

        assert [d["id"] for d in assembled["dropped"]] == ["frames-gallery"]
