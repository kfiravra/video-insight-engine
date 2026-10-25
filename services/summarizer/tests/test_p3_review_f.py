"""Phase-3 review fixes, lane F — the talking-head frames skip (3.5) and its moment fill.

* A warm re-run keeps its cached frame manifest (review C2 = D1): no 360p download
  started means metadata found the frames cached, so the run is never skipped.
* A real skip starts the 720p download at once, so the moment fill never has to
  start it after ``complete``.
* The skip waits for the probe while the 360p file is still on its way (C3), and only
  a probe confident above the plan's bar can skip a run (C4).
* "booster" / "case opening" / "crate opening" titles stay HIGH (C5).
* A skip run frames every moment of every moment tab (C7).
* ``.env.example`` lists every output-quality rollback lever (C9).

Faked boundaries: the probe task, the downloads, scene extraction, S3 and the ffmpeg seek.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import settings
from src.models.probe_types import TierProbe
from src.services.media import hires_prefetch, visual_tier
from src.services.media.hires_prefetch import LocalHiresSource, LocalLowresSource
from src.services.media.visual_tier import derive_tier, metadata_tier, skips_frames
from src.services.pipeline.assembly import moment_frame_fill as mff
from src.services.pipeline.phases import assembly as assembly_phase
from src.services.pipeline.phases import frames as frames_phase
from src.services.pipeline.phases.triage import PROBE_CONFIDENCE_THRESHOLD
from src.services.pipeline.tab_ledger import TabLedger

_LONG = 4978.0  # S02, 83 min
_S09 = "Opening an OP-17 (The World's Strongest Warriors) One Piece Booster Box!"


def _probe(domain: str = "podcast", fmt: str = "interview", confidence: float = 0.95) -> TierProbe:
    return TierProbe(domain=domain, format=fmt, has_visual_demo=False, confidence=confidence)


def _landed(probe: TierProbe | None) -> asyncio.Future[TierProbe | None]:
    future: asyncio.Future[TierProbe | None] = asyncio.get_running_loop().create_future()
    future.set_result(probe)
    return future


def _later(probe: TierProbe, delay: float) -> asyncio.Future[TierProbe | None]:
    future: asyncio.Future[TierProbe | None] = asyncio.get_running_loop().create_future()
    asyncio.get_running_loop().call_later(delay, future.set_result, probe)
    return future


class _Lowres:
    """The run's 360p download as metadata left it; ``path()`` waits until it lands."""

    def __init__(self, *, started: bool = True, landed: bool = False) -> None:
        self.started = started
        self.close = AsyncMock()
        self._landed = asyncio.Event()
        if landed:
            self._landed.set()

    def land(self) -> None:
        self._landed.set()

    async def path(self) -> Path:
        await self._landed.wait()
        return Path("/tmp/yt-360p.mp4")


def _ctx(probe_task: Any, lowres: Any, hires: Any = None) -> Any:
    return SimpleNamespace(
        youtube_id="yt",
        video_data=SimpleNamespace(title="Max Tegmark: Life 3.0", duration=_LONG, context=None),
        tier_probe_task=probe_task,
        lowres_video=lowres,
        hires_video=hires,
        frame_descriptions=[],
        frames_skipped=False,
    )


async def _run_frames(ctx: Any) -> AsyncMock:
    extract = AsyncMock(return_value={"all_frames": [], "selected_frames": []})
    with (
        patch.object(settings, "SCENE_EXTRACTION_ENABLED", True),
        patch.object(settings, "FRAMES_SKIP_WHEN_NO_VISUAL_DEMO", True),
        patch.object(frames_phase, "extract_scene_keyframes", extract),
    ):
        stream = frames_phase.run_phase_frames(ctx)
        await asyncio.wait_for(_drain(stream), timeout=2)
    return extract


async def _drain(stream: Any) -> None:
    async for _event in stream:
        pass


# ─── C2 = D1: a cached manifest, and the 720p on a real skip ───


class TestCachedFrames:
    async def test_should_extract_the_cached_frames_when_no_360p_download_was_started(
        self,
    ) -> None:
        extract = await _run_frames(_ctx(_landed(_probe()), _Lowres(started=False)))

        extract.assert_awaited_once()

    async def test_should_not_mark_the_run_skipped_when_its_frames_are_cached(self) -> None:
        ctx = _ctx(_landed(_probe()), _Lowres(started=False))

        await _run_frames(ctx)

        assert ctx.frames_skipped is False

    async def test_should_start_the_720p_download_when_the_run_skips(self) -> None:
        hires = LocalHiresSource("yt")
        ctx = _ctx(_landed(_probe()), _Lowres(landed=True), hires)
        with patch.object(hires_prefetch, "download_video_720p", AsyncMock(return_value=None)):
            await _run_frames(ctx)
            started = hires.started
            await hires.close()

        assert started is True


# ─── C3: the probe wait lasts while the 360p is on its way ───


