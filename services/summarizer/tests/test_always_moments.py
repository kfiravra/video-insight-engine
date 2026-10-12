"""A Moments tab always exists (Kfir 2026-10-12, pipeline-1min 2.1).

No planned moment_track → assembly adds the fallback-chain tab (memory outline
→ YouTube chapters → description timestamps) after the planned tabs, the quiz
still last; no source → no tab (one is never invented). The plan-done
provisional copy carries the id and label of the tab that replaces it.
"""

from __future__ import annotations

from unittest.mock import patch

from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly import core as assembly_core
from src.services.pipeline.assembly.moment_fallback import (
    always_moments_tab,
    provisional_moments_tab,
)
from src.shared_config.domain_config import effective_requirements

_OUTLINE = [
    {"start": 0, "end": 300, "title": "The stack"},
    {"start": 300, "end": 900, "title": "Agent workflow"},
]
_META = {"duration": 900, "outline": _OUTLINE, "chapters": []}
_NO_SOURCE_META = {"duration": 900, "outline": [], "chapters": []}
_DESCRIPTION = {"timestamps": [{"time": "0:00", "seconds": 0, "label": "Start"}]}
_QUESTIONS = [
    {"question": f"Q{i}?", "options": ["a", "b", "c", "d"], "correctIndex": 1, "explanation": "b"}
    for i in range(3)
]
_CODE_TAB = {"id": "code", "label": "Code", "component": "code_playground",
             "dataSource": "tech.snippets", "goal": "Run it"}  # fmt: skip
_QUIZ_TAB = {"id": "quiz", "label": "Quiz", "component": "quiz_arena",
             "dataSource": "enrichment.quiz", "goal": "Check it"}  # fmt: skip
_MOMENTS_TAB = {"id": "moments", "label": "🎬 Demos", "emoji": "🎬", "component": "moment_track",
                "dataSource": "learning.timestamps", "goal": "Jump to a demo"}  # fmt: skip


def _snippet(code: str) -> dict:
    return {"filename": f"{code}.py", "language": "python", "code": code, "explanation": "demo"}


_EXTRACTION = {"tech": {"snippets": [_snippet("x = 1"), _snippet("y = 2")]}}


def _triage(*tabs: dict) -> dict:
    return {"contentTags": ["tech"], "primaryTag": "tech", "tabs": list(tabs)}


def _assemble(triage: dict, meta: dict, description: dict | None = None) -> dict:
    return assemble_response(
        triage, _EXTRACTION, {"quiz": _QUESTIONS}, None, video_meta=meta,
        description_analysis=description,
    )  # fmt: skip


def _ids(result: dict) -> list[str]:
    return [t["id"] for t in result["tabs"]]


def _moment_tabs(result: dict) -> list[dict]:
    return [t for t in result["tabs"] if t["component"] == "moment_track"]


class TestAlwaysMomentsInAssembly:
    def test_should_add_the_outline_moments_tab_when_the_plan_designed_none(self) -> None:
        result = _assemble(_triage(_CODE_TAB, _QUIZ_TAB), _META)

        labels = [m["label"] for m in _moment_tabs(result)[0]["props"]["items"]]
        assert labels == ["The stack", "Agent workflow"]

    def test_should_place_it_after_the_planned_tabs_with_the_quiz_still_last(self) -> None:
        result = _assemble(_triage(_CODE_TAB, _QUIZ_TAB), _META)

        assert _ids(result)[-3:] == ["code", "key_moments", "quiz"]

    def test_should_not_add_a_second_moments_tab_when_the_plan_designed_one(self) -> None:
        result = _assemble(_triage(_CODE_TAB, _MOMENTS_TAB, _QUIZ_TAB), _META)

        assert [t["id"] for t in _moment_tabs(result)] == ["moments"]

    def test_should_take_description_timestamps_when_nothing_else_exists(self) -> None:
        result = _assemble(_triage(_CODE_TAB), _NO_SOURCE_META, _DESCRIPTION)

        assert [m["label"] for m in _moment_tabs(result)[0]["props"]["items"]] == ["Start"]

    def test_should_add_no_moments_tab_without_any_source(self) -> None:
        result = _assemble(_triage(_CODE_TAB, _QUIZ_TAB), _NO_SOURCE_META)

        assert _moment_tabs(result) == []

    def test_should_record_no_drop_when_there_is_no_source(self) -> None:
        result = _assemble(_triage(_CODE_TAB, _QUIZ_TAB), _NO_SOURCE_META)

        assert all(entry["component"] != "moment_track" for entry in result["dropped"])

    def test_should_respect_a_domain_that_forbids_moment_track(self) -> None:
        def _forbidding(domain: str, content_format: str | None = None) -> dict:
            policy = effective_requirements(domain, content_format)
            return {**policy, "forbidden": policy["forbidden"] | {"moment_track"}}

        with patch.object(assembly_core, "effective_requirements", _forbidding):
            result = _assemble(_triage(_CODE_TAB, _QUIZ_TAB), _META)

        assert _moment_tabs(result) == []

    def test_should_let_a_required_backfill_from_extraction_win(self) -> None:
        """podcast requires moment_track: real segments beat the outline."""
        triage = {"contentTags": ["podcast"], "primaryTag": "podcast", "tabs": [
            {"id": "topics", "label": "Topics", "component": "info_grid",
             "dataSource": "podcast.topics"},
        ]}  # fmt: skip
        extraction = {"podcast": {
            "segments": [{"title": "Intro", "summary": "Hi", "timestamp": 0},
                         {"title": "Deep dive", "summary": "Raft", "timestamp": 480}],
            "topics": [{"topic": "Raft", "detail": "Consensus"},
                       {"topic": "CAP", "detail": "Trade-offs"}],
        }}  # fmt: skip

        result = assemble_response(triage, extraction, None, None, video_meta=_META)

        assert [t["id"] for t in _moment_tabs(result)] == ["segments"]


