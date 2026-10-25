"""p4.0 (c) — podcast.qa questions in the host's words, checked (G3-4).

``assembly/qa_questions.check_questions`` runs on the copy assembly reads, right
after the quote pass: a question found in the transcript stamps its row at the
second it is asked (``questionStatus: verified``); an English source drops a
question it cannot find; any other source keeps it as a translation (D12); fewer
than three left empties the list. A Q&A tab with nothing left drops before any
fallback (``drop_emptied_qa``) — no outline section ships as a question.
"""

from __future__ import annotations

import copy
from typing import Any

from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.qa_questions import (
    MIN_QUESTIONS,
    QUESTION_STATUS_KEY,
    check_questions,
)
from src.services.pipeline.quote_verify import TRANSLATED, VERIFIED, QuoteIndex

_SEGMENTS = [
    {"start": 0, "text": "Welcome to the show. Today my guest is Max."},
    {"start": 10, "text": "Do you think there's intelligent life out there in the universe?"},
    {"start": 15, "text": "I think we are probably alone, at least in our galaxy."},
    {
        "start": 60,
        "text": "Let me ask you this. How do you think about consciousness as a physicist?",
    },
    {"start": 70, "text": "Consciousness is the way information feels when it is processed."},
    {"start": 120, "text": "What's your favorite equation?"},
    {"start": 125, "text": "Probably the one that governs all of quantum mechanics."},
    {
        "start": 200,
        "text": "If we build machines that think better than we do, what happens to meaning?",
    },
    {"start": 210, "text": "Meaning is something we create ourselves, not something we find."},
]
_ALIENS = "Do you think there's intelligent life out there in the universe?"
_CONSCIOUSNESS = "How do you think about consciousness as a physicist?"
_EQUATION = "What's your favorite equation?"
_MEANING_CUT = "If we build machines that think … what happens to meaning?"
_REWORDED = "Does building thinking machines take away the meaning of human life?"
_OUTLINE = [
    {"start": 0, "end": 100, "title": "Are we alone?", "establishes": "Probably, yes."},
    {"start": 100, "end": 300, "title": "Machines and meaning", "establishes": "We make it."},
]


def _index(language: str | None = None) -> QuoteIndex:
    return QuoteIndex.build(_SEGMENTS, language)


def _exchange(question: str, timestamp: int) -> dict[str, Any]:
    return {"question": question, "answer": "a", "speaker": "Max", "timestamp": timestamp}


def _extraction(*exchanges: dict[str, Any]) -> dict[str, Any]:
    segments = [{"title": "Aliens", "summary": "s", "timestamp": 0}]
    return {
        "podcast": {"qa": list(exchanges), "segments": segments},
        "learning": {"keyIdeas": [{"claim": "c", "why": "w", "timestamp": 15}]},
    }


def _checked_qa(extraction: dict[str, Any], index: QuoteIndex) -> list[dict[str, Any]]:
    checked, _ = check_questions(extraction, index)
    assert checked is not None
    return checked["podcast"]["qa"]


_THREE_SAID = (_exchange(_ALIENS, 40), _exchange(_CONSCIOUSNESS, 75), _exchange(_EQUATION, 121))


