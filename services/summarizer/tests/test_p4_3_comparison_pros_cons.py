"""A review's comparison ships the reviewer's own pros and cons (output-quality 4.3, R10).

S07 extracted 14 pros and 7 cons; its comparison tab, planned on
``review.comparisons`` (a row list), shipped ``pros: [] cons: []``, and a tab planned
on ``review.pros`` (the playbook's own plan, a list of strings) assembled nothing. A
comparison tab planned on any review path now reads the review block's pros / cons —
still never derived from a row's ``winner`` (0b.9), and with no score the review does
not anchor (D13). When both tabs ship, the dedupe sheds the shared strings from the
rows tab (``COMPARISON_LIST_KEYS``). Validation counts a comparison's rows, pros and
cons alike, so the Pros & cons tab ships without rows instead of demoting to a grid.

Tab-dedupe hotfix (Kfir 2026-11-05): a review ships ONE comparison tab. The first one
planned keeps the component and carries the rows, the pros, the cons and the verdict
whichever review path it was planned on. A later comparison tab drops as
``duplicate_component``.
"""

from __future__ import annotations

from typing import Any

from src.services.pipeline.assembly import assemble_comparison, assemble_response
from src.services.pipeline.assembly.component_dedupe import DUPLICATE_COMPONENT
from src.services.pipeline.assembly.core import (
    DUPLICATE_ITEMS,
    _dedupe_tabs,
    _validate_assembled_props,
)

_PROS = ["Best sound Apple has made", "Six hours of battery with ANC on"]
_CONS = ["No lossless over Bluetooth"]
_ROWS = [
    {
        "feature": "Noise cancellation",
        "thisProduct": "same level",
        "competitor": "same level",
        "competitorName": "Sony WF-1000XM4",
        "winner": "tie",
    },
    {
        "feature": "Transparency",
        "thisProduct": "on a level by itself",
        "competitor": "worse",
        "competitorName": "Bose QuietComfort Earbuds II",
        "winner": "left",
    },
]


def _review(**extra: Any) -> dict:
    return {
        "review": {
            "product": "AirPods Pro 2",
            "pros": list(_PROS),
            "cons": list(_CONS),
            "comparisons": [dict(row) for row in _ROWS],
            **extra,
        }
    }


def _tab(source: str) -> dict:
    return {"id": source.replace(".", "_"), "component": "comparison", "dataSource": source}


def _shipped(props: dict | None) -> dict:
    assert props is not None
    return props


def _assemble(source: str, extraction: dict) -> dict:
    data = extraction["review"][source.split(".")[1]]
    return _shipped(assemble_comparison(_tab(source), data, extraction, None))


class TestProsConsFromTheReviewBlock:
    def test_should_ship_the_review_pros_and_cons_when_planned_on_review_comparisons(self):
        out = _assemble("review.comparisons", _review())

        assert (out["pros"], out["cons"]) == (_PROS, _CONS)

    def test_should_keep_the_rows_when_planned_on_review_comparisons(self):
        out = _assemble("review.comparisons", _review())

        assert [row["feature"] for row in out["comparisons"]] == [r["feature"] for r in _ROWS]

    def test_should_ship_pros_and_cons_when_planned_on_review_pros(self):
        out = _assemble("review.pros", _review())

        assert (out["pros"], out["cons"]) == (_PROS, _CONS)

    def test_should_ship_the_review_rows_when_planned_on_review_pros(self):
        out = _assemble("review.pros", _review())

        assert [row["feature"] for row in out["comparisons"]] == [r["feature"] for r in _ROWS]

    def test_should_ship_pros_and_cons_when_planned_on_review_cons(self):
        out = _assemble("review.cons", _review())

        assert (out["pros"], out["cons"]) == (_PROS, _CONS)

    def test_should_drop_blank_and_non_string_lines(self):
        extraction = _review()
        extraction["review"]["pros"] = ["Great ANC", "  ", None, {"text": "x"}]

        out = _assemble("review.comparisons", extraction)

        assert out["pros"] == ["Great ANC"]

    def test_should_not_read_the_review_block_for_a_tab_planned_outside_it(self):
        tab = {"id": "vs", "component": "comparison", "dataSource": "tech.comparisons"}

        out = _shipped(assemble_comparison(tab, [dict(r) for r in _ROWS], _review(), None))

        assert (out["pros"], out["cons"]) == ([], [])

    def test_should_return_none_when_the_review_states_no_pros_cons_or_rows(self):
        extraction = {"review": {"product": "X", "pros": [], "cons": [], "comparisons": []}}

        assert assemble_comparison(_tab("review.pros"), [], extraction, None) is None

    def test_should_never_derive_pros_from_winners_when_the_review_has_none(self):
        extraction = _review(pros=[], cons=[])

        out = _assemble("review.comparisons", extraction)

        assert (out["pros"], out["cons"]) == ([], [])


class TestNoDerivedScore:
    def test_should_ship_no_score_when_the_verdict_is_unanchored(self):
        extraction = _review(
            verdict={"badge": "recommended", "bottomLine": "Buy it", "score": 9},
            rating={"score": 9, "maxScore": 10},
        )

        verdict = _assemble("review.pros", extraction)["verdict"]

        assert {"score", "maxScore", "subScores", "badge"} & verdict.keys() == set()


