"""Unit tests for the offline tier-probe A/B (``scripts/tier_probe_ab.py``, task 0.8).

Covers the pure parts only — annotation stripping, transcript windows, prompt
rendering, response parsing and scoring. No Docker, Mongo, network or LLM.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from src.services.pipeline.classifier import VALID_DOMAINS, VALID_FORMATS

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import tier_probe_ab as probe  # noqa: E402
from _tier_probe_inputs import GoldenVideo, ProbeInput, load_live_golden  # noqa: E402


def _input(transcript: str = "", **overrides: object) -> ProbeInput:
    base: dict[str, object] = {
        "youtube_id": "abc123",
        "title": "Knife skills",
        "channel": "Chef",
        "duration": 600,
        "youtube_category": "Howto & Style",
        "tags": ["knife", "cooking"],
        "description": "A video.",
        "transcript": transcript,
        "metadata_source": "summarizer",
        "transcript_source": "ytdlp-captions",
    }
    base.update(overrides)
    return ProbeInput(**base)


def _result(golden_id: str, model: str, raw: str, latency_ms: int = 1000) -> probe.ProbeResult:
    parsed, error = probe.parse_probe(raw)
    return probe.ProbeResult(
        golden_id, model, latency_ms, 0.001, raw=raw, parsed=parsed, parse_error=error
    )


def _raw(domain: str = "food", fmt: str = "tutorial", visual: bool = True) -> str:
    return json.dumps(
        {"domain": domain, "format": fmt, "has_visual_demo": visual, "confidence": 0.9}
    )


class TestStripVisualAnnotations:
    def test_should_drop_inline_visual_span_when_speech_follows(self) -> None:
        text = "add salt\n[VISUAL at 1:05: hand pours salt]then stir"
        assert probe.strip_visual_annotations(text) == "add salt then stir"

    def test_should_drop_whole_annotation_when_it_contains_brackets(self) -> None:
        text = "loop here\n[VISUAL at 2:00: code arr[i] = x]\nnext line"
        assert probe.strip_visual_annotations(text) == "loop here next line"

    def test_should_drop_on_screen_text_annotations(self) -> None:
        text = "intro [ON-SCREEN TEXT at 0:03: SUBSCRIBE] outro"
        assert probe.strip_visual_annotations(text) == "intro outro"

    def test_should_stop_at_line_end_when_annotation_is_unbalanced(self) -> None:
        text = "a\n[VISUAL at 0:01: broken [bracket\nspeech survives"
        assert probe.strip_visual_annotations(text) == "a speech survives"

    def test_should_keep_text_unchanged_when_no_annotation(self) -> None:
        assert probe.strip_visual_annotations("plain  speech\ntext") == "plain speech text"


class TestTranscriptWindows:
    def test_should_split_short_transcript_without_duplication(self) -> None:
        text = " ".join(f"w{i}" for i in range(100))
        start, mid, end = probe.transcript_windows(text, size=700)
        assert len(start) + len(mid) + len(end) <= len(text)

    def test_should_cap_each_window_when_transcript_is_long(self) -> None:
        text = " ".join(f"word{i}" for i in range(3000))
        windows = probe.transcript_windows(text, size=700)
        assert all(0 < len(w) <= 700 for w in windows)

    def test_should_anchor_windows_at_start_and_end(self) -> None:
        text = " ".join(f"word{i}" for i in range(3000))
        start, _mid, end = probe.transcript_windows(text, size=700)
        assert start.startswith("word0 ") and end.endswith("word2999")

    def test_should_exclude_annotation_text_from_windows(self) -> None:
        speech = " ".join(f"word{i}" for i in range(3000))
        text = speech[:5000] + "\n[VISUAL at 9:00: SECRET-FRAME]" + speech[5000:]
        assert "SECRET-FRAME" not in " ".join(probe.transcript_windows(text))


class TestRenderPrompt:
    def test_should_fill_every_placeholder(self) -> None:
        template = probe.PROMPT_PATH.read_text(encoding="utf-8")
        prompt = probe.render_prompt(template, _input("hello world " * 400))
        assert "{" + "window_mid}" not in prompt and "{title}" not in prompt

    def test_should_cap_description_at_500_chars(self) -> None:
        prompt = probe.render_prompt("D:{description}|", _input(description="x" * 2000))
        assert prompt == "D:" + "x" * 500 + "|"

    def test_should_sanitize_braces_and_tags_in_metadata(self) -> None:
        prompt = probe.render_prompt("T:{title}", _input(title="<b>{window_start}</b>"))
        assert prompt == "T:‹b›window_start‹/b›"

    def test_should_mark_missing_transcript(self) -> None:
        prompt = probe.render_prompt("{window_start}", _input(""))
        assert prompt == "(no transcript)"

    def test_template_should_carry_output_language_line(self) -> None:
        template = probe.PROMPT_PATH.read_text(encoding="utf-8")
        assert "<output_language>" in template and "English" in template


class TestParseProbe:
    def test_should_accept_bare_json(self) -> None:
        parsed, error = probe.parse_probe(_raw())
        assert error is None and parsed is not None and parsed["domain"] == "food"

    def test_should_accept_fenced_json_with_trailing_prose(self) -> None:
        raw = "```json\n" + _raw() + "\n```\n\n**Reasoning:** the video shows"
        assert probe.parse_probe(raw)[1] is None

    @pytest.mark.parametrize(
        ("raw", "fragment"),
        [
            ('{"domain": "entertainment", "format": "vlog"}', "invalid domain"),
            (_raw(fmt="essay"), "invalid format"),
            ('{"domain": "food", "format": "vlog", "has_visual_demo": "yes"}', "not a bool"),
            (_raw().replace("0.9", "1.5"), "confidence"),
            ("no json here", "no json object"),
            ('{"domain": "food", ', "invalid json"),
        ],
    )
    def test_should_reject_invalid_response(self, raw: str, fragment: str) -> None:
        parsed, error = probe.parse_probe(raw)
        assert parsed is None and error is not None and fragment in error

    def test_should_flag_fenced_response_as_not_bare(self) -> None:
        assert not probe.is_bare_json("```json\n" + _raw() + "\n```")

    def test_should_flag_plain_object_as_bare(self) -> None:
        assert probe.is_bare_json(_raw())


class TestScoring:
    def test_percentile_should_use_nearest_rank(self) -> None:
        assert probe.percentile([float(v) for v in range(1, 21)], 95) == 19.0

    def test_percentile_should_return_zero_when_empty(self) -> None:
        assert probe.percentile([], 50) == 0.0

    def test_summarize_should_score_agreement_and_failures(self) -> None:
        golden = [GoldenVideo("food-knife-skills", "a", "food", "tutorial")]
        golden.append(GoldenVideo("science-black-holes", "b", "science", "lecture"))
        results = [
            _result("food-knife-skills", "m", _raw()),
            _result("science-black-holes", "m", '{"domain": "entertainment"}'),
        ]
        summary = probe.summarize(results, golden)["m"]
        assert (summary["domain_pct"], summary["parse_failures"]) == (50.0, 1)

    def test_summarize_should_drop_excluded_golden_rows(self) -> None:
        golden = [GoldenVideo("food-knife-skills", "a", "food", "tutorial")]
        golden.append(GoldenVideo("review-airpods-pro", "b", "review", "commentary"))
        results = [
            _result("food-knife-skills", "m", _raw()),
            _result("review-airpods-pro", "m", _raw()),
        ]
        summary = probe.summarize(results, golden, frozenset({"review-airpods-pro"}))["m"]
        assert (summary["n"], summary["domain_pct"]) == (1, 100.0)

    def test_summarize_should_count_calls_over_the_3s_cap(self) -> None:
        golden = [GoldenVideo("food-knife-skills", "a", "food", "tutorial")]
        results = [_result("food-knife-skills", "m", _raw(), latency_ms=3200)]
        assert probe.summarize(results, golden)["m"]["over_3s"] == 1


class TestContracts:
    def test_valid_sets_should_match_the_classifier(self) -> None:
        assert (probe.VALID_DOMAINS, probe.VALID_FORMATS) == (VALID_DOMAINS, VALID_FORMATS)

    def test_every_live_golden_video_should_have_a_visual_label(self) -> None:
        live_ids = {video.golden_id for video in load_live_golden()}
        assert live_ids == set(probe.VISUAL_DEMO_LABELS)

    def test_load_results_should_reparse_saved_rows(self, tmp_path: Path) -> None:
        row = {"golden_id": "g", "model": "m", "latency_ms": 900, "cost_usd": 0.0}
        row |= {"raw": "```json\n" + _raw() + "\n```", "parse_error": "stale"}
        path = tmp_path / "results.json"
        path.write_text(json.dumps({"results": [row]}), encoding="utf-8")
        assert probe.load_results(path)[0].parse_error is None
