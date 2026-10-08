"""scripts/{spotcheck_frame_vision,benchmark_fast_models,_bench_quality_scorers}.py.

Both vision scripts imported the deleted ``VISION_ANALYSIS_PROMPT`` (and the
benchmark the long-gone ``_translate_json``), so they failed at import. The
scorers still weighed synthesis/enrichment fields the pipeline no longer
writes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scorers() -> ModuleType:
    return _load("_bench_quality_scorers")


def _frames(tmp_path: Path, count: int) -> list[dict]:
    frames = []
    for i in range(count):
        path = tmp_path / f"f{i}.jpg"
        path.write_bytes(b"\xff\xd8\xff")
        frames.append({"path": str(path), "s3_key": f"k{i}", "timestamp": float(i)})
    return frames


class TestVisionScripts:
    @pytest.mark.parametrize("script", ["spotcheck_frame_vision", "benchmark_fast_models"])
    def test_should_end_the_request_with_the_frame_count_line_when_building_messages(
        self, script: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = _load(script)
        monkeypatch.setattr(module, "load_vision_prompt", lambda: "VISION PROMPT")

        messages, _ = module._build_vision_messages(_frames(tmp_path, 2))

        content = messages[0]["content"]
        assert (content[0]["text"], content[-1]["text"]) == (
            "VISION PROMPT",
            "This request has 2 frames: Frame 0 to Frame 1. Return exactly 2 objects.",
        )

    def test_should_number_labels_by_frames_sent_when_one_fails_to_encode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = _load("spotcheck_frame_vision")
        monkeypatch.setattr(module, "load_vision_prompt", lambda: "VISION PROMPT")
        frames = _frames(tmp_path, 3)
        frames[0]["path"] = str(tmp_path / "missing.jpg")

        messages, _ = module._build_vision_messages(frames)

        texts = [block.get("text", "") for block in messages[0]["content"]]
        labels = [text for text in texts if text.startswith("Frame ")]
        assert labels == ["Frame 0 (at 0:01):", "Frame 1 (at 0:02):"]


class TestBenchmarkStages:
    @pytest.mark.parametrize(("tag", "runs"), [("learning", True), ("food", False), (None, True)])
    def test_should_run_enrichment_only_for_quiz_domains(self, tag: str | None, runs: bool) -> None:
        module = _load("benchmark_fast_models")

        assert module._stage_applies("enrichment", SimpleNamespace(matched_tag=tag)) is runs


class TestScorers:
    def test_should_score_synthesis_fully_when_tldr_and_takeaways_are_empty(
        self, scorers: ModuleType
    ) -> None:
        run = SimpleNamespace(
            tldr="", key_takeaways=[], master_summary="m" * 400, seo_description="s" * 40
        )

        assert scorers.score_synthesis(run, run) == 1.0

    def test_should_score_enrichment_by_quiz_count_alone(self, scorers: ModuleType) -> None:
        baseline = SimpleNamespace(quiz=[1, 2, 3, 4], flashcards=[1, 2], scenarios=None)
        candidate = SimpleNamespace(quiz=[1, 2, 3, 4], flashcards=None, scenarios=None)

        assert scorers.score_enrichment(baseline, candidate) == 1.0

    def test_should_score_enrichment_zero_when_the_candidate_writes_no_quiz(
        self, scorers: ModuleType
    ) -> None:
        baseline = SimpleNamespace(quiz=[1, 2, 3])
        candidate = SimpleNamespace(quiz=None)

        assert scorers.score_enrichment(baseline, candidate) == 0.0
