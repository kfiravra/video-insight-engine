"""Unit tests for the model A/B round 2's offline views (``scripts/_model_ab_r2_*.py``, 4.0d).

Both rounds' answers are hand-placed: the T1dQhQAm8Tc cassette's recorded
answers (each extraction group gets its slice of the recorded single call) on
the requests its capture records, as round-1 and round-2 rows. Scoring them
together, the memory run pairs and the reviewer dump are checked on those.
No network, no LLM, no spend.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from tests.replay.cassette import LLMEntry, load_cassette
from tests.replay.group_synth import recorded_answer, slice_answer

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from _model_ab_capture import capture_cassettes, group_from_json  # noqa: E402
from _model_ab_r2_review import dump_sizes, render_review_md, review_dump  # noqa: E402
from _model_ab_r2_rows import combined_rows, memory_pairs, round1_results  # noqa: E402
from _model_ab_r2_tables import render_raw, spend_of  # noqa: E402
from _model_ab_round2 import ROUND2_CALL_TYPES, request_info  # noqa: E402
from _model_ab_types import HAIKU_45, SONNET_46, SONNET_55, AbRequest, CallResult  # noqa: E402

VIDEO = "T1dQhQAm8Tc"


@pytest.fixture(scope="module")
def captured() -> list[AbRequest]:
    return [r for r in capture_cassettes((VIDEO,)) if r.call_type in ROUND2_CALL_TYPES]


@pytest.fixture(scope="module")
def entries() -> dict[str, LLMEntry]:
    return {e.key.span or e.key.feature: e for e in load_cassette(VIDEO).llm}


def _answer(request: AbRequest, entries: dict[str, LLMEntry]) -> str:
    if request.call_type != "extraction":
        return entries[request.call_type].output
    group = group_from_json(request.context["group"])
    keys = [(f.domain, f.field, f.kind) for f in group.fields]
    return json.dumps(slice_answer(recorded_answer(entries["extraction"]), keys))


def _row(request: AbRequest, raw: str, arm: str, model: str, repeat: int) -> CallResult:
    effort = "high" if arm == "B-high" else "medium" if arm == "B-medium" else "low"
    return CallResult(
        request.request_id,
        request.call_type,
        arm,
        model,
        effort,
        repeat,
        1_000,
        raw=raw,
        cost_usd=0.01,
    )


def _arms(call_type: str) -> tuple[list[tuple[str, str, int]], list[tuple[str, str, int]]]:
    """(round 1's, round 2's) (arm, model, repeat) per call type."""
    if call_type == "plan":
        return [("A", SONNET_46, 0), ("B-medium", SONNET_55, 0)], [("B-high", SONNET_55, 0)]
    if call_type == "memory":
        return [("A", HAIKU_45, 0)], [("A", HAIKU_45, 1), ("A", HAIKU_45, 2)]
    return [("A", HAIKU_45, 0)], [("A+rule", HAIKU_45, 0), ("A+rule", HAIKU_45, 1)]


@pytest.fixture
def stores(
    captured: list[AbRequest], entries: dict[str, LLMEntry], tmp_path: Path
) -> tuple[dict[str, Any], Path, Path]:
    """(request info, round-1 report, round-2 answers) holding the recorded answers."""
    first: list[CallResult] = []
    second: list[CallResult] = []
    for request in captured:
        raw = _answer(request, entries)
        earlier, later = _arms(request.call_type)
        first += [_row(request, raw, *arm) for arm in earlier]
        second += [_row(request, raw, *arm) for arm in later]
    round1 = tmp_path / "g4.json"
    round1.write_text(json.dumps({"rows": [asdict(r) for r in first]}), encoding="utf-8")
    answers = tmp_path / "answers.jsonl"
    answers.write_text("".join(json.dumps(asdict(r)) + "\n" for r in second), encoding="utf-8")
    return request_info(captured), round1, answers


def _rows(stores: tuple[dict[str, Any], Path, Path]) -> list[dict[str, Any]]:
    info, round1, answers = stores
    return combined_rows(info, round1_results(round1, frozenset(info)), answers)


class TestCombinedRows:
    def test_should_tag_each_answer_with_its_round_when_both_rounds_are_scored(
        self, stores: tuple[dict[str, Any], Path, Path]
    ) -> None:
        rounds = {
            (r["arm"], r["repeat"], r["round"]) for r in _rows(stores) if r["call_type"] == "memory"
        }
        assert rounds == {("A", 0, 1), ("A", 1, 2), ("A", 2, 2)}

    def test_should_compare_the_high_plan_with_the_medium_one_when_both_answered(
        self, stores: tuple[dict[str, Any], Path, Path]
    ) -> None:
        high = next(r for r in _rows(stores) if r["arm"] == "B-high")
        assert high["vsMedium"]["vsMedium_tabs_jaccard"] == 1.0

    def test_should_refuse_the_rows_when_round_2_answered_a_key_round_1_holds(
        self, stores: tuple[dict[str, Any], Path, Path]
    ) -> None:
        info, round1, answers = stores
        clashing = json.loads(round1.read_text(encoding="utf-8"))["rows"][0]
        with answers.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(clashing) + "\n")
        with pytest.raises(ValueError, match="round 1 holds"):
            combined_rows(info, round1_results(round1, frozenset(info)), answers)

    def test_should_pair_every_memory_run_when_a_cassette_has_three(
        self, stores: tuple[dict[str, Any], Path, Path]
    ) -> None:
        pairs = memory_pairs(_rows(stores))
        assert [(p["runs"], p["outline_delta"]) for p in pairs] == [
            (["0 (r1)", "1 (r2)"], 0),
            (["0 (r1)", "2 (r2)"], 0),
            (["1 (r2)", "2 (r2)"], 0),
        ]

    def test_should_count_only_round_2_calls_when_the_spend_is_split_per_arm(
        self, stores: tuple[dict[str, Any], Path, Path]
    ) -> None:
        spend = spend_of(_rows(stores), 0.12)
        assert (spend["calls"], sorted(spend["per_arm"])) == (
            11,
            ["extraction A+rule", "memory A", "plan B-high"],
        )

    def test_should_render_every_call_type_when_the_raw_tables_are_written(
        self, stores: tuple[dict[str, Any], Path, Path]
    ) -> None:
        rows = _rows(stores)
        markdown = render_raw(rows, spend_of(rows, 0.12))
        assert all(f"### {c}" in markdown for c in ROUND2_CALL_TYPES)


class TestReviewDump:
    def test_should_give_every_item_a_stable_id_when_the_dump_is_built(
        self, captured: list[AbRequest], stores: tuple[dict[str, Any], Path, Path]
    ) -> None:
        dump = review_dump(VIDEO, captured, _rows(stores))
        outputs = dump["fields"]["learning.concepts"]["outputs"]
        assert [o["output"] for o in outputs] == ["A.0", "A+rule.0", "A+rule.1"]
        assert outputs[1]["items"][0]["id"] == "A+rule.0.learning.concepts.0"

    def test_should_dump_only_list_fields_when_a_group_also_asks_for_scalars(
        self, captured: list[AbRequest], stores: tuple[dict[str, Any], Path, Path]
    ) -> None:
        dump = review_dump(VIDEO, captured, _rows(stores))
        assert sorted(dump["fields"]) == [
            "learning.concepts",
            "learning.timestamps",
            "tech.patterns",
            "tech.snippets",
            "tech.topics",
        ]

    def test_should_carry_the_transcript_and_the_frames_when_the_group_is_visual(
        self, captured: list[AbRequest], stores: tuple[dict[str, Any], Path, Path]
    ) -> None:
        dump = review_dump(VIDEO, captured, _rows(stores))
        visual = dump["fields"]["tech.snippets"]["visual_context"]
        assert (
            dump["transcript"].startswith("[0:"),
            visual.startswith("<visual_annotations>"),
        ) == (
            True,
            True,
        )

    def test_should_mark_an_output_unparsed_when_its_answer_is_not_json(
        self, captured: list[AbRequest], stores: tuple[dict[str, Any], Path, Path]
    ) -> None:
        rows = _rows(stores)
        for row in rows:
            if row["arm"] == "A+rule" and row["repeat"] == 1:
                row["raw"] = "not json"
        outputs = review_dump(VIDEO, captured, rows)["fields"]["tech.topics"]["outputs"]
        assert (outputs[-1]["parsed"], outputs[-1]["items"]) == (False, [])

    def test_should_list_every_item_id_when_the_dump_is_rendered(
        self, captured: list[AbRequest], stores: tuple[dict[str, Any], Path, Path]
    ) -> None:
        dump = review_dump(VIDEO, captured, _rows(stores))
        markdown = render_review_md(dump)
        ids = [i["id"] for f in dump["fields"].values() for o in f["outputs"] for i in o["items"]]
        assert (all(f"`{i}`" in markdown for i in ids), dump_sizes(dump)["items"]) == (
            True,
            len(ids),
        )
