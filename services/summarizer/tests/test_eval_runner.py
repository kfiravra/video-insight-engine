"""Tests for the golden eval runner (``scripts/run_eval.py`` + ``_eval_scoring``).

The runner lives in the repo-root ``scripts/`` directory (outside the
summarizer package), so that directory is put on ``sys.path``. The vie-api
is the only external dependency; it is replaced by a fake
``run_with_reauth`` — no network, no LLM spend.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import run_eval  # noqa: E402
from _eval_scoring import score_entry, stub_actual  # noqa: E402

# Captured before ``fake_api`` stubs ``asyncio.sleep``: fakes that must yield use it.
_real_sleep = asyncio.sleep


def _expected(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "test-vid",
        "domain": "tech",
        "expectedTabs": ["overview", "code", "patterns", "cheat_sheet"],
        "requiredComponents": ["overview", "code_playground"],
        "keyContent": ["useState", "useEffect", "render"],
    }
    base.update(overrides)
    return base


def _actual(tabs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"meta": {}, "tabs": tabs or []}


def _record(**overrides: Any) -> dict[str, Any]:
    base = {
        **_expected(),
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
        "format": "tutorial",
        "language": "en",
    }
    base.update(overrides)
    return base


# ─── score_entry ────────────────────────────────────────────────────────
class TestScoreEntry:
    def test_should_score_one_when_all_expectations_are_met(self) -> None:
        actual = _actual(
            [
                {
                    "id": "overview",
                    "component": "overview",
                    "props": {"items": [{"text": "useState useEffect render"}]},
                },
                {"id": "code", "component": "code_playground", "props": {"items": [{}]}},
                {"id": "patterns", "component": "info_grid", "props": {"items": [{}]}},
                {"id": "cheat_sheet", "component": "info_grid", "props": {"items": [{}]}},
            ]
        )
        assert score_entry(_expected(), actual).overall == 1.0

    def test_should_halve_component_coverage_when_one_of_two_is_missing(self) -> None:
        actual = _actual([{"id": "overview", "component": "overview", "props": {}}])
        assert score_entry(_expected(), actual).component_coverage == 0.5

    def test_should_zero_content_coverage_when_no_term_appears(self) -> None:
        actual = _actual(
            [{"id": "overview", "component": "overview", "props": {"items": [{"text": "x"}]}}]
        )
        assert score_entry(_expected(), actual).content_coverage == 0.0

    def test_should_lose_a_quarter_per_tab_when_tab_count_drifts(self) -> None:
        actual = _actual(
            [
                {"id": "overview", "component": "overview", "props": {}},
                {"id": "code", "component": "code_playground", "props": {}},
            ]
        )
        assert score_entry(_expected(), actual).tab_count_score == 0.5

    def test_should_count_an_empty_items_list_as_an_empty_tab(self) -> None:
        actual = _actual(
            [
                {"id": "overview", "component": "overview", "props": {"items": []}},
                {"id": "code", "component": "code_playground", "props": {}},
            ]
        )
        assert score_entry(_expected(), actual).empty_tab_count == 1

    def test_should_score_zero_coverage_when_response_has_no_tabs(self) -> None:
        result = score_entry(_expected(), _actual([]))
        assert (result.tab_count, result.component_coverage) == (0, 0.0)

    def test_should_cap_sub_scores_at_one_when_expectations_are_empty(self) -> None:
        expected = _expected(expectedTabs=[], requiredComponents=[], keyContent=[])
        result = score_entry(expected, _actual([]))
        assert (result.component_coverage, result.content_coverage) == (1.0, 1.0)

    def test_should_zero_forbidden_term_when_forbidden_component_present(self) -> None:
        actual = _actual(
            [
                {"id": "overview", "component": "overview", "props": {}},
                {"id": "quiz", "component": "quiz_arena", "props": {}},
            ]
        )
        result = score_entry(_expected(forbiddenComponents=["quiz_arena"]), actual)
        assert result.forbidden_ok == 0.0 and "quiz_arena" in result.notes

    def test_should_keep_full_forbidden_term_when_no_forbidden_hit(self) -> None:
        actual = _actual([{"id": "overview", "component": "overview", "props": {}}])
        expected = _expected(
            expectedTabs=["overview"],
            requiredComponents=["overview"],
            keyContent=[],
            forbiddenComponents=["quiz_arena"],
        )
        assert score_entry(expected, actual).overall == 1.0

    def test_should_satisfy_expectations_when_scoring_the_dry_run_stub(self) -> None:
        assert score_entry(_expected(), stub_actual(_expected())).overall >= 0.95


# ─── Dataset loading ───────────────────────────────────────────────────
class TestDataset:
    def test_should_load_28_entries_with_18_live_when_reading_the_golden_set(self) -> None:
        records = run_eval.load_dataset()
        live = [r for r in records if not r.get("disabled")]
        assert (len(records), len(live)) == (28, 18)

    def test_should_drop_disabled_entries_when_selecting_records(self) -> None:
        records = [_record(id="a"), _record(id="b", disabled=True), _record(id="c")]
        assert [r["id"] for r in run_eval.select_records(records)] == ["a", "c"]

    def test_should_apply_filter_then_limit_when_selecting_records(self) -> None:
        records = [_record(id="food-a"), _record(id="tech-b"), _record(id="food-c")]
        selected = run_eval.select_records(records, id_filter="food", limit=1)
        assert [r["id"] for r in selected] == ["food-a"]

    def test_should_reject_dataset_when_a_live_url_is_not_youtube(self, tmp_path: Path) -> None:
        path = tmp_path / "videos.yaml"
        bad = _record(url="http://localhost:8080/admin")
        path.write_text(json.dumps({"videos": [bad]}), encoding="utf-8")
        with pytest.raises(ValueError, match="not a YouTube URL"):
            run_eval.load_dataset(path)


# ─── Runner flow (fake vie-api) ────────────────────────────────────────
def _fake_doc(record: dict[str, Any]) -> dict[str, Any]:
    tabs = stub_actual(record)["tabs"]
    return {"meta": {}, "tabs": tabs, "duration": 300, "youtubeId": "abcdefghijk"}


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool]]:
    """Replace the vie-api call; records (url, bypass_cache) per submission."""
    calls: list[tuple[str, bool]] = []

    async def fake_run(
        api_url: str, url: str, token: str, bypass_cache: bool, **_: Any
    ) -> tuple[dict[str, Any], str]:
        calls.append((url, bypass_cache))
        return _fake_doc(_record()), token

    async def fake_auth(api_url: str) -> str:
        return "token"

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(run_eval, "run_with_reauth", fake_run)
    monkeypatch.setattr(run_eval, "authenticate", fake_auth)
    monkeypatch.setattr(run_eval.asyncio, "sleep", no_sleep)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    return calls


def _write_dataset(tmp_path: Path, records: list[dict[str, Any]]) -> Path:
    path = tmp_path / "videos.yaml"
    path.write_text(json.dumps({"videos": records}), encoding="utf-8")
    return path


class TestRunner:
    def test_should_bypass_cache_by_default_when_running_live(
        self, tmp_path: Path, fake_api: list[tuple[str, bool]]
    ) -> None:
        dataset = _write_dataset(tmp_path, [_record()])
        run_eval.main(["--dataset", str(dataset), "--output", str(tmp_path / "out")])
        assert fake_api == [(_record()["url"], True)]

    def test_should_not_bypass_cache_when_no_bypass_flag_is_set(
        self, tmp_path: Path, fake_api: list[tuple[str, bool]]
    ) -> None:
        dataset = _write_dataset(tmp_path, [_record()])
        argv = ["--dataset", str(dataset), "--output", str(tmp_path / "out")]
        run_eval.main([*argv, "--no-bypass-cache"])
        assert fake_api[0][1] is False

    def test_should_write_noise_file_when_running_the_set_twice(
        self, tmp_path: Path, fake_api: list[tuple[str, bool]]
    ) -> None:
        dataset = _write_dataset(tmp_path, [_record(id="a"), _record(id="b")])
        noise_path = tmp_path / "noise.json"
        argv = ["--dataset", str(dataset), "--output", str(tmp_path / "out")]
        run_eval.main([*argv, "--noise-runs", "2", "--noise-out", str(noise_path)])
        noise = json.loads(noise_path.read_text())
        assert (noise["runs"], sorted(noise["videos"]), len(fake_api)) == (2, ["a", "b"], 4)

    def test_should_fail_completed_assertion_when_pipeline_run_errors(self) -> None:
        run = run_eval.EntryRun(record=_record(), actual=None, error="pipeline failed: boom")
        outcome = run_eval.to_outcome(run, None, dry_run=False)
        assert [(a.type, a.passed) for a in outcome.assertions] == [("completed", False)]

    def test_should_report_quality_as_none_when_pipeline_run_errors(self) -> None:
        run = run_eval.EntryRun(record=_record(), actual=None, error="pipeline failed: boom")
        outcome = run_eval.to_outcome(run, None, dry_run=False)
        assert outcome.metrics()["quality"] is None

    def test_should_label_the_api_without_its_url_when_writing_the_summary(
        self, tmp_path: Path, fake_api: list[tuple[str, bool]]
    ) -> None:
        dataset = _write_dataset(tmp_path, [_record()])
        out = tmp_path / "out"
        secret = "https://eval:pw@secret-host.example/api"
        run_eval.main(["--dataset", str(dataset), "--output", str(out), "--api-url", secret])
        text = next(out.glob("eval-*.json")).read_text()
        assert "secret-host" not in text and '"apiLabel": "remote:' in text

    async def test_should_scrub_the_api_url_when_an_error_quotes_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = "https://secret-host.example"

        async def failing_run(*_: Any) -> tuple[dict[str, Any], str]:
            raise RuntimeError(f"Client error '500' for url '{secret}/api/videos'")

        monkeypatch.setattr(run_eval, "run_with_reauth", failing_run)
        session = run_eval.Session(api_url=secret, bypass_cache=True, dry_run=False, langfuse=None)
        run = await run_eval.run_entry(_record(), session)
        assert run.error is not None and "secret-host" not in run.error

    def test_should_rebuild_noise_from_existing_reports_when_noise_from_is_given(
        self, tmp_path: Path, fake_api: list[tuple[str, bool]]
    ) -> None:
        dataset = _write_dataset(tmp_path, [_record(id="a")])
        out = tmp_path / "out"
        argv = ["--dataset", str(dataset), "--output", str(out), "--noise-runs", "2"]
        run_eval.main([*argv, "--noise-out", str(tmp_path / "first.json")])
        reports = sorted(str(p) for p in out.glob("eval-*.json"))
        rebuilt = tmp_path / "rebuilt.json"
        run_eval.main(["--noise-from", *reports, "--noise-out", str(rebuilt)])
        noise = json.loads(rebuilt.read_text())
        assert (noise["reports"], noise["excludedVideos"], len(fake_api)) == (reports, [], 2)

    def test_should_select_only_quick_entries_when_subset_is_quick(self) -> None:
        records = [_record(id="a", quick=True), _record(id="b"), _record(id="c", quick=True)]
        selected = run_eval.select_records(records, subset="quick")
        assert [r["id"] for r in selected] == ["a", "c"]

    def test_should_select_one_entry_per_live_domain_when_subset_is_quick(self) -> None:
        records = run_eval.select_records(run_eval.load_dataset(), subset="quick")
        domains = [r["domain"] for r in records]
        assert sorted(domains) == sorted(set(domains)) and len(domains) >= 8

    def test_should_report_duplicate_rate_when_tabs_repeat_an_item(self) -> None:
        item = {"text": "whisk the eggs with the sugar until pale"}
        doc = {
            "tabs": [
                {"id": "a", "component": "step_player", "props": {"steps": [item]}},
                {"id": "b", "component": "checklist", "props": {"items": [item]}},
            ]
        }
        run = run_eval.EntryRun(record=_record(), actual=doc, error=None)
        assert run_eval.to_outcome(run, None, dry_run=False).duplicate_rate == 0.5

    def test_should_exit_nonzero_when_dry_run_average_is_below_floor(self, tmp_path: Path) -> None:
        dataset = _write_dataset(
            tmp_path,
            [_record(expectedTabs=["overview"], requiredComponents=["overview", "x", "y"])],
        )
        argv = ["--dataset", str(dataset), "--output", str(tmp_path / "out"), "--dry-run"]
        assert run_eval.main([*argv, "--fail-under", "0.99"]) == 1

    def test_should_refuse_a_single_noise_pass(self) -> None:
        with pytest.raises(SystemExit):
            run_eval.main(["--noise-runs", "1"])


# ─── Concurrency ───────────────────────────────────────────────────────
def _yt(i: int) -> str:
    return f"https://www.youtube.com/watch?v=vid{i:08d}"


class TestConcurrency:
    def test_should_keep_at_most_n_runs_in_flight_when_concurrency_is_n(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_api: list[tuple[str, bool]]
    ) -> None:
        in_flight, peak = 0, 0

        async def slow_run(*_: Any, **__: Any) -> tuple[dict[str, Any], str]:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await _real_sleep(0.01)
            in_flight -= 1
            return _fake_doc(_record()), "token"

        monkeypatch.setattr(run_eval, "run_with_reauth", slow_run)
        dataset = _write_dataset(tmp_path, [_record(id=f"v{i}", url=_yt(i)) for i in range(7)])
        argv = ["--dataset", str(dataset), "--output", str(tmp_path / "out")]
        run_eval.main([*argv, "--concurrency", "3"])
        assert peak == 3

    def test_should_order_report_rows_by_golden_id_when_runs_finish_out_of_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_api: list[tuple[str, bool]]
    ) -> None:
        delays = {_yt(0): 0.03, _yt(1): 0.0, _yt(2): 0.015}

        async def uneven_run(_api: str, url: str, *_: Any, **__: Any) -> tuple[dict[str, Any], str]:
            await _real_sleep(delays[url])
            return _fake_doc(_record()), "token"

        monkeypatch.setattr(run_eval, "run_with_reauth", uneven_run)
        records = [_record(id=vid, url=_yt(i)) for i, vid in enumerate(("c", "a", "b"))]
        out = tmp_path / "out"
        dataset = _write_dataset(tmp_path, records)
        run_eval.main(["--dataset", str(dataset), "--output", str(out), "--concurrency", "3"])
        summary = json.loads(next(out.glob("eval-*.json")).read_text())
        assert [v["id"] for v in summary["videos"]] == ["a", "b", "c"]

    def test_should_refuse_a_concurrency_below_one(self) -> None:
        with pytest.raises(SystemExit):
            run_eval.main(["--concurrency", "0"])


# ─── Resume (--resume-since) ───────────────────────────────────────────
class TestResume:
    async def test_should_not_post_a_video_when_a_completed_run_is_reused(
        self, fake_api: list[tuple[str, bool]]
    ) -> None:
        session = run_eval.Session(
            api_url="http://api", bypass_cache=True, dry_run=False, langfuse=None
        )
        reuse = {"vid00000000": _fake_doc(_record())}
        run = await run_eval.run_entry(_record(url=_yt(0)), session, reuse)
        assert (run.actual is not None, fake_api) == (True, [])

    def test_should_reuse_only_in_the_first_noise_pass_when_resuming(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_api: list[tuple[str, bool]]
    ) -> None:
        async def reusable(*_: Any) -> dict[str, dict[str, Any]]:
            return {"vid00000000": _fake_doc(_record())}

        monkeypatch.setattr(run_eval, "load_reusable_runs", reusable)
        dataset = _write_dataset(
            tmp_path, [_record(id="a", url=_yt(0)), _record(id="b", url=_yt(1))]
        )
        noise_path = tmp_path / "noise.json"
        argv = ["--dataset", str(dataset), "--output", str(tmp_path / "out"), "--noise-runs", "2"]
        run_eval.main(
            [*argv, "--noise-out", str(noise_path), "--resume-since", "2026-10-07T16:30:00+03:00"]
        )
        noise = json.loads(noise_path.read_text())
        posted = Counter(url for url, _ in fake_api)
        assert (posted, noise["schemaVersion"], sorted(noise["videos"])) == (
            Counter({_yt(0): 1, _yt(1): 2}),
            2,
            ["a", "b"],
        )

    def test_should_refuse_resume_when_since_has_no_utc_offset(self) -> None:
        with pytest.raises(SystemExit):
            run_eval.main(["--resume-since", "2026-10-07T16:30:00"])
