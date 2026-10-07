"""Tests for folding runs into eval reports and re-syncing them (``scripts/_eval_merge.py``).

``run_eval.py --ids … --merge-into …`` and ``--resync`` drive the real
runner against a fake vie-api (no network, no LLM spend); ``rescore`` must
reproduce ``score_entry`` exactly from a stored report row.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import run_eval  # noqa: E402
from _eval_scoring import rescore, score_entry  # noqa: E402


def _record(vid: str, **overrides: Any) -> dict[str, Any]:
    base = {
        "id": vid,
        "url": f"https://www.youtube.com/watch?v={vid:0>11}",
        "domain": "tech",
        "format": "tutorial",
        "language": "en",
        "expectedTabs": ["overview", "code"],
        "requiredComponents": ["overview", "code_playground"],
        "keyContent": ["render"],
    }
    base.update(overrides)
    return base


_DOC = {
    "meta": {},
    "duration": 300,
    "tabs": [
        {"id": "overview", "component": "overview", "props": {"items": [{"text": "render"}]}},
        {"id": "code", "component": "code_playground", "props": {"items": []}},
    ],
}


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the vie-api call; records the URL of every submission."""
    posted: list[str] = []

    async def fake_run(
        _api: str, url: str, token: str, *_: Any, **__: Any
    ) -> tuple[dict[str, Any], str]:
        posted.append(url)
        return dict(_DOC), token

    async def fake_auth(_api: str) -> str:
        return "token"

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(run_eval, "run_with_reauth", fake_run)
    monkeypatch.setattr(run_eval, "authenticate", fake_auth)
    monkeypatch.setattr(run_eval.asyncio, "sleep", no_sleep)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    return posted


def _dataset(tmp_path: Path, records: list[dict[str, Any]]) -> str:
    path = tmp_path / "videos.yaml"
    path.write_text(json.dumps({"videos": records}), encoding="utf-8")
    return str(path)


def _baseline(tmp_path: Path, records: list[dict[str, Any]]) -> list[str]:
    """Two noise passes over ``records``; returns the r1/r2 report paths."""
    out = tmp_path / "base"
    argv = ["--dataset", _dataset(tmp_path, records), "--output", str(out)]
    run_eval.main([*argv, "--noise-runs", "2", "--noise-out", str(tmp_path / "base.json")])
    return sorted(str(p) for p in out.glob("eval-*.json"))


def _rows(path: str) -> dict[str, dict[str, Any]]:
    return {v["id"]: v for v in json.loads(Path(path).read_text())["videos"]}


# ─── rescore ───────────────────────────────────────────────────────────
class TestRescore:
    def test_should_reproduce_score_entry_when_expectations_are_unchanged(self) -> None:
        stored = score_entry(_record("a"), _DOC).as_dict()
        components = [t["component"] for t in _DOC["tabs"]]
        assert rescore(_record("a"), stored, components).as_dict() == stored

    def test_should_apply_edited_required_components_when_rescoring_a_row(self) -> None:
        stored = score_entry(_record("a", requiredComponents=["quiz_arena"]), _DOC).as_dict()
        components = [t["component"] for t in _DOC["tabs"]]
        edited = rescore(_record("a"), stored, components)
        assert (stored["component_coverage"], edited.component_coverage) == (0.0, 1.0)