class TestTwoComparisonTabsShipEachLineOnce:
    """The twin rule on two comparison props (a cached response's pair): the pros-only
    tab first, the rows tab after it."""

    def _settled(self) -> tuple[list[dict], list[dict]]:
        extraction = _review()
        pros_only = {"pros": list(_PROS), "cons": list(_CONS), "comparisons": []}
        tabs = [
            {**_tab("review.pros"), "props": pros_only},
            {**_tab("review.comparisons"), "props": _assemble("review.comparisons", extraction)},
        ]
        dropped: list[dict] = []
        _dedupe_tabs(tabs, dropped)
        return tabs, dropped

    def test_should_keep_the_pros_and_cons_on_the_first_tab(self):
        tabs, _ = self._settled()

        assert (tabs[0]["props"]["pros"], tabs[0]["props"]["cons"]) == (_PROS, _CONS)

    def test_should_shed_the_shared_pros_and_cons_from_the_rows_tab(self):
        tabs, _ = self._settled()

        assert (tabs[1]["props"]["pros"], tabs[1]["props"]["cons"]) == ([], [])

    def test_should_keep_the_rows_on_the_rows_tab(self):
        tabs, _ = self._settled()

        assert len(tabs[1]["props"]["comparisons"]) == len(_ROWS)

    def test_should_record_the_trim_as_duplicate_items(self):
        _, dropped = self._settled()

        assert [d["reason"] for d in dropped] == [DUPLICATE_ITEMS]


class TestValidationCountsProsAndCons:
    def test_should_keep_a_review_tab_that_ships_pros_and_cons_without_comparison_rows(self):
        props = {"pros": _PROS, "cons": [], "comparisons": []}

        assert _validate_assembled_props("comparison", props) is True

    def test_should_drop_a_comparison_with_no_rows_pros_or_cons(self):
        props = {"pros": [], "cons": [], "comparisons": []}

        assert _validate_assembled_props("comparison", props) is False

    def test_should_still_drop_a_spot_tab_with_no_spots(self):
        assert _validate_assembled_props("spot_explorer", {"spots": []}) is False


def _planned(*sources: str) -> dict[str, dict]:
    """``assemble_response`` over a review plan: Pros & cons tab id ``pros_cons``, the
    rows tab ``vs``, in the given order (``review.comparisons`` is the rows tab)."""
    tabs = [
        {"id": "vs" if source == "review.comparisons" else "pros_cons", "component": "comparison",
         "dataSource": source, "label": source}
        for source in sources
    ]  # fmt: skip
    triage = {"primaryTag": "review", "contentTags": ["review"], "tabs": tabs}
    result = assemble_response(triage, _review(), None, None)
    return {tab["id"]: tab for tab in result["tabs"]}


class TestTheReviewPlanEndToEnd:
    """A cached review plan (Pros & cons on ``review.pros``, then the rivals on
    ``review.comparisons``) through ``assemble_response``: one comparison tab ships."""

    def _result(self) -> dict:
        triage = {"primaryTag": "review", "contentTags": ["review"], "tabs": [
            {"id": "pros_cons", "component": "comparison", "dataSource": "review.pros",
             "label": "Pros & cons"},
            {"id": "vs", "component": "comparison", "dataSource": "review.comparisons",
             "label": "Vs"},
        ]}  # fmt: skip
        return assemble_response(triage, _review(), None, None)

    def test_should_ship_the_pros_and_cons_tab_as_a_comparison(self):
        tab = _planned("review.pros", "review.comparisons")["pros_cons"]

        assert (tab["component"], tab["props"]["pros"], tab["props"]["cons"]) == (
            "comparison",
            _PROS,
            _CONS,
        )

    def test_should_carry_the_rivals_rows_on_the_one_comparison_tab(self):
        props = _planned("review.pros", "review.comparisons")["pros_cons"]["props"]

        assert len(props["comparisons"]) == len(_ROWS)

    def test_should_drop_the_rivals_tab_as_a_duplicate_component(self):
        dropped = self._result()["dropped"]

        assert [(d["id"], d["reason"]) for d in dropped] == [("vs", DUPLICATE_COMPONENT)]


class TestOneComparisonTabWhateverTheOrder:
    """The rows tab planned first and a Pros & cons tab on the review block last: the
    rows tab keeps the component and ships rows, pros and cons; the other drops."""

    def test_should_ship_only_the_first_planned_comparison_tab(self):
        tabs = _planned("review.comparisons", "review")

        assert [t for t in tabs if tabs[t]["component"] == "comparison"] == ["vs"]

    def test_should_ship_rows_pros_and_cons_on_the_first_tab(self):
        props = _planned("review.comparisons", "review")["vs"]["props"]

        assert (len(props["comparisons"]), props["pros"], props["cons"]) == (
            len(_ROWS),
            _PROS,
            _CONS,
        )

    def test_should_ship_rows_pros_and_cons_on_a_lone_rows_tab(self):
        props = _planned("review.comparisons")["vs"]["props"]

        assert (len(props["comparisons"]), props["pros"], props["cons"]) == (
            len(_ROWS),
            _PROS,
            _CONS,
        )
