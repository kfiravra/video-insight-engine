"""The frame/media orchestration runs as production code during a replay.

Only subprocesses, S3 and the scorer's CPU calls are faked (``media_fakes``),
so downloads, the HIGH-tier reselect and the tier decision are the code under
test — these assertions are what phase 1a/1b changes will move.
"""

from __future__ import annotations

import json

import pytest

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


@pytest.mark.parametrize("video_id", available_cassettes())
def test_tier_should_be_derived_by_production_code(cassettes: dict, video_id: str) -> None:
    video = cassettes[video_id].video
    assert derive_tier(video.category, video.title, video.tags[:6]) == cassettes[video_id].tier


def test_proxied_standard_run_should_download_like_prod(replays: dict) -> None:
    assert replays[_REFERENCE_VIDEO].downloads() == [
        ("lowres", "scene_detect"),
        ("720p", "prefetch"),
        ("720p", "moment_fill"),
    ]


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


def test_zero_frame_run_should_upload_no_manifest(replays: dict) -> None:
    assert replays[_ZERO_FRAMES_VIDEO].frame_manifest is None


def test_hires_should_upgrade_every_selected_frame(replays: dict, cassettes: dict) -> None:
    manifest = replays[_REFERENCE_VIDEO].frame_manifest or {}
    assert manifest.get("hiresCount") == len(cassettes[_REFERENCE_VIDEO].frames.selected)