class TestAlwaysMomentsTab:
    def test_should_return_none_when_a_moment_tab_already_exists(self) -> None:
        tabs = [{"id": "moments", "component": "moment_track"}]

        assert always_moments_tab(tabs, [], _META, None, frozenset()) is None

    def test_should_pick_a_free_id_when_a_plan_tab_holds_key_moments(self) -> None:
        plan = [{"id": "key_moments", "component": "info_grid"}]

        tab = always_moments_tab([], plan, _META, None, frozenset())

        assert (tab or {}).get("id") == "key_moments_2"


class TestProvisionalMomentsTab:
    def test_should_take_the_planned_moment_tabs_id_label_and_goal(self) -> None:
        built = provisional_moments_tab([_CODE_TAB, _MOMENTS_TAB], _META, None, frozenset())

        tab, _position = built or ({}, 0)
        assert (tab["id"], tab["label"], tab["goal"]) == ("moments", "🎬 Demos", "Jump to a demo")

    def test_should_estimate_the_position_after_the_overview_and_earlier_tabs(self) -> None:
        built = provisional_moments_tab([_CODE_TAB, _MOMENTS_TAB], _META, None, frozenset())

        assert (built or ({}, -1))[1] == 2

    def test_should_leave_the_quiz_out_of_the_position_estimate(self) -> None:
        plan = [_QUIZ_TAB, _CODE_TAB, _MOMENTS_TAB]

        assert (provisional_moments_tab(plan, _META, None, frozenset()) or ({}, -1))[1] == 2

    def test_should_be_the_always_moments_tab_when_the_plan_designed_none(self) -> None:
        built = provisional_moments_tab([_CODE_TAB, _QUIZ_TAB], _META, None, frozenset())

        tab, position = built or ({}, -1)
        assert (tab["id"], tab["component"], position) == ("key_moments", "moment_track", 2)

    def test_should_be_outline_based(self) -> None:
        built = provisional_moments_tab([_CODE_TAB, _MOMENTS_TAB], _META, None, frozenset())

        items = (built or ({"props": {"items": []}}, 0))[0]["props"]["items"]
        assert [m["label"] for m in items] == ["The stack", "Agent workflow"]

    def test_should_return_none_without_any_source(self) -> None:
        assert provisional_moments_tab([_CODE_TAB], _NO_SOURCE_META, None, frozenset()) is None

    def test_should_return_none_when_the_domain_forbids_moment_track(self) -> None:
        forbidden = frozenset({"moment_track"})

        assert provisional_moments_tab([_MOMENTS_TAB], _META, None, forbidden) is None


class TestPlanDoneAndAssemblyAgree:
    """The provisional tab is replaced BY ID — both ends must reach the same id."""

    def test_should_name_the_provisional_after_the_tab_assembly_adds(self) -> None:
        triage = _triage(_CODE_TAB, _QUIZ_TAB)

        built = provisional_moments_tab(triage["tabs"], _META, None, frozenset())

        assert (built or ({"id": None}, 0))[0]["id"] in _ids(_assemble(triage, _META))

    def test_should_agree_on_the_free_id_when_key_moments_is_taken(self) -> None:
        taken = {"id": "key_moments", "label": "Facts", "component": "code_playground",
                 "dataSource": "tech.snippets"}  # fmt: skip
        triage = _triage(taken, _QUIZ_TAB)

        built = provisional_moments_tab(triage["tabs"], _META, None, frozenset())

        assert (built or ({"id": None}, 0))[0]["id"] in _ids(_assemble(triage, _META))