# ─── --merge-into ──────────────────────────────────────────────────────
class TestMergeInto:
    def test_should_add_the_new_video_rows_to_each_baseline_pass(
        self, tmp_path: Path, fake_api: list[str]
    ) -> None:
        reports = _baseline(tmp_path, [_record("a")])
        dataset = _dataset(tmp_path, [_record("a"), _record("c")])
        argv = ["--dataset", dataset, "--output", str(tmp_path / "new"), "--ids", "c"]
        run_eval.main(
            [*argv, "--noise-runs", "2", "--merge-into", *reports, "--noise-out", "/dev/null"]
        )
        assert [sorted(_rows(r)) for r in reports] == [["a", "c"], ["a", "c"]]

    def test_should_run_only_the_selected_ids_when_merging(
        self, tmp_path: Path, fake_api: list[str]
    ) -> None:
        reports = _baseline(tmp_path, [_record("a")])
        fake_api.clear()
        dataset = _dataset(tmp_path, [_record("a"), _record("c")])
        argv = ["--dataset", dataset, "--output", str(tmp_path / "new"), "--ids", "c"]
        run_eval.main(
            [*argv, "--noise-runs", "2", "--merge-into", *reports, "--noise-out", "/dev/null"]
        )
        assert fake_api == [_record("c")["url"]] * 2

    def test_should_rebuild_noise_from_the_merged_reports_without_retired_ids(
        self, tmp_path: Path, fake_api: list[str]
    ) -> None:
        reports = _baseline(tmp_path, [_record("a"), _record("b")])
        dataset = _dataset(tmp_path, [_record("a"), _record("b", disabled=True), _record("c")])
        noise_path = tmp_path / "noise.json"
        argv = ["--dataset", dataset, "--output", str(tmp_path / "new"), "--ids", "c"]
        run_eval.main(
            [*argv, "--noise-runs", "2", "--merge-into", *reports, "--noise-out", str(noise_path)]
        )
        noise = json.loads(noise_path.read_text())
        assert (noise["reports"], sorted(noise["videos"]), noise["retiredVideos"]) == (
            reports,
            ["a", "c"],
            ["b"],
        )

    def test_should_keep_a_backup_of_each_merged_report(
        self, tmp_path: Path, fake_api: list[str]
    ) -> None:
        reports = _baseline(tmp_path, [_record("a")])
        original = Path(reports[0]).read_text()
        dataset = _dataset(tmp_path, [_record("a"), _record("c")])
        argv = ["--dataset", dataset, "--output", str(tmp_path / "new"), "--ids", "c"]
        run_eval.main(
            [*argv, "--noise-runs", "2", "--merge-into", *reports, "--noise-out", "/dev/null"]
        )
        assert Path(reports[0] + ".bak").read_text() == original

    def test_should_refuse_a_merge_when_reports_do_not_match_the_passes(self) -> None:
        with pytest.raises(SystemExit):
            run_eval.main(["--ids", "c", "--noise-runs", "2", "--merge-into", "r1.json"])

    def test_should_refuse_a_merge_without_ids(self) -> None:
        with pytest.raises(SystemExit):
            run_eval.main(["--merge-into", "r1.json"])


# ─── --resync ──────────────────────────────────────────────────────────
class TestResync:
    def test_should_rescore_stored_rows_when_expectations_change(
        self, tmp_path: Path, fake_api: list[str]
    ) -> None:
        reports = _baseline(tmp_path, [_record("a", requiredComponents=["quiz_arena"])])
        relabelled = _record("a", domain="science")
        run_eval.main(["--dataset", _dataset(tmp_path, [relabelled]), "--resync", reports[0]])
        row = _rows(reports[0])["a"]
        assert (row["domain"], row["quality"]["component_coverage"], fake_api) == (
            "science",
            1.0,
            [_record("a")["url"]] * 2,
        )

    def test_should_write_the_dataset_xfail_marker_into_stored_assertions(
        self, tmp_path: Path, fake_api: list[str]
    ) -> None:
        check = {"type": "requiredComponents", "components": ["step_player"]}
        reports = _baseline(tmp_path, [_record("a", assertions=[check])])
        marked = {**check, "xfail": {"reason": "plan sees 3,000 chars", "until": "1b.2"}}
        dataset = _dataset(tmp_path, [_record("a", assertions=[marked])])
        run_eval.main(["--dataset", dataset, "--resync", reports[0]])
        stored = _rows(reports[0])["a"]["assertions"][1]
        assert (stored["passed"], stored["xfail"], stored["until"]) == (
            False,
            "plan sees 3,000 chars",
            "1b.2",
        )

    def test_should_show_xfail_with_its_task_in_the_markdown_report(
        self, tmp_path: Path, fake_api: list[str]
    ) -> None:
        check = {
            "type": "requiredComponents",
            "components": ["step_player"],
            "xfail": {"reason": "plan sees 3,000 chars", "until": "1b.2"},
        }
        reports = _baseline(tmp_path, [_record("a", assertions=[check])])
        markdown = Path(reports[0]).with_suffix(".md").read_text()
        assert "**XFAIL** `a` requiredComponents" in markdown and "until 1b.2" in markdown
