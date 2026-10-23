"""Vision skip for long talking heads (output-quality 3.5, D2, decision D-A).

``FRAMES_SKIP_WHEN_NO_VISUAL_DEMO``: a video over 30 min whose usable tier probe
saw no visual demo and whose tier is not HIGH skips scene detection + vision when
its tier is LOW or its probe format is in ``visualCriticality.skipFormats``
(``[]`` = LOW only). The skip closes the 360p download, frames-done still follows
plan-done, and the moment fill frames every moment from the kept 720p file. The
same task drops the bare ``"opening"`` title keyword. Faked boundaries: the probe
task, the downloads, scene extraction, the S3 client and the ffmpeg seek.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import Settings, settings
from src.models.probe_types import VALID_FORMATS, TierProbe
from src.services.media import visual_tier
from src.services.media.visual_tier import derive_tier, metadata_tier, skips_frames
from src.services.pipeline.assembly import moment_frame_fill as mff
from src.services.pipeline.phases import assembly as assembly_phase
from src.services.pipeline.phases import frames as frames_phase
from src.services.pipeline.phases import frames_done
from src.services.pipeline.tab_ledger import TabLedger
from src.shared_config.domain_config import visual_criticality_config

_LONG = 4978.0  # S02, 83 min
_RECOMMENDED = ["talk", "interview", "podcast"]  # D-A (ii)


def _probe(domain: str, fmt: str, *, demo: bool = False, usable: bool = True) -> TierProbe:
    return TierProbe(
        domain=domain, format=fmt, has_visual_demo=demo, confidence=0.95, transcript_usable=usable
    )


@pytest.fixture(params=[_RECOMMENDED, []], ids=["skipFormats=talk,interview,podcast", "[]"])
def skip_formats(request: pytest.FixtureRequest) -> Iterator[list[str]]:
    """The shipped table with each D-A answer as its ``skipFormats``."""
    table = copy.deepcopy(visual_criticality_config())
    table["skipFormats"] = request.param
    with patch.object(visual_tier, "visual_criticality_config", return_value=table):
        yield request.param


def _decide(probe: TierProbe | None, title: str, duration: float) -> bool:
    return skips_frames(probe, derive_tier(probe, title), duration)


# ─── The decision table ───


class TestSkipsFrames:
    def test_should_skip_when_a_long_low_tier_video_shows_no_demo(self, skip_formats) -> None:
        assert _decide(_probe("podcast", "interview"), "Max Tegmark: Life 3.0", _LONG) is True

    def test_should_skip_a_standard_tier_talk_only_when_talk_is_a_skip_format(
        self, skip_formats
    ) -> None:
        skipped = _decide(_probe("learning", "talk"), "A keynote", _LONG)

        assert skipped is ("talk" in skip_formats)

    def test_should_keep_frames_for_a_lecture_when_its_tier_is_standard(self, skip_formats) -> None:
        assert _decide(_probe("learning", "lecture"), "1. Introduction", _LONG) is False

    def test_should_keep_frames_when_the_tier_is_high(self, skip_formats) -> None:
        assert _decide(_probe("podcast", "unboxing"), "Podcast merch", _LONG) is False

    def test_should_keep_frames_when_the_probe_saw_a_visual_demo(self, skip_formats) -> None:
        assert _decide(_probe("podcast", "talk", demo=True), "Live coding chat", _LONG) is False

    def test_should_keep_frames_when_the_video_is_thirty_minutes_or_less(
        self, skip_formats
    ) -> None:
        assert _decide(_probe("podcast", "interview"), "Short chat", 1800.0) is False

    def test_should_keep_frames_when_there_is_no_probe(self, skip_formats) -> None:
        assert skips_frames(None, "low", _LONG) is False

    def test_should_keep_frames_when_the_probe_read_an_unusable_transcript(
        self, skip_formats
    ) -> None:
        garbage = _probe("podcast", "interview", usable=False)

        assert skips_frames(garbage, "low", _LONG) is False


# The stored gate-2 probes (the output-quality task's traces, 2026-10).
_SAMPLE = [
    ("S01", "learning", "talk", False, 3787, "Rails World 2026 Opening Keynote - DHH"),
    ("S02", "podcast", "interview", False, 4978, "Max Tegmark: Life 3.0 | Lex Fridman Podcast #1"),
    ("S03", "learning", "lecture", False, 3434, "1. Introduction to Human Behavioral Biology"),
    ("S13", "podcast", "interview", False, 2532, "DHH Omarchy Quattro Announcement | TheStandup"),
    ("S05", "tech", "lecture", True, 794, "10 React Hooks Explained // Plus Build your own"),
    ("S06", "food", "tutorial", True, 1001, "Chris Makes BA's Best Lasagna | Bon Appétit"),
]
_SKIPPED = {"S02", "S13"}  # tier LOW, either answer
_SKIPPED_BY_FORMAT = {"S01"}  # STANDARD talk, only with talk listed


@pytest.mark.parametrize(("code", "domain", "fmt", "demo", "duration", "title"), _SAMPLE)
def test_should_skip_the_sample_videos_the_answer_names_when_their_probes_are_read(
    skip_formats, code: str, domain: str, fmt: str, demo: bool, duration: int, title: str
) -> None:
    expected = code in _SKIPPED or (code in _SKIPPED_BY_FORMAT and bool(skip_formats))

    assert _decide(_probe(domain, fmt, demo=demo), title, float(duration)) is expected


# ─── The registry ───


class TestRegistry:
    def test_should_list_only_probe_formats_as_skip_formats(self) -> None:
        assert set(visual_criticality_config().get("skipFormats", [])) <= VALID_FORMATS

    def test_should_never_list_a_high_format_as_a_skip_format(self) -> None:
        table = visual_criticality_config()

        assert not set(table.get("skipFormats", [])) & set(table.get("highFormats", []))

    def test_should_not_raise_an_opening_keynote_to_the_high_tier(self) -> None:
        title = "Rails World 2026 Opening Keynote - DHH"

        assert derive_tier(_probe("learning", "talk"), title) == "standard"

    def test_should_keep_a_pack_opening_title_at_the_high_tier(self) -> None:
        assert derive_tier(_probe("gaming", "commentary"), "Pokemon pack opening") == "high"

    def test_should_keep_a_box_opening_tag_at_the_high_tier_without_a_probe(self) -> None:
        assert metadata_tier(None, "OP-17 booster box!", ["one piece box opening"]) == "high"

    def test_should_default_the_flag_on_when_env_is_unset(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("FRAMES_SKIP_WHEN_NO_VISUAL_DEMO", raising=False)

        assert Settings().FRAMES_SKIP_WHEN_NO_VISUAL_DEMO is True


# ─── The frames phase ───


def _landed(probe: TierProbe | None) -> asyncio.Future[TierProbe | None]:
    future: asyncio.Future[TierProbe | None] = asyncio.get_running_loop().create_future()
    future.set_result(probe)
    return future


def _never() -> asyncio.Future[TierProbe | None]:
    return asyncio.get_running_loop().create_future()


def _frames_ctx(probe_task: Any, duration: float = _LONG) -> Any:
    video = SimpleNamespace(title="Max Tegmark: Life 3.0", duration=duration, context=None)
    return SimpleNamespace(
        youtube_id="yt",
        video_data=video,
        tier_probe_task=probe_task,
        # A cold run's 360p download, already on disk.
        lowres_video=MagicMock(
            started=True, close=AsyncMock(), path=AsyncMock(return_value=Path("/tmp/yt.mp4"))
        ),
        hires_video=None,
        frame_descriptions=[],
        frames_skipped=False,
        plan_done=asyncio.Event(),
        frames_done=asyncio.Event(),
    )


async def _run_frames(ctx: Any, *, flag: bool = True) -> tuple[list[str], AsyncMock]:
    extract = AsyncMock(return_value={"all_frames": [], "selected_frames": []})
    with (
        patch.object(settings, "SCENE_EXTRACTION_ENABLED", True),
        patch.object(settings, "FRAMES_SKIP_WHEN_NO_VISUAL_DEMO", flag),
        patch.object(frames_phase, "extract_scene_keyframes", extract),
    ):
        events = await asyncio.wait_for(_collect(frames_phase.run_phase_frames(ctx)), timeout=2)
    return events, extract


async def _collect(stream: Any) -> list[str]:
    return [event async for event in stream]


class TestRunPhaseFrames:
    async def test_should_return_before_scene_detection_when_the_run_skips(self) -> None:
        _events, extract = await _run_frames(_frames_ctx(_landed(_probe("podcast", "interview"))))

        extract.assert_not_awaited()

    async def test_should_send_no_frames_event_when_the_run_skips(self) -> None:
        events, _extract = await _run_frames(_frames_ctx(_landed(_probe("podcast", "interview"))))

        assert events == []

    async def test_should_close_the_360p_download_when_the_run_skips(self) -> None:
        ctx = _frames_ctx(_landed(_probe("podcast", "interview")))

        await _run_frames(ctx)

        ctx.lowres_video.close.assert_awaited_once()

    async def test_should_mark_the_run_skipped_when_the_run_skips(self) -> None:
        ctx = _frames_ctx(_landed(_probe("podcast", "interview")))

        await _run_frames(ctx)

        assert ctx.frames_skipped is True

    async def test_should_extract_frames_when_the_probe_saw_a_demo(self) -> None:
        ctx = _frames_ctx(_landed(_probe("podcast", "interview", demo=True)))

        _events, extract = await _run_frames(ctx)

        assert (extract.await_count, ctx.frames_skipped) == (1, False)

    async def test_should_extract_frames_when_the_probe_never_lands(self) -> None:
        ctx = _frames_ctx(_never())

        with patch.object(frames_phase, "PLAN_PROBE_WAIT_SECONDS", 0.01):
            _events, extract = await _run_frames(ctx)

        extract.assert_awaited_once()

    async def test_should_not_wait_for_the_probe_when_the_flag_is_off(self) -> None:
        _events, extract = await _run_frames(_frames_ctx(_never()), flag=False)

        extract.assert_awaited_once()

    async def test_should_not_wait_for_the_probe_when_the_video_is_29_minutes(self) -> None:
        _events, extract = await _run_frames(_frames_ctx(_never(), duration=29 * 60))

        extract.assert_awaited_once()

    async def test_should_set_frames_done_after_plan_done_when_the_run_skips(self) -> None:
        ctx = _frames_ctx(_landed(_probe("podcast", "interview")))
        wrapped = frames_done.then_frames_done(frames_phase.run_phase_frames)
        with (
            patch.object(settings, "SCENE_EXTRACTION_ENABLED", True),
            patch.object(settings, "FRAMES_SKIP_WHEN_NO_VISUAL_DEMO", True),
            patch.object(frames_done, "frames_done_step", MagicMock(return_value=[])),
        ):
            task = asyncio.create_task(_collect(wrapped(ctx)))
            await asyncio.sleep(0.05)
            held = ctx.frames_done.is_set()
            ctx.plan_done.set()
            await asyncio.wait_for(task, timeout=2)

        assert (held, ctx.frames_done.is_set()) == (False, True)


# ─── The moment fill ───


def _moment_tab(tab_id: str, count: int) -> dict:
    items = [{"label": f"m{i}", "seconds": 60 * i + 5} for i in range(count)]
    return {"id": tab_id, "component": "moment_track", "props": {"items": items}}


def _s3() -> MagicMock:
    s3 = MagicMock()
    s3.is_available = MagicMock(return_value=True)
    s3.exists = AsyncMock(return_value=False)
    s3.put_bytes = AsyncMock(return_value=None)
    s3.generate_presigned_url = MagicMock(side_effect=lambda key: f"https://s3/{key}")
    return s3


def _hires() -> MagicMock:
    return MagicMock(started=True, path=AsyncMock(return_value=Path("/tmp/vie-hires/yt.mp4")))


def _fill_ctx(tabs: list[dict], *, skipped: bool) -> Any:
    return SimpleNamespace(
        assembled_tabs=tabs,
        youtube_id="yt",
        hires_video=_hires(),
        frames_skipped=skipped,
        tab_ledger=TabLedger(),
    )


def _framed(tabs: list[dict]) -> int:
    return sum(1 for tab in tabs for item in tab["props"]["items"] if item.get("thumbnailUrl"))


async def _fill(ctx: Any) -> list[str]:
    with (
        patch.object(mff, "s3_client", _s3()),
        patch.object(mff, "extract_frame", AsyncMock(return_value=b"jpeg")),
    ):
        return await _collect(assembly_phase.run_phase_moment_fill(ctx))


class TestMomentFill:
    def test_should_lift_the_cap_when_the_run_skipped(self) -> None:
        assert (mff.fill_cap(True), mff.fill_cap(False)) == (None, mff._FILL_MAX_FRAMES)

    async def test_should_frame_every_moment_when_the_run_skipped_its_frames(self) -> None:
        tabs = [_moment_tab("timestamps", 18), _moment_tab("segments", 18)]

        await _fill(_fill_ctx(tabs, skipped=True))

        assert _framed(tabs) == 36

    async def test_should_keep_the_fill_cap_when_the_run_kept_its_frames(self) -> None:
        tabs = [_moment_tab("timestamps", 18), _moment_tab("segments", 18)]

        await _fill(_fill_ctx(tabs, skipped=False))

        assert _framed(tabs) == mff._FILL_MAX_FRAMES

    async def test_should_count_what_the_raised_fill_adds_when_the_720p_lands(self) -> None:
        tabs = [_moment_tab("timestamps", 18), _moment_tab("segments", 18)]
        cap = mff.fill_cap(True)
        with (
            patch.object(mff, "s3_client", _s3()),
            patch.object(mff, "extract_frame", AsyncMock(return_value=b"jpeg")),
        ):
            pending = sum(mff.pending_fill(tabs, _hires(), cap).values())  # type: ignore[arg-type]
            filled = await mff.fill_moment_frames(tabs, "yt", _hires(), cap)  # type: ignore[arg-type]

        assert pending == filled == 36
