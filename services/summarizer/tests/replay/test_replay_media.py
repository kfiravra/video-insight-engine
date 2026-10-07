"""The frame/media orchestration runs as production code during a replay.

Only subprocesses, S3 and the scorer's CPU calls are faked (``media_fakes``),
so downloads, the HIGH-tier reselect and the tier decision are the code under
test — these assertions are what phase 1a/1b changes will move.
"""

from __future__ import annotations

import json

import pytest

from src.models.probe_types import TierProbe
from src.services.media.visual_tier import derive_tier
from src.services.pipeline.phases.frames import _is_presenter_frame
from tests.replay.cassette import Cassette, available_cassettes
from tests.replay.driver import ReplayResult

_REFERENCE_VIDEO = "T1dQhQAm8Tc"
_HIGH_VIDEO = "jMq8lEu-of0"
_ZERO_FRAMES_VIDEO = "uC45_4nnEAI"


def _replayed_selection(result: ReplayResult) -> list[int]:
    return [f["index"] for f in (result.frame_manifest or {}).get("frames", [])]


def _vision_kept(cassette: Cassette) -> set[int]:
    """Candidates the recorded vision output does NOT mark as presenter filler."""
    entry = next(e for e in cassette.llm if e.key.span == "frame_vision")
    by_rank = sorted(cassette.frames.candidates, key=lambda f: -f.total_score)
    described = {by_rank[d["frame_index"]].index: d for d in json.loads(entry.output)}
    return {
        f.index for f in cassette.frames.candidates if not _is_presenter_frame(vars(f), described)
    }


def _probe(cassette: Cassette) -> TierProbe:
    entry = next(e for e in cassette.llm if e.key.span == "tier_probe")
    return TierProbe.model_validate(json.loads(entry.output))


@pytest.mark.parametrize("video_id", available_cassettes())
def test_tier_should_be_derived_from_the_probe_by_production_code(
    cassettes: dict, video_id: str
) -> None:
    cassette = cassettes[video_id]
    assert derive_tier(_probe(cassette), cassette.video.title) == cassette.tier


@pytest.mark.parametrize("video_id", available_cassettes())
def test_frames_should_wait_for_the_tier_at_step_6b(replays: dict, video_id: str) -> None:
    """1b.1: the tier is decided at Step 6b (probe ≤ 3 s, else metadata), not at frames start."""
    assert "frames.tier_wait" in replays[video_id].phase_walls()


@pytest.mark.parametrize("video_id", available_cassettes())
def test_should_download_one_720p_file_per_run(replays: dict, video_id: str) -> None:
    """1a.2: the prefetch serves hi-res frames AND moment fill — no refiner or
    moment-fill re-download (the recorded runs fetched 720p twice)."""
    assert sorted(replays[video_id].downloads()) == [
        ("720p", "prefetch"),
        ("lowres", "scene_detect"),
    ]


def test_moment_fill_should_seek_the_kept_720p_file(replays: dict) -> None:
    """T1dQhQAm8Tc still has frameless moments after injection: moment fill
    fills them from the prefetched file instead of downloading again."""
    tabs = (replays[_REFERENCE_VIDEO].saved_result or {}).get("tabs", [])
    keys = [
        item.get("s3Key", "")
        for tab in tabs
        if tab.get("component") == "moment_track"
        for item in tab["props"]["items"]
    ]
    assert any("/frames/" in key for key in keys)


def test_standard_selection_should_match_recording(replays: dict, cassettes: dict) -> None:
    recorded = [f.index for f in cassettes[_REFERENCE_VIDEO].frames.selected]
    assert _replayed_selection(replays[_REFERENCE_VIDEO]) == recorded


def test_high_tier_selection_should_come_from_vision_kept_frames(
    replays: dict, cassettes: dict
) -> None:
    selection = set(_replayed_selection(replays[_HIGH_VIDEO]))
    assert selection and selection <= _vision_kept(cassettes[_HIGH_VIDEO])


def test_high_tier_selection_should_keep_recorded_size(replays: dict, cassettes: dict) -> None:
    recorded = len(cassettes[_HIGH_VIDEO].frames.selected)
    assert len(_replayed_selection(replays[_HIGH_VIDEO])) == recorded


def test_high_tier_should_time_vision_reselect(replays: dict) -> None:
    assert "frames.vision_reselect" in replays[_HIGH_VIDEO].phase_walls()


def test_zero_candidate_run_should_upload_ladder_frames(replays: dict) -> None:
    """C21/D17: the static-camera benchmark gets frames from the 1a.3 ladder."""
    manifest = replays[_ZERO_FRAMES_VIDEO].frame_manifest or {}
    assert len(manifest.get("frames", [])) > 0


def test_zero_candidate_run_should_time_the_ladder(replays: dict) -> None:
    assert "frames.scene_ladder" in replays[_ZERO_FRAMES_VIDEO].phase_walls()


def test_zero_candidate_run_should_show_moment_images(replays: dict) -> None:
    tabs = (replays[_ZERO_FRAMES_VIDEO].saved_result or {}).get("tabs", [])
    moments = [t for t in tabs if t.get("component") == "moment_track"]
    assert moments and all(
        any(item.get("thumbnailUrl") for item in t["props"]["items"]) for t in moments
    )


def test_hires_should_upgrade_every_selected_frame(replays: dict, cassettes: dict) -> None:
    manifest = replays[_REFERENCE_VIDEO].frame_manifest or {}
    assert manifest.get("hiresCount") == len(cassettes[_REFERENCE_VIDEO].frames.selected)