class TestProbeWait:
    async def test_should_skip_when_the_probe_lands_past_the_plan_wait_before_the_360p(
        self,
    ) -> None:
        ctx = _ctx(_later(_probe(), 0.2), _Lowres())

        with patch.object(frames_phase, "PLAN_PROBE_WAIT_SECONDS", 0.05):
            await _run_frames(ctx)

        assert ctx.frames_skipped is True

    async def test_should_extract_frames_when_the_360p_lands_and_the_probe_never_does(
        self,
    ) -> None:
        lowres = _Lowres()
        never: asyncio.Future[TierProbe | None] = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_later(0.05, lowres.land)

        with patch.object(frames_phase, "PLAN_PROBE_WAIT_SECONDS", 0.1):
            extract = await _run_frames(_ctx(never, lowres))

        extract.assert_awaited_once()

    async def test_should_leave_the_360p_download_running_when_the_wait_ends(self) -> None:
        release = asyncio.Event()

        async def slow_download(_youtube_id: str, _timeout: float) -> None:
            await release.wait()

        with patch.object(hires_prefetch, "download_video_lowres", slow_download):
            lowres = LocalLowresSource("yt")
            lowres.start()
            demo = TierProbe(domain="podcast", format="talk", has_visual_demo=True, confidence=0.9)
            await _run_frames(_ctx(_landed(demo), lowres))
            running = lowres.started and not lowres.finished
            release.set()
            await lowres.close()

        assert running is True


# ─── C4: only a confident probe skips ───


class TestProbeConfidence:
    @pytest.mark.parametrize("confidence", [0.4, 0.6])
    def test_should_keep_frames_when_the_probe_is_not_confident(self, confidence: float) -> None:
        probe = _probe(confidence=confidence)

        assert skips_frames(probe, derive_tier(probe, "Max Tegmark: Life 3.0"), _LONG) is False

    def test_should_skip_when_the_probe_is_confident_above_the_plans_bar(self) -> None:
        probe = _probe(confidence=0.61)

        assert skips_frames(probe, derive_tier(probe, "Max Tegmark: Life 3.0"), _LONG) is True

    def test_should_use_the_plans_confidence_bar_when_deciding_a_skip(self) -> None:
        assert visual_tier.SKIP_MIN_PROBE_CONFIDENCE == PROBE_CONFIDENCE_THRESHOLD


# ─── C5: booster / case / crate openings stay HIGH ───


class TestOpeningKeywords:
    def test_should_keep_the_s09_title_high_when_there_is_no_probe(self) -> None:
        assert metadata_tier(None, _S09) == "high"

    def test_should_keep_the_s09_title_high_when_the_probe_says_commentary(self) -> None:
        assert derive_tier(_probe("gaming", "commentary"), _S09) == "high"

    @pytest.mark.parametrize(
        "title", ["Opening 36 Pokemon Booster Packs", "CS2 Case Opening", "Rust crate opening"]
    )
    def test_should_rank_an_opening_title_high_when_it_names_what_is_opened(
        self, title: str
    ) -> None:
        assert metadata_tier(None, title) == "high"


# ─── C7: a skip run frames every moment of every moment tab ───


def _moment_tab(tab_id: str, count: int, offset: int) -> dict:
    items = [{"label": f"m{i}", "seconds": offset + 60 * i + 5} for i in range(count)]
    return {"id": tab_id, "component": "moment_track", "props": {"items": items}}


def _two_full_tabs() -> list[dict]:
    """A 4 h podcast: Q&A and segments, 40 moments each (the > 90 min band)."""
    return [_moment_tab("qa", 40, 0), _moment_tab("segments", 40, 30)]


def _s3() -> MagicMock:
    s3 = MagicMock()
    s3.is_available = MagicMock(return_value=True)
    s3.exists = AsyncMock(return_value=False)
    s3.put_bytes = AsyncMock(return_value=None)
    s3.generate_presigned_url = MagicMock(side_effect=lambda key: f"https://s3/{key}")
    return s3


def _hires() -> MagicMock:
    return MagicMock(
        started=True, finished=True, path=AsyncMock(return_value=Path("/tmp/vie-hires/yt.mp4"))
    )


def _framed(tabs: list[dict]) -> int:
    return sum(1 for tab in tabs for item in tab["props"]["items"] if item.get("thumbnailUrl"))


def _skip_run_ctx(tabs: list[dict]) -> Any:
    return SimpleNamespace(
        assembled_tabs=tabs,
        youtube_id="yt",
        hires_video=_hires(),
        frames_skipped=True,
        tab_ledger=TabLedger(),
    )


class TestSkipRunFill:
    async def test_should_frame_both_moment_tabs_when_a_skip_run_has_80_moments(self) -> None:
        tabs = _two_full_tabs()
        ctx = _skip_run_ctx(tabs)
        with (
            patch.object(mff, "s3_client", _s3()),
            patch.object(mff, "extract_frame", AsyncMock(return_value=b"jpeg")),
        ):
            await _drain(assembly_phase.run_phase_moment_fill(ctx))

        assert _framed(tabs) == 80

    def test_should_count_every_moment_as_pending_when_a_skip_run_has_80_moments(self) -> None:
        with patch.object(mff, "s3_client", _s3()):
            pending = mff.pending_fill(_two_full_tabs(), _hires(), mff.fill_cap(True))

        assert pending == {"qa": 40, "segments": 40}


# ─── C9: the template lists every rollback lever ───


def _env_template() -> str:
    path = Path(__file__).resolve().parents[3] / ".env.example"
    if not path.is_file():
        pytest.skip(".env.example is not available here (container run)")
    return path.read_text()


@pytest.mark.parametrize(
    "lever",
    [
        "HERO_REWRITE_ENABLED",
        "QUIZ_V2_ENABLED",
        "MERGE_RANK_ENABLED",
        "FRAMES_SKIP_WHEN_NO_VISUAL_DEMO",
    ],
)
def test_should_list_the_rollback_lever_when_an_operator_copies_the_env_template(
    lever: str,
) -> None:
    assert f"# {lever}=true" in _env_template().splitlines()
