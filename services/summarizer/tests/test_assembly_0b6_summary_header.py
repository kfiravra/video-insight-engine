"""0b.6 — a dense tab's summary_header carries content, never the goal (R4).

The FE prints ``tab.goal`` as the tab's intro line; the header on top of a
dense tab used to repeat it ("IN SHORT" = the goal, 30 of 45 tabs). Now the
header is the extraction ``summary`` on the answer tab, else the tab's first
item — and it is dropped rather than shipped when that text equals the goal
(case, inner whitespace and a trailing ``.!?`` aside — the FE hides a stored
header equal to the goal by the same rule). A flash deck never gets one: its
first card's text would give the answer away.
"""

from __future__ import annotations

from src.services.pipeline.assembly.attachments import (
    _DENSE_THRESHOLD,
    _HEADER_MAX_CHARS,
    attach_response_secondaries,
    attach_secondaries,
)

GOAL = "Scan DHH's concrete figures and claims at a glance."


def _spots(n: int, first: dict | None = None) -> list[dict]:
    spots = [{"name": f"Spot {i}", "description": f"detail {i}"} for i in range(n)]
    if first is not None:
        spots[0] = first
    return spots


def _dense_tab(component: str = "spot_explorer", key: str = "spots", **overrides) -> dict:
    tab = {
        "id": "numbers",
        "component": component,
        "goal": GOAL,
        "props": {key: _spots(_DENSE_THRESHOLD)},
    }
    tab.update(overrides)
    return tab


def _header(attachments: list[dict]) -> dict | None:
    return next((a for a in attachments if a["component"] == "summary_header"), None)


class TestHeaderContent:
    def test_header_is_the_first_item_headline_and_body(self):
        first = {"name": "150,000 lines in one month", "description": "vs ~30,000 per year."}
        tab = _dense_tab(props={"spots": _spots(_DENSE_THRESHOLD, first)})

        header = _header(attach_secondaries(tab, {}, None, None, "learning"))

        assert header is not None
        assert header["props"]["summary"] == "150,000 lines in one month — vs ~30,000 per year."

    def test_header_never_equals_the_goal(self):
        tab = _dense_tab()

        header = _header(attach_secondaries(tab, {}, None, None, "learning"))

        assert header is not None
        assert header["props"]["summary"] != GOAL

    def test_header_title_marks_a_first_item_header(self):
        tab = _dense_tab()

        header = _header(attach_secondaries(tab, {}, None, None, "learning"))

        assert header is not None
        assert header["props"]["title"] == "First up"

    def test_first_item_with_only_a_headline_uses_it_alone(self):
        tab = _dense_tab(
            props={"items": [{"label": f"Moment {i}", "seconds": i} for i in range(12)]}
        )
        tab["component"] = "moment_track"

        header = _header(attach_secondaries(tab, {}, None, None, "learning"))

        assert header is not None
        assert header["props"]["summary"] == "Moment 0"

    def test_string_first_item_is_used_verbatim(self):
        tab = _dense_tab(component="checklist", props={"items": ["  Rest the dough ", *["x"] * 11]})

        header = _header(attach_secondaries(tab, {}, None, None, "food"))

        assert header is not None
        assert header["props"]["summary"] == "Rest the dough"

    def test_long_text_is_capped_on_a_word_boundary(self):
        first = {"name": "Headline", "description": "word " * 100}
        tab = _dense_tab(props={"spots": _spots(_DENSE_THRESHOLD, first)})

        header = _header(attach_secondaries(tab, {}, None, None, "learning"))

        assert header is not None
        assert len(header["props"]["summary"]) <= _HEADER_MAX_CHARS
        assert header["props"]["summary"].endswith("…")

    def test_slot_and_size_unchanged(self):
        tab = _dense_tab()

        header = _header(attach_secondaries(tab, {}, None, None, "learning"))

        assert header is not None
        assert (header["slot"], header["size"]) == ("top", "banner")


