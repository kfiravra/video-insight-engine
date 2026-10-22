"""p4.0 (d) — a talk's asks: an explicit request with its quote (G3-4).

``assembly/talk_asks``: on a run with key ideas, the "What they asked" checklist on
``learning.takeaways`` is the key ideas of kind ``ask`` whose quote survived the quote
pass (``{label: claim, note: “quote”, timestamp}``); fewer than the checklist minimum
drops the tab and nothing refills it. A run without key ideas keeps its takeaways. An
ask without its quote is badged as an argument on the key-ideas tab.
"""

from __future__ import annotations

from typing import Any

from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.assemblers_primary import assemble_checklist
from src.services.pipeline.assembly.talk_asks import ask_rows, asks_data
from src.services.pipeline.quote_verify import QuoteIndex, verify_extraction_quotes

_SEGMENTS = [
    {"start": 100, "text": "If your app doesn't have a CLI, I want to see it by next Friday."},
    {"start": 200, "text": "Go and delete one feature from your product this week."},
    {"start": 300, "text": "Write to me and tell me what broke."},
    {"start": 400, "text": "I go pencils down every afternoon and read the diffs."},
]
_CLI = "If your app doesn't have a CLI, I want to see it by next Friday."
_DELETE = "Go and delete one feature from your product this week."
_WRITE = "Write to me and tell me what broke."
_PARAPHRASE = "Stop writing any code by hand, forever and ever."


def _idea(claim: str, kind: str, quote: str | None, timestamp: int) -> dict[str, Any]:
    return {"claim": claim, "why": "w", "quote": quote, "timestamp": timestamp, "kind": kind}


_PRACTICE = _idea("He reads every diff himself", "argument", _SEGMENTS[3]["text"], 400)
_ASKS = [
    _idea("Ship a CLI for your app by next Friday", "ask", _CLI, 100),
    _idea("Delete one feature this week", "ask", _DELETE, 200),
    _idea("Tell him what broke", "ask", _WRITE, 300),
]
_UNQUOTED_ASK = _idea("Never write code by hand again", "ask", _PARAPHRASE, 450)
_POINT = _idea("Small teams ship faster than big ones", "argument", None, 500)
_TAKEAWAYS = ["Use a CLI", "Avoid big features", "Read every diff"]


def _tab(tab_id: str, component: str, source: str) -> dict[str, Any]:
    return {"id": tab_id, "label": tab_id, "component": component, "dataSource": source}


_KEY_IDEAS_TAB = _tab("key_ideas", "spot_explorer", "learning.keyIdeas")
_ASKS_TAB = _tab("asks", "checklist", "learning.takeaways")


def _assemble(learning: dict[str, Any], asks_first: bool = False) -> dict[str, Any]:
    tabs = [_ASKS_TAB, _KEY_IDEAS_TAB] if asks_first else [_KEY_IDEAS_TAB, _ASKS_TAB]
    triage = {
        "primaryTag": "learning",
        "contentTags": ["learning"],
        "contentFormat": "talk",
        "tabs": tabs,
    }
    index = QuoteIndex.build(_SEGMENTS)
    extraction = {"learning": learning}
    return assemble_response(
        triage, extraction, None, None, video_meta={"duration": 600}, quote_index=index
    )


def _shipped(result: dict[str, Any], tab_id: str) -> dict[str, Any] | None:
    return next((tab for tab in result["tabs"] if tab["id"] == tab_id), None)


def _items(result: dict[str, Any], tab_id: str) -> list[dict[str, Any]]:
    tab = _shipped(result, tab_id)
    return [] if tab is None else tab["props"]["items"]


