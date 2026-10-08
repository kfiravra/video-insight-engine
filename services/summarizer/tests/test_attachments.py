"""Unit tests for secondary-tier attachment building (interactive-overhaul-v2 P2).

`attach_secondaries` is authoritative and data-driven: it enriches sparse tabs
(frame_strip / quick_quiz / tip_callout) and breaks up dense ones
(summary_header), and never produces a standalone secondary tab.
"""

from __future__ import annotations

import pytest

from src.services.pipeline.assembly.attachments import (
    _DENSE_THRESHOLD,
    _PRIMARY_LIST_KEY,
    _SPARSE_THRESHOLD,
    attach_secondaries,
    can_host_quick_quiz,
)
from src.shared_config.domain_config import quiz_policy


def _frames(n: int) -> list[dict]:
    return [
        {"thumbnailUrl": f"https://cdn/{i}.jpg", "timestamp": i * 10, "caption": f"f{i}"}
        for i in range(n)
    ]


def _tab(component: str, list_key: str, n: int, goal: str = "Tab goal") -> dict:
    return {
        "id": "t",
        "component": component,
        "goal": goal,
        "props": {list_key: [{"x": i} for i in range(n)]},
    }


class TestSparseEnrichment:
    def test_sparse_tab_gets_a_frame_strip_when_frames_available(self):
        tab = _tab("info_grid", "items", _SPARSE_THRESHOLD)
        out = attach_secondaries(tab, {}, None, _frames(6), "review")
        assert len(out) == 1
        assert out[0]["component"] == "frame_strip"
        assert out[0]["slot"] == "bottom"
        assert out[0]["props"]["frames"]

    def test_sparse_tab_falls_back_to_quick_quiz_when_no_frames(self):
        tab = _tab("info_grid", "items", 2)
        enrichment = {
            "quiz": [
                {"question": "Q1?", "options": ["a", "b"], "correctIndex": 0},
                {"question": "Q2?", "options": ["c", "d"], "correctIndex": 1},
            ]
        }
        out = attach_secondaries(tab, {}, enrichment, None, "learning")
        assert len(out) == 1
        assert out[0]["component"] == "quick_quiz"
        # quick_quiz is a SINGLE question.
        assert len(out[0]["props"]["questions"]) == 1

    def test_sparse_tab_falls_back_to_tip_callout(self):
        tab = _tab("info_grid", "items", 2)
        extraction = {"food": {"tips": ["Rest the dough overnight."]}}
        out = attach_secondaries(tab, extraction, None, None, "food")
        assert len(out) == 1
        assert out[0]["component"] == "tip_callout"
        assert out[0]["props"]["text"] == "Rest the dough overnight."

    def test_sparse_tab_gets_nothing_when_no_enrichment_source(self):
        tab = _tab("info_grid", "items", 2)
        out = attach_secondaries(tab, {}, None, None, "learning")
        assert out == []


class TestDenseSummary:
    def test_dense_tab_gets_a_top_summary_header(self):
        tab = _tab("info_grid", "items", _DENSE_THRESHOLD, goal="Searchable spec table.")
        out = attach_secondaries(tab, {}, None, None, "review")
        assert len(out) == 1
        assert out[0]["component"] == "summary_header"
        assert out[0]["slot"] == "top"
        assert out[0]["props"]["summary"] == "Searchable spec table."

    def test_dense_tab_with_no_goal_gets_nothing(self):
        tab = _tab("info_grid", "items", _DENSE_THRESHOLD, goal="")
        out = attach_secondaries(tab, {}, None, None, "review")
        assert out == []