class TestNeverTheGoal:
    def test_header_dropped_when_first_item_equals_the_goal(self):
        first = {"name": GOAL}
        tab = _dense_tab(props={"spots": _spots(_DENSE_THRESHOLD, first)})

        out = attach_secondaries(tab, {}, None, None, "learning")

        assert _header(out) is None

    def test_goal_comparison_ignores_case_and_trailing_period(self):
        first = {"name": GOAL.rstrip(".").upper()}
        tab = _dense_tab(props={"spots": _spots(_DENSE_THRESHOLD, first)})

        out = attach_secondaries(tab, {}, None, None, "learning")

        assert _header(out) is None

    def test_goal_comparison_ignores_a_trailing_question_mark(self):
        goal = "What does DHH mean by pencils down?"
        first = {"name": "what does DHH mean by pencils down"}
        tab = _dense_tab(goal=goal, props={"spots": _spots(_DENSE_THRESHOLD, first)})

        out = attach_secondaries(tab, {}, None, None, "learning")

        assert _header(out) is None

    def test_goal_comparison_collapses_inner_whitespace(self):
        first = {"name": GOAL.replace(" ", "  \n ")}
        tab = _dense_tab(props={"spots": _spots(_DENSE_THRESHOLD, first)})

        out = attach_secondaries(tab, {}, None, None, "learning")

        assert _header(out) is None

    def test_textless_first_item_yields_no_header(self):
        tab = _dense_tab(props={"spots": [{"x": i} for i in range(_DENSE_THRESHOLD)]})

        out = attach_secondaries(tab, {}, None, None, "learning")

        assert out == []

    def test_dense_tab_without_a_goal_still_gets_its_first_item(self):
        tab = _dense_tab(goal="")

        header = _header(attach_secondaries(tab, {}, None, None, "learning"))

        assert header is not None
        assert header["props"]["summary"] == "Spot 0 — detail 0"


class TestFlashDeck:
    def test_flash_deck_never_gets_a_header(self):
        cards = [{"front": f"Q{i}?", "back": f"A{i}."} for i in range(_DENSE_THRESHOLD)]
        tab = {"id": "cards", "component": "flash_deck", "goal": GOAL, "props": {"cards": cards}}

        out = attach_secondaries(tab, {}, None, None, "learning")

        assert _header(out) is None

    def test_flash_deck_answer_tab_skips_the_in_short_header_too(self):
        cards = [{"front": f"Q{i}?", "back": f"A{i}."} for i in range(_DENSE_THRESHOLD)]
        tab = {"id": "cards", "component": "flash_deck", "goal": GOAL, "props": {"cards": cards}}
        extraction = {"learning": {"summary": "The thesis."}}

        out = attach_secondaries(tab, extraction, None, None, "learning", is_answer_tab=True)

        assert _header(out) is None


class TestAnswerTab:
    _EXTRACTION = {"learning": {"summary": "DHH argues hand-written code is over."}}

    def test_answer_tab_header_is_the_extraction_summary(self):
        tab = _dense_tab()

        header = _header(
            attach_secondaries(tab, self._EXTRACTION, None, None, "learning", is_answer_tab=True)
        )

        assert header is not None
        assert header["props"]["summary"] == "DHH argues hand-written code is over."

    def test_answer_tab_summary_header_is_titled_in_short(self):
        tab = _dense_tab()

        header = _header(
            attach_secondaries(tab, self._EXTRACTION, None, None, "learning", is_answer_tab=True)
        )

        assert header is not None
        assert header["props"]["title"] == "In short"

    def test_non_answer_tab_ignores_the_extraction_summary(self):
        tab = _dense_tab()

        header = _header(attach_secondaries(tab, self._EXTRACTION, None, None, "learning"))

        assert header is not None
        assert header["props"]["summary"] == "Spot 0 — detail 0"

    def test_summary_from_another_domain_when_primary_has_none(self):
        extraction = {"tech": {"topics": []}, "learning": {"summary": "Hooks explained."}}
        tab = _dense_tab()

        header = _header(
            attach_secondaries(tab, extraction, None, None, "tech", is_answer_tab=True)
        )

        assert header is not None
        assert header["props"]["summary"] == "Hooks explained."

    def test_answer_tab_without_a_summary_falls_back_to_the_first_item(self):
        tab = _dense_tab()

        header = _header(
            attach_secondaries(tab, {"food": {}}, None, None, "food", is_answer_tab=True)
        )

        assert header is not None
        assert header["props"]["summary"] == "Spot 0 — detail 0"

    def test_answer_tab_summary_equal_to_goal_is_dropped(self):
        tab = _dense_tab()
        extraction = {"learning": {"summary": GOAL}}

        out = attach_secondaries(tab, extraction, None, None, "learning", is_answer_tab=True)

        assert _header(out) is None