def _checklist_rows(ideas: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """The asks checklist's rows as the tab assembles them, after the quote pass."""
    extraction = {"learning": {"keyIdeas": ideas, "takeaways": _TAKEAWAYS}}
    checked = verify_extraction_quotes(extraction, None, QuoteIndex.build(_SEGMENTS)).extraction
    data = asks_data("checklist", "learning.takeaways", checked, _TAKEAWAYS)
    props = assemble_checklist({}, data, checked or {}, None) or {"items": []}
    return [(item["label"], item["note"]) for item in props["items"]]


class TestAsksTab:
    def test_should_build_the_asks_checklist_from_the_kind_ask_key_ideas_with_their_quotes(
        self,
    ) -> None:
        assert _checklist_rows([_PRACTICE, *_ASKS]) == [
            ("Ship a CLI for your app by next Friday", f"“{_CLI}”"),
            ("Delete one feature this week", f"“{_DELETE}”"),
            ("Tell him what broke", f"“{_WRITE}”"),
        ]

    def test_should_not_count_an_ask_whose_quote_the_transcript_does_not_say(self) -> None:
        assert _checklist_rows([*_ASKS[:2], _UNQUOTED_ASK]) == []

    def test_should_drop_the_asks_tab_when_fewer_than_three_asks_carry_a_quote(self) -> None:
        ideas = [_PRACTICE, *_ASKS[:2], _UNQUOTED_ASK]

        result = _assemble({"keyIdeas": ideas, "takeaways": _TAKEAWAYS})

        assert _shipped(result, "asks") is None

    def test_should_keep_an_explainers_takeaways_checklist_when_the_run_has_no_key_ideas(
        self,
    ) -> None:
        result = _assemble({"keyIdeas": [], "takeaways": _TAKEAWAYS})

        assert [item["label"] for item in _items(result, "asks")] == _TAKEAWAYS

    def test_should_never_refill_an_emptied_asks_tab_from_another_list(self) -> None:
        tips = [{"title": f"Tip {n}", "detail": "d"} for n in range(5)]
        learning = {"keyIdeas": [_PRACTICE, _ASKS[0]], "takeaways": [], "tips": tips}

        result = _assemble(learning)

        assert [tab["id"] for tab in result["tabs"] if tab["component"] == "checklist"] == []


def _spot_names(result: dict[str, Any]) -> list[str]:
    tab = _shipped(result, "key_ideas")
    return [] if tab is None else [spot["name"] for spot in tab["props"]["spots"]]


_ASK_CLAIMS = [idea["claim"] for idea in _ASKS]


class TestEachAskShowsOnce:
    def test_should_shed_ask_cards_from_key_ideas_when_the_asks_tab_ships(self) -> None:
        result = _assemble({"keyIdeas": [_PRACTICE, *_ASKS, _POINT], "takeaways": _TAKEAWAYS})

        assert (_spot_names(result), [i["label"] for i in _items(result, "asks")]) == (
            [_PRACTICE["claim"], _POINT["claim"]],
            _ASK_CLAIMS,
        )

    def test_should_keep_ask_cards_in_key_ideas_when_the_asks_tab_does_not_ship(self) -> None:
        ideas = [_PRACTICE, *_ASKS[:2], _POINT]

        result = _assemble({"keyIdeas": ideas, "takeaways": _TAKEAWAYS})

        assert _spot_names(result) == [idea["claim"] for idea in ideas]

    def test_should_shed_ask_cards_when_the_asks_tab_is_planned_first(self) -> None:
        learning = {"keyIdeas": [_PRACTICE, *_ASKS, _POINT], "takeaways": _TAKEAWAYS}

        result = _assemble(learning, asks_first=True)

        assert _spot_names(result) == [_PRACTICE["claim"], _POINT["claim"]]

    def test_should_keep_the_cards_and_drop_the_asks_tab_when_key_ideas_would_fall_short(
        self,
    ) -> None:
        result = _assemble({"keyIdeas": [_PRACTICE, *_ASKS], "takeaways": _TAKEAWAYS})

        assert (_spot_names(result), _shipped(result, "asks")) == (
            [_PRACTICE["claim"], *_ASK_CLAIMS],
            None,
        )

    def test_should_record_the_shed_cards_as_a_trim_of_key_ideas(self) -> None:
        result = _assemble({"keyIdeas": [_PRACTICE, *_ASKS, _POINT], "takeaways": _TAKEAWAYS})

        records = [(d["id"], d["reason"]) for d in result["dropped"] if d["id"] == "key_ideas"]
        assert records == [("key_ideas", "duplicate_items")]


class TestAskBadge:
    def _badges(self, ideas: list[dict[str, Any]]) -> list[str | None]:
        tab = _shipped(_assemble({"keyIdeas": ideas}), "key_ideas")
        return [] if tab is None else [spot.get("category") for spot in tab["props"]["spots"]]

    def test_should_badge_an_ask_without_a_quote_as_an_argument(self) -> None:
        assert self._badges([_PRACTICE, _UNQUOTED_ASK])[1] == "Argument"

    def test_should_keep_the_ask_badge_when_the_ask_keeps_its_quote(self) -> None:
        assert self._badges([_PRACTICE, _ASKS[0]])[1] == "Ask"


class TestAskRows:
    def test_should_stamp_each_row_at_its_idea(self) -> None:
        rows = ask_rows({"learning": {"keyIdeas": _ASKS}}) or []

        assert [row.get("timestamp") for row in rows] == [100, 200, 300]

    def test_should_write_a_translated_quote_as_prose(self) -> None:
        translated = [{**idea, "quoteTranslated": True} for idea in _ASKS]

        rows = ask_rows({"learning": {"keyIdeas": translated}}) or []

        assert rows[0]["note"] == _CLI

    def test_should_return_none_when_the_run_has_no_key_ideas(self) -> None:
        assert ask_rows({"learning": {"takeaways": _TAKEAWAYS}}) is None

    def test_should_leave_any_other_tab_data_unchanged(self) -> None:
        extraction = {"learning": {"keyIdeas": _ASKS, "takeaways": _TAKEAWAYS}}

        assert asks_data("checklist", "learning.tips", extraction, _TAKEAWAYS) is _TAKEAWAYS
