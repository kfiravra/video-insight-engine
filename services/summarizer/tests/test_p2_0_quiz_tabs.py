"""Quiz stamps in seconds, and no tab with empty props (output-quality 2.0 a + b, G1-1 / G1-2).

S03's stored gate-1 quiz (data/S03/doc.json): minutes stored as seconds (3, 20, 15, 15),
one explanation citing the wrong clock and one a bare "(1322)". No LLM, no I/O. The
filmstrip and hero-reference rules are in ``test_p2_0_tab_references``.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.density import drop_empty_tabs, enforce_density, has_content
from src.services.pipeline.assembly.normalizers import _normalize_quiz_question
from src.services.pipeline.assembly.quiz_stamps import (
    CLOCK_AGREEMENT_S,
    reconcile_quiz_time,
    with_reconciled_quiz,
)
from src.services.pipeline.enrichment import parse_quiz

_S03_DURATION = 3434
# (stored timestamp, explanation) — data/S03/doc.json enrichment.quiz, gate 1 (trimmed).
_S03_QUIZ: list[tuple[int, str]] = [
    (3, "At (3:22), it is noted that menstruation and a brain tumor were used in court."),
    (20, "At (20:02), it is argued that categorical thinking misreads behavior."),
    (382, "At (3:22), Sapolsky shows that bodily influences can alter brain function."),
    (15, "At (15:01), it is detailed that categories exaggerate differences."),
    (1342, "At (22:22), the course's method is established as working back through layers."),
    (15, "At (15:01), Sapolsky uses the example of test scores between a 65 and a 66."),
    (1322, "At (1322), it is highlighted that body and behavior influence each other."),
    (2850, "At (47:30), Sapolsky argues that like clouds, behavior is complex."),
]
# Each question's answer, checked against data/S03/transcript.txt (blocks 3:13 / 19:45 /
# 6:19 / 14:53 / 22:03 / 14:53 / 22:03 / 47:22): the minutes stamps take their clock, the
# third question's 382 (6:22) is right and its "(3:22)" is not.
_S03_SECONDS = [202, 1202, 382, 901, 1342, 901, 1322, 2850]
_BARE_SECONDS = re.compile(r"\(\d+\)")


def _s03_items() -> list[dict[str, Any]]:
    return [
        {
            "question": f"Question {n} about the lecture?",
            "options": ["One", "Two", "Three", "Four"],
            "correctIndex": n % 4,
            "timestamp": stamp,
            "explanation": explanation,
        }
        for n, (stamp, explanation) in enumerate(_S03_QUIZ)
    ]


# ─── (a) Quiz stamps in seconds ───


class TestReconcileQuizTime:
    def test_should_take_the_clock_when_the_stamp_reads_as_its_minutes(self) -> None:
        assert reconcile_quiz_time(3, "At (3:22), it is noted.")[0] == 202

    def test_should_keep_a_disagreeing_stamp_and_cite_it_in_the_explanation(self) -> None:
        assert reconcile_quiz_time(382, "At (3:22), Sapolsky shows it.", _S03_DURATION) == (
            382,
            "At (6:22), Sapolsky shows it.",
        )

    def test_should_take_the_clock_when_the_stamp_lies_past_the_end(self) -> None:
        assert reconcile_quiz_time(9_000, "At (5:12) he says so.", 600)[0] == 312

    def test_should_leave_the_prose_alone_when_it_cites_several_clocks(self) -> None:
        text = "Compare (1:40) with (2:30)."

        assert reconcile_quiz_time(400, text) == (400, text)

    def test_should_keep_the_stamp_when_it_agrees_within_the_tolerance(self) -> None:
        assert reconcile_quiz_time(202 + CLOCK_AGREEMENT_S, "At (3:22), he says so.")[0] == 212

    def test_should_keep_the_stamp_when_the_explanation_cites_no_clock(self) -> None:
        assert reconcile_quiz_time(15, "Categories exaggerate differences.") == (
            15,
            "Categories exaggerate differences.",
        )

    def test_should_leave_a_stampless_question_stampless(self) -> None:
        assert reconcile_quiz_time(None, "He says it (4:32).")[0] is None

    def test_should_render_a_bare_second_count_inside_the_video_as_a_clock(self) -> None:
        _, text = reconcile_quiz_time(1322, "At (1322), the link is reciprocal.", _S03_DURATION)

        assert text == "At (22:02), the link is reciprocal."

    def test_should_strip_a_bare_number_past_the_end_with_its_lead_in(self) -> None:
        assert reconcile_quiz_time(100, "At (2019), the study shows it.", 1500) == (
            100,
            "The study shows it.",
        )

    def test_should_ignore_and_strip_a_clock_past_the_end(self) -> None:
        assert reconcile_quiz_time(300, "He says so (55:00).", 600) == (300, "He says so.")

    def test_should_leave_a_bracketed_bare_number_alone(self) -> None:
        assert reconcile_quiz_time(5, "Option [3] is right.")[1] == "Option [3] is right."

    def test_should_change_nothing_when_applied_twice(self) -> None:
        once = reconcile_quiz_time(3, "At (1322), x (3:22).", _S03_DURATION)

        assert reconcile_quiz_time(*once, _S03_DURATION) == once


class TestS03Quiz:
    def test_should_land_every_stored_stamp_on_its_answer_when_parsed(self) -> None:
        questions = parse_quiz({"quiz": _s03_items()}, 8, _S03_DURATION)

        assert [q.timestamp for q in questions] == _S03_SECONDS

    def test_should_leave_no_bare_second_count_in_any_explanation_when_parsed(self) -> None:
        questions = parse_quiz({"quiz": _s03_items()}, 8, _S03_DURATION)

        assert [q.explanation for q in questions if _BARE_SECONDS.search(q.explanation)] == []

    def test_should_cite_each_stamps_own_clock_in_its_explanation_when_parsed(self) -> None:
        questions = parse_quiz({"quiz": _s03_items()}, 8, _S03_DURATION)

        cited = [re.findall(r"\((\d+:\d\d)\)", q.explanation) for q in questions]
        assert cited == [[f"{s // 60}:{s % 60:02d}"] for s in _S03_SECONDS]

    def test_should_still_drop_a_question_stamped_past_the_end_without_a_clock(self) -> None:
        item = {**_s03_items()[0], "timestamp": 9_000, "explanation": "No clock here."}

        assert parse_quiz({"quiz": [item]}, 8, _S03_DURATION) == []

    def test_should_convert_a_minutes_stamp_in_the_normalizer(self) -> None:
        normalized = _normalize_quiz_question(_s03_items()[1])

        assert normalized is not None and normalized["timestamp"] == 1202

    def test_should_fix_the_stored_quiz_tab_when_reassembled(self) -> None:
        triage = {
            "primaryTag": "learning",
            "contentTags": ["learning"],
            "tabs": [
                {
                    "id": "quiz",
                    "label": "Quiz",
                    "component": "quiz_arena",
                    "dataSource": "enrichment.quiz",
                }
            ],
        }
        enrichment = {"quiz": _s03_items()}

        result = assemble_response(triage, {}, enrichment, None, {"duration": _S03_DURATION})

        quiz = next(t for t in result["tabs"] if t["component"] == "quiz_arena")
        assert [q.get("timestamp") for q in quiz["props"]["questions"]] == _S03_SECONDS

    def test_should_leave_the_stored_enrichment_record_as_the_model_wrote_it(self) -> None:
        enrichment = {"quiz": _s03_items()}

        with_reconciled_quiz(enrichment, _S03_DURATION)

        assert [q["timestamp"] for q in enrichment["quiz"]] == [s for s, _ in _S03_QUIZ]


# ─── (b) No empty tab ───


class TestEmptyProps:
    @pytest.mark.parametrize("props", [{}, {"frames": []}, {"title": " ", "data": {"items": [{}]}}])
    def test_should_fold_a_tab_whose_props_carry_no_content(self, props: dict) -> None:
        assert enforce_density("video_filmstrip", props) is None

    def test_should_count_a_number_as_content(self) -> None:
        assert has_content({"score": 0}) is True

    def test_should_drop_an_added_tab_with_empty_props_and_keep_the_rest(self) -> None:
        tabs = [{"id": "a", "props": {}}, {"id": "b", "props": {"items": ["x"]}}]

        removed = drop_empty_tabs(tabs)

        assert ([t["id"] for t in removed], [t["id"] for t in tabs]) == (["a"], ["b"])
