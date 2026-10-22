"""Interview exchange rows and asks rows keep what the web reads (output-quality 4.1).

An exchange row (``podcast.qa``) now carries its whole ``question`` even when the
question is all of the row's title — the web tells an exchange from a moment by it —
and its ``questionStatus`` (4.0 c), an enum the translation walk leaves alone. A
plain moment carries neither. A checklist row built from a stamped item (a talk's
asks, ``talk_asks.ask_rows``) keeps its timestamp.
"""

from __future__ import annotations

from src.services.pipeline.assembly import assemble_checklist
from src.services.pipeline.assembly.moment_normalizers import normalize_moment_items
from src.services.pipeline.translation import _collect_strings

_QUESTION = "Why Rails?"


def _exchange(**extra: object) -> dict[str, object]:
    return {"question": _QUESTION, "answer": "It is fast.", "speaker": "DHH",
            "timestamp": 60, **extra}  # fmt: skip


class TestExchangeRow:
    def test_should_keep_the_whole_question_on_every_exchange_row(self):
        assert normalize_moment_items([_exchange()])[0]["question"] == _QUESTION

    def test_should_title_the_row_with_the_question(self):
        assert normalize_moment_items([_exchange()])[0]["label"] == _QUESTION

    def test_should_pass_the_question_status_through(self):
        row = normalize_moment_items([_exchange(questionStatus="verified")])[0]

        assert row["questionStatus"] == "verified"

    def test_should_keep_the_speaker_on_the_exchange_row(self):
        assert normalize_moment_items([_exchange()])[0]["speaker"] == "DHH"

    def test_should_not_send_the_question_status_to_translation(self):
        row = normalize_moment_items([_exchange(questionStatus="translated")])[0]
        pairs: list[tuple[tuple[str | int, ...], str]] = []

        _collect_strings(row, (), pairs)

        assert "questionStatus" not in {path[-1] for path, _ in pairs}

    def test_should_carry_no_question_on_a_plain_moment(self):
        moment = {"title": "The demo", "description": "d", "seconds": 90}

        assert "question" not in normalize_moment_items([moment])[0]


def _items(data: list) -> list[dict]:
    props = assemble_checklist({}, data, {}, None)
    assert props is not None
    return props["items"]


class TestChecklistTimestamps:
    def test_should_keep_an_ask_rows_timestamp(self):
        rows = [{"label": "Ship a CLI", "note": "“Ship a CLI”", "timestamp": 2775}]

        assert _items(rows)[0]["timestamp"] == 2775

    def test_should_keep_a_text_items_seconds_as_its_timestamp(self):
        rows = [{"text": "Back up first", "seconds": 30}]

        assert _items(rows)[0]["timestamp"] == 30

    def test_should_ship_no_timestamp_on_an_unstamped_row(self):
        assert "timestamp" not in _items([{"label": "Ship a CLI"}])[0]

    def test_should_keep_a_string_item_as_its_label(self):
        assert _items(["Read the docs"]) == [{"label": "Read the docs"}]

    def test_should_drop_a_dict_with_no_text_field(self):
        assert assemble_checklist({}, [{"day": 1, "spots": []}], {}, None) is None
