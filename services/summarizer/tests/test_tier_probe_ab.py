"""Unit tests for the offline tier-probe A/B (``scripts/tier_probe_ab.py``, task 0.8).

Covers the pure parts — prompt rendering through the pipeline's renderer,
response parsing, scoring — and the offline ``--rescore`` path. Annotation
stripping and transcript windows live in the pipeline (``test_tier_probe.py``).
No Docker, Mongo, network or LLM (external boundaries are patched to fail).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from src.services.pipeline.tier_probe import render_tier_probe_prompt

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import _tier_probe_inputs as inputs_mod  # noqa: E402
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
    parsed, error, issues = probe.parse_probe(raw)
    return probe.ProbeResult(
        golden_id,
        model,
        latency_ms,
        0.001,
        raw=raw,
        parsed=parsed,
        parse_error=error,
        field_errors=issues,
    )


def _raw(domain: str = "food", fmt: str = "tutorial", visual: bool = True) -> str:
    return json.dumps(
        {"domain": domain, "format": fmt, "has_visual_demo": visual, "confidence": 0.9}
    )


class TestRenderPrompt:
    def test_should_fill_every_placeholder(self) -> None:
        template = probe.PROMPT_PATH.read_text(encoding="utf-8")
        prompt = inputs_mod.render_prompt(template, _input("hello world " * 400))
        assert "{" + "window_mid}" not in prompt and "{title}" not in prompt

    def test_should_render_exactly_like_the_pipeline(self) -> None:
        template = probe.PROMPT_PATH.read_text(encoding="utf-8")
        item = _input("hello world " * 400, youtube_category="")
        expected = render_tier_probe_prompt(template, inputs_mod.to_probe_input(item))
        assert inputs_mod.render_prompt(template, item) == expected

    def test_should_render_the_shipped_pipeline_prompt(self) -> None:
        assert probe.PROMPT_PATH.parts[-3:] == ("src", "prompts", "tier_probe.txt")


class TestParseProbe:
    def test_should_accept_bare_json(self) -> None:
        parsed, error, issues = probe.parse_probe(_raw())
        assert (error, issues) == (None, []) and parsed is not None and parsed["domain"] == "food"

    def test_should_accept_fenced_json_with_trailing_prose(self) -> None:
        raw = "```json\n" + _raw() + "\n```\n\n**Reasoning:** the video shows"
        assert probe.parse_probe(raw)[1:] == (None, [])

    def test_should_normalise_enums_like_the_pipeline(self) -> None:
        parsed, _error, _issues = probe.parse_probe(_raw(domain=" Food ", fmt="TUTORIAL"))
        assert parsed is not None and (parsed["domain"], parsed["format"]) == ("food", "tutorial")

    def test_should_default_invalid_format_and_keep_other_fields(self) -> None:
        parsed, error, issues = probe.parse_probe(_raw(fmt="essay"))
        assert error is None and parsed is not None
        assert (parsed["format"], parsed["domain"], len(issues)) == ("commentary", "food", 1)

    def test_should_clamp_confidence_out_of_range(self) -> None:
        parsed, _error, issues = probe.parse_probe(_raw().replace("0.9", "1.5"))
        assert parsed is not None and (parsed["confidence"], issues) == (1.0, [])

    def test_should_null_invalid_domain_but_keep_visual(self) -> None:
        raw = '{"domain": "entertainment", "format": "vlog", "has_visual_demo": false}'
        parsed, error, issues = probe.parse_probe(raw)
        assert error is None and parsed is not None and issues
        assert (parsed["domain"], parsed["has_visual_demo"]) == (None, False)

    def test_should_null_non_bool_visual(self) -> None:
        raw = '{"domain": "food", "format": "vlog", "has_visual_demo": "yes"}'
        parsed, _error, issues = probe.parse_probe(raw)
        assert parsed is not None and parsed["has_visual_demo"] is None and issues

    @pytest.mark.parametrize(
        ("raw", "fragment"),
        [("no json here", "no json object"), ('{"domain": "food", ', "invalid json")],
    )
    def test_should_hard_fail_without_a_json_object(self, raw: str, fragment: str) -> None:
        parsed, error, _issues = probe.parse_probe(raw)
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

    def test_summarize_should_score_fields_independently(self) -> None:
        golden = [GoldenVideo("food-knife-skills", "a", "food", "tutorial")]
        golden.append(GoldenVideo("science-black-holes", "b", "science", "lecture"))
        bad_domain = '{"domain": "entertainment", "format": "lecture", "has_visual_demo": false}'
        results = [
            _result("food-knife-skills", "m", _raw()),
            _result("science-black-holes", "m", bad_domain),
        ]
        summary = probe.summarize(results, golden)["m"]
        assert (summary["domain_pct"], summary["format_pct"], summary["visual_pct"]) == (
            50.0,
            100.0,
            100.0,
        )

    def test_summarize_should_count_hard_parse_failures(self) -> None:
        golden = [GoldenVideo("food-knife-skills", "a", "food", "tutorial")]
        results = [_result("food-knife-skills", "m", "sorry, no json")]
        assert probe.summarize(results, golden)["m"]["parse_failures"] == 1

    def test_summarize_should_skip_rows_no_longer_live(self) -> None:
        golden = [GoldenVideo("food-knife-skills", "a", "food", "tutorial")]
        results = [_result("food-knife-skills", "m", _raw()), _result("gone", "m", _raw())]
        assert probe.summarize(results, golden)["m"]["n"] == 1

    def test_dropped_ids_should_name_rows_no_longer_live(self) -> None:
        golden = [GoldenVideo("food-knife-skills", "a", "food", "tutorial")]
        results = [_result("food-knife-skills", "m", _raw()), _result("gone", "m", _raw())]
        assert probe.dropped_ids(results, golden) == ["gone"]

    def test_summarize_should_count_errored_calls_in_the_latency_tail(self) -> None:
        golden = [GoldenVideo("food-knife-skills", "a", "food", "tutorial")]
        timeout = probe.ProbeResult("food-knife-skills", "m", 15000, 0.0, call_error="Timeout")
        summary = probe.summarize([timeout], golden)["m"]
        assert (summary["max_ms"], summary["over_3s"]) == (15000, 1)

    def test_summarize_should_withhold_p95_below_20_calls(self) -> None:
        golden = [GoldenVideo("food-knife-skills", "a", "food", "tutorial")]
        results = [_result("food-knife-skills", "m", _raw())] * 19
        assert probe.summarize(results, golden)["m"]["p95_ms"] is None

    def test_summarize_should_report_p95_from_20_calls(self) -> None:
        golden = [GoldenVideo("food-knife-skills", "a", "food", "tutorial")]
        results = [_result("food-knife-skills", "m", _raw(), latency_ms=ms) for ms in range(20)]
        assert probe.summarize(results, golden)["m"]["p95_ms"] == 18

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
    def test_every_live_golden_video_should_have_a_visual_label(self) -> None:
        live_ids = {video.golden_id for video in load_live_golden()}
        assert live_ids == set(probe.VISUAL_DEMO_LABELS)

    def test_load_results_should_reparse_saved_rows(self) -> None:
        row: dict[str, object] = {"golden_id": "g", "model": "m", "latency_ms": 900}
        row |= {"cost_usd": 0.0, "raw": "```json\n" + _raw() + "\n```", "parse_error": "stale"}
        assert probe.load_results([row])[0].parse_error is None


class TestRescore:
    @pytest.fixture
    def saved_report(self, tmp_path: Path) -> Path:
        row = {"golden_id": "food-knife-skills", "model": "m", "latency_ms": 900}
        row |= {"cost_usd": 0.001, "raw": _raw()}
        payload = {"inputs": {"food-knife-skills": {"transcript": "s3"}}, "results": [row]}
        path = tmp_path / "report.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    @pytest.fixture
    def no_external(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _external(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("rescore touched an external system")

        monkeypatch.setattr(probe, "gather_inputs", _external)
        monkeypatch.setattr(probe, "load_api_keys", _external)
        monkeypatch.setattr(inputs_mod.subprocess, "run", _external)

    @pytest.mark.usefixtures("no_external")
    def test_rescore_should_reuse_saved_inputs_offline(self, saved_report: Path) -> None:
        assert probe.main(["--out-json", str(saved_report), "--rescore"]) == 0
        saved = json.loads(saved_report.read_text(encoding="utf-8"))
        assert saved["inputs"] == {"food-knife-skills": {"transcript": "s3"}}

    @pytest.mark.usefixtures("no_external")
    def test_rescore_should_write_summary(self, saved_report: Path) -> None:
        probe.main(["--out-json", str(saved_report), "--rescore"])
        saved = json.loads(saved_report.read_text(encoding="utf-8"))
        assert saved["summary"]["all"]["m"]["domain_pct"] == 100.0

    def test_rescore_should_require_out_json(self) -> None:
        with pytest.raises(SystemExit):
            probe.main(["--rescore"])

    def test_live_run_should_require_cache_dir(self) -> None:
        with pytest.raises(SystemExit):
            probe.main([])


class TestMongoCaptions:
    def test_cached_input_should_take_captions_from_mongo(self) -> None:
        item = _input(frame_captions=['"stale'])
        refreshed = inputs_mod._with_mongo_captions(item, {"frameCaptions": ['say "hi" here']})
        assert refreshed.frame_captions == ['say "hi" here']

    def test_mongo_script_should_walk_fields_not_regex_the_json(self) -> None:
        assert (
            "collectCaptions" in inputs_mod._MONGO_JS and ".slice(15)" not in inputs_mod._MONGO_JS
        )