class TestNoStandaloneAndEdges:
    def test_mid_size_tab_gets_nothing(self):
        mid = (_SPARSE_THRESHOLD + _DENSE_THRESHOLD) // 2
        tab = _tab("info_grid", "items", mid)
        out = attach_secondaries(tab, {"food": {"tips": ["x"]}}, None, _frames(6), "food")
        assert out == []

    def test_tab_without_a_primary_list_gets_nothing(self):
        # overview has no list_key in the primary-list map.
        tab = {"id": "overview", "component": "overview", "props": {"data": {}}}
        out = attach_secondaries(tab, {}, None, _frames(6), "learning")
        assert out == []

    def test_filmstrip_primary_never_gets_a_frame_strip(self):
        # A frame-heavy tab shouldn't be decorated with another frame strip;
        # it falls through to quick_quiz / tip.
        tab = _tab("video_filmstrip", "frames", 3)
        tab["props"] = {"frames": _frames(3)}
        out = attach_secondaries(
            tab,
            {"travel": {"tips": ["Pack light."]}},
            None,
            _frames(6),
            "travel",
        )
        assert all(a["component"] != "frame_strip" for a in out)

    def test_music_domain_skips_frame_strip(self):
        tab = _tab("info_grid", "items", 2)
        out = attach_secondaries(
            tab,
            {"music": {"tips": ["Mind the bridge."]}},
            None,
            _frames(6),
            "music",
        )
        # frame_strip is suppressed for music; tip is used instead.
        assert all(a["component"] != "frame_strip" for a in out)

    def test_every_attachment_carries_slot_and_known_component(self):
        tab = _tab("info_grid", "items", 2)
        out = attach_secondaries(
            tab,
            {"food": {"tips": ["t"]}},
            None,
            _frames(6),
            "food",
        )
        for att in out:
            assert att["slot"] in ("top", "bottom")
            assert att["component"]
            assert isinstance(att["props"], dict)


class TestOncePerResponseBudget:
    """frame_strip/quick_quiz/tip_callout carry response-global payloads —
    excluded_kinds lets the caller budget each to one appearance."""

    def test_excluded_frame_strip_falls_through_to_quick_quiz(self):
        tab = _tab("info_grid", "items", 2)
        enrichment = {
            "quiz": [
                {
                    "question": "Q?",
                    "options": ["a", "b", "c"],
                    "correctIndex": 0,
                    "explanation": "because",
                }
            ]
        }
        out = attach_secondaries(
            tab, {}, enrichment, _frames(6), "learning", excluded_kinds={"frame_strip"}
        )
        assert [a["component"] for a in out] == ["quick_quiz"]

    def test_all_kinds_excluded_yields_nothing(self):
        tab = _tab("info_grid", "items", 2)
        out = attach_secondaries(
            tab,
            {"food": {"tips": ["t"]}},
            {"quiz": [{"question": "Q?", "options": ["a", "b"], "correctIndex": 0}]},
            _frames(6),
            "food",
            excluded_kinds={"frame_strip", "quick_quiz", "tip_callout"},
        )
        assert out == []

    def test_summary_header_is_not_budgeted(self):
        tab = _tab("info_grid", "items", _DENSE_THRESHOLD)
        out = attach_secondaries(
            tab,
            {},
            None,
            None,
            "review",
            excluded_kinds={"frame_strip", "quick_quiz", "tip_callout"},
        )
        assert [a["component"] for a in out] == ["summary_header"]

    def test_default_excluded_kinds_keeps_prior_behavior(self):
        tab = _tab("info_grid", "items", 2)
        out = attach_secondaries(tab, {}, None, _frames(6), "review")
        assert [a["component"] for a in out] == ["frame_strip"]


class TestQuickQuizHosts:
    """quizPolicy.attachmentHostsExclude (pipeline-1min 1d.2): no quiz strip under do-along tabs."""

    _ENRICHMENT = {
        "quiz": [{"question": "Q?", "options": ["a", "b"], "correctIndex": 0, "explanation": "e"}]
    }
    _TIPS = {"food": {"tips": ["Rest the dough overnight."]}}

    @pytest.mark.parametrize("component", quiz_policy()["attachmentHostsExclude"])
    def test_should_attach_a_tip_not_a_quiz_when_the_host_is_excluded(self, component):
        tab = _tab(component, _PRIMARY_LIST_KEY[component], 2)

        out = attach_secondaries(tab, self._TIPS, self._ENRICHMENT, None, "food")

        assert [a["component"] for a in out] == ["tip_callout"]

    def test_should_attach_a_quick_quiz_when_the_host_is_allowed(self):
        tab = _tab("info_grid", "items", 2)

        out = attach_secondaries(tab, self._TIPS, self._ENRICHMENT, None, "food")

        assert [a["component"] for a in out] == ["quick_quiz"]

    @pytest.mark.parametrize(
        ("component", "expected"),
        [
            ("info_grid", True),
            ("spot_explorer", True),
            ("concept_canvas", True),
            ("quiz_arena", False),
            ("overview", False),
            *((excluded, False) for excluded in quiz_policy()["attachmentHostsExclude"]),
        ],
    )
    def test_should_report_whether_a_component_can_host_a_quick_quiz(self, component, expected):
        assert can_host_quick_quiz(component) is expected