class TestCheckQuestions:
    def test_should_keep_a_question_found_verbatim_and_stamp_the_row_at_the_question(self) -> None:
        qa = _checked_qa(_extraction(*_THREE_SAID), _index())

        assert (qa[0]["timestamp"], qa[0][QUESTION_STATUS_KEY]) == (10, VERIFIED)

    def test_should_drop_a_question_the_transcript_does_not_say_when_the_source_is_english(
        self,
    ) -> None:
        qa = _checked_qa(_extraction(*_THREE_SAID, _exchange(_REWORDED, 200)), _index())

        assert [item["question"] for item in qa] == [_ALIENS, _CONSCIOUSNESS, _EQUATION]

    def test_should_keep_a_translated_question_when_the_source_is_not_english(self) -> None:
        qa = _checked_qa(_extraction(*_THREE_SAID, _exchange(_REWORDED, 200)), _index("he"))

        assert (qa[-1]["timestamp"], qa[-1][QUESTION_STATUS_KEY]) == (200, TRANSLATED)

    def test_should_accept_a_question_cut_into_two_runs_with_an_ellipsis(self) -> None:
        exchanges = (*_THREE_SAID[:2], _exchange(_MEANING_CUT, 215))

        qa = _checked_qa(_extraction(*exchanges), _index())

        assert (qa[-1]["timestamp"], qa[-1][QUESTION_STATUS_KEY]) == (200, VERIFIED)

    def test_should_empty_the_qa_list_when_fewer_than_three_questions_pass(self) -> None:
        exchanges = (*_THREE_SAID[:2], _exchange(_REWORDED, 200))

        checked, tally = check_questions(_extraction(*exchanges), _index())
        qa = (checked or {}).get("podcast", {}).get("qa")

        assert (qa, tally.emptied, MIN_QUESTIONS) == ([], True, 3)

    def test_should_drop_an_exchange_without_a_question(self) -> None:
        exchanges = (*_THREE_SAID, {"question": "  ", "answer": "a", "timestamp": 5})

        assert len(_checked_qa(_extraction(*exchanges), _index())) == 3

    def test_should_leave_every_other_list_untouched(self) -> None:
        extraction = _extraction(*_THREE_SAID, _exchange(_REWORDED, 200))

        checked, _ = check_questions(extraction, _index())
        podcast = (checked or {}).get("podcast", {})

        assert (
            podcast.get("segments") is extraction["podcast"]["segments"],
            (checked or {}).get("learning") is extraction["learning"],
        ) == (True, True)

    def test_should_leave_the_callers_extraction_untouched(self) -> None:
        extraction = _extraction(*_THREE_SAID, _exchange(_REWORDED, 200))
        before = copy.deepcopy(extraction)

        check_questions(extraction, _index())

        assert extraction == before

    def test_should_return_the_same_extraction_when_it_has_no_qa(self) -> None:
        extraction = {"learning": {"keyIdeas": []}}

        assert check_questions(extraction, _index())[0] is extraction


# ─── assembly ───


def _tab(tab_id: str, source: str) -> dict[str, Any]:
    return {"id": tab_id, "label": tab_id, "component": "moment_track", "dataSource": source}


def _assemble(extraction: dict[str, Any], index: QuoteIndex | None = None) -> dict[str, Any]:
    triage = {
        "primaryTag": "podcast",
        "contentTags": ["podcast"],
        "contentFormat": "interview",
        "tabs": [_tab("qa", "podcast.qa"), _tab("conversation_map", "podcast.segments")],
    }
    video_meta = {"duration": 600, "outline": _OUTLINE}
    return assemble_response(
        triage, extraction, None, None, video_meta=video_meta, quote_index=index or _index()
    )


def _ids(result: dict[str, Any]) -> list[str]:
    return [tab["id"] for tab in result["tabs"]]


class TestQaTab:
    def test_should_seek_each_shipped_row_to_its_question(self) -> None:
        result = _assemble(_extraction(*_THREE_SAID))
        qa = next(tab for tab in result["tabs"] if tab["id"] == "qa")

        assert [item["seconds"] for item in qa["props"]["items"]] == [10, 60, 120]

    def test_should_drop_an_emptied_qa_tab_instead_of_filling_it_from_the_outline(self) -> None:
        exchanges = (*_THREE_SAID[:2], _exchange(_REWORDED, 200))

        assert "qa" not in _ids(_assemble(_extraction(*exchanges)))

    def test_should_record_the_emptied_qa_tab_as_a_data_drop(self) -> None:
        exchanges = (*_THREE_SAID[:2], _exchange(_REWORDED, 200))

        dropped = _assemble(_extraction(*exchanges))["dropped"]

        assert [(d["id"], d["reason"]) for d in dropped if d["id"] == "qa"] == [
            ("qa", "validation_empty_list")
        ]

    def test_should_keep_the_conversation_map_when_the_qa_tab_drops(self) -> None:
        exchanges = (_exchange(_REWORDED, 200),)

        assert "conversation_map" in _ids(_assemble(_extraction(*exchanges)))