class TestResponseLoop:
    """attach_response_secondaries: the first content tab after the overview is the answer tab."""

    def _tabs(self) -> list[dict]:
        overview = {"id": "overview", "component": "overview", "goal": "g", "props": {"data": {}}}
        return [overview, _dense_tab(id="first"), _dense_tab(id="second")]

    def test_first_content_tab_gets_the_extraction_summary(self):
        tabs = self._tabs()
        extraction = {"learning": {"summary": "The thesis."}}

        attach_response_secondaries(tabs, extraction, None, None, "learning")

        assert _header(tabs[1]["attachments"])["props"]["summary"] == "The thesis."

    def test_later_tabs_get_their_first_item(self):
        tabs = self._tabs()
        extraction = {"learning": {"summary": "The thesis."}}

        attach_response_secondaries(tabs, extraction, None, None, "learning")

        assert _header(tabs[2]["attachments"])["props"]["summary"] == "Spot 0 — detail 0"

    def test_answer_moves_past_a_content_tab_that_took_nothing(self):
        overview, first, second = self._tabs()
        filmstrip = {"id": "frames", "component": "video_filmstrip", "goal": "g", "props": {}}
        tabs = [overview, filmstrip, first, second]

        attach_response_secondaries(
            tabs, {"learning": {"summary": "The thesis."}}, None, None, "learning"
        )

        assert _header(tabs[2]["attachments"])["props"]["summary"] == "The thesis."

    def test_the_tab_that_takes_the_answer_keeps_it(self):
        overview, first, second = self._tabs()
        filmstrip = {"id": "frames", "component": "video_filmstrip", "goal": "g", "props": {}}
        tabs = [overview, filmstrip, first, second]

        attach_response_secondaries(
            tabs, {"learning": {"summary": "The thesis."}}, None, None, "learning"
        )

        assert _header(tabs[3]["attachments"])["props"]["summary"] == "Spot 0 — detail 0"

    def test_a_tab_that_took_an_attachment_holds_the_answer(self):
        overview, _, dense = self._tabs()
        sparse = {"id": "s", "component": "info_grid", "goal": "g", "props": {"items": [{"x": 1}]}}
        tabs = [overview, sparse, dense]
        extraction = {"learning": {"summary": "The thesis.", "tips": ["Rest the dough."]}}

        attach_response_secondaries(tabs, extraction, None, None, "learning")

        assert _header(tabs[2]["attachments"])["props"]["summary"] == "Spot 0 — detail 0"

    def test_overview_is_never_decorated(self):
        tabs = self._tabs()

        attach_response_secondaries(tabs, {"learning": {"summary": "s"}}, None, None, "learning")

        assert "attachments" not in tabs[0]

    def test_once_per_response_kinds_are_budgeted(self):
        sparse = {"id": "s", "component": "info_grid", "goal": "g", "props": {"items": [{"x": 1}]}}
        tabs = [dict(sparse, id="a"), dict(sparse, id="b")]
        extraction = {"food": {"tips": ["Rest the dough."]}}

        attach_response_secondaries(tabs, extraction, None, None, "food")

        kinds = [a["component"] for t in tabs for a in t.get("attachments", [])]
        assert kinds == ["tip_callout"]

    def test_filmstrip_tab_pre_consumes_the_frame_strip_budget(self):
        frames = [{"thumbnailUrl": f"u{i}", "timestamp": i, "caption": "c"} for i in range(6)]
        filmstrip = {
            "id": "f",
            "component": "video_filmstrip",
            "goal": "g",
            "props": {"frames": frames},
        }
        sparse = {"id": "s", "component": "info_grid", "goal": "g", "props": {"items": [{"x": 1}]}}
        tabs = [filmstrip, sparse]

        attach_response_secondaries(tabs, {"frames": frames}, None, frames, "travel")

        assert all(a["component"] != "frame_strip" for a in sparse.get("attachments", []))
