"""Phase-4 review fixes, lane FB (output-quality): quiz v2 and merge_rank.

- B-W1: an edge move never files a key idea out of video order across sections (S02 p4v
  shipped 4241 s under "Quantum computing…" ahead of 4200 s under "Optimism…").
- B-W2: one question per idea before any second — a repeat takes no part's or outline
  section's slot (a 10-min quiz shipped K1 twice while K2 waited as a spare).
- B-W4: Q&A twins inside the Q&A list drop even when their group keeps a position.
- B-W5: two cut titles never merge on a shared lead; a closing acronym stays.
- A-W2: an interview's disagreement is a quiz target.

The LLM is faked at the retry wrapper; merge_rank's sections are pure code.
"""

from __future__ import annotations

import copy
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.memory_types import OutlineSection
from src.models.pipeline_types import QuizQuestion
from src.services.pipeline.extraction_chunks import ExtractionChunk
from src.services.pipeline.merge_rank import read_answer
from src.services.pipeline.merge_rank_apply import MergeRankAnswer, apply_merge_rank
from src.services.pipeline.merge_rank_render import MergeRankRequest, RenderInput, build_request
from src.services.pipeline.merge_rank_sections import section_names
from src.services.pipeline.quiz_v2 import QuizV2Request, enrich_quiz_v2
from src.services.pipeline.quiz_v2_ideas import quiz_targets
from src.services.pipeline.quiz_v2_select import select_quiz
from src.services.pipeline.quote_verify import QuoteIndex

_IDEAS_TAB = {"id": "ideas", "label": "Positions", "component": "spot_explorer",
              "dataSource": "learning.keyIdeas"}  # fmt: skip
_QA_TAB = {"id": "qa", "label": "Questions Asked", "component": "moment_track",
           "dataSource": "podcast.qa"}  # fmt: skip


def _section(start: int, end: int, title: str, establishes: str = "") -> OutlineSection:
    return OutlineSection(start=start, end=end, title=title, establishes=establishes)


def _idea(claim: str, seconds: int, label: str = "") -> dict[str, Any]:
    return {"claim": claim, "why": "because", "section": label, "timestamp": seconds}


def _chunks(duration: int) -> tuple[ExtractionChunk, ...]:
    return (ExtractionChunk(index=0, transcript="", end_s=duration, video_end_s=duration),)


def _sectioned(
    ideas: list[dict[str, Any]], outline: tuple[OutlineSection, ...], duration: int = 1200
) -> list[dict[str, Any]]:
    """The key ideas as merge_rank ships them when its call did not answer (sections only)."""
    extraction = {"learning": {"keyIdeas": ideas}}
    source = RenderInput(extraction, [_IDEAS_TAB], outline, _chunks(duration), duration)
    request = build_request(source)
    assert request is not None
    result, _ = apply_merge_rank(extraction, MergeRankAnswer("failed", request))
    return result["learning"]["keyIdeas"]


def _stamps(shipped: list[dict[str, Any]]) -> list[int]:
    return [idea["timestamp"] for idea in shipped]


# ─── B-W1: video order across sections ───

_PAIR = (
    _section(0, 600, "Pricing the subscription tiers", "annual plans beat monthly plans"),
    _section(600, 1200, "Hiring the first engineers", "recruiting senior engineers early"),
)
_PRICING, _HIRING = "Pricing the subscription tiers", "Hiring the first engineers"


class TestVideoOrderAcrossSections:
    def test_should_undo_a_move_back_when_it_would_pass_an_idea_left_in_its_section(
        self,
    ) -> None:
        ideas = [
            _idea("Annual plans beat monthly plans on pricing", 100),
            _idea("We hired two senior engineers first", 640),
            _idea("Annual pricing plans for subscription tiers win", 700),
            _idea("Senior engineers recruiting again", 900),
        ]

        shipped = _sectioned(ideas, _PAIR)

        assert _stamps(shipped) == [100, 640, 700, 900]

    def test_should_file_the_unmoved_idea_under_its_stamps_section_when_its_move_is_undone(
        self,
    ) -> None:
        ideas = [
            _idea("We hired two senior engineers first", 640),
            _idea("Annual pricing plans for subscription tiers win", 700),
        ]

        shipped = _sectioned([_idea("Annual plans beat monthly", 100), *ideas], _PAIR)

        assert next(i["section"] for i in shipped if i["timestamp"] == 700) == _HIRING

    def test_should_undo_a_move_forward_when_it_would_pass_an_idea_left_in_its_section(
        self,
    ) -> None:
        ideas = [
            _idea("Senior engineers recruiting starts early", 500),
            _idea("Annual plans beat monthly plans on pricing", 550),
            _idea("Hiring the first senior engineers", 900),
        ]

        shipped = _sectioned(ideas, _PAIR)

        assert [(i["timestamp"], i["section"]) for i in shipped] == [
            (500, _PRICING),
            (550, _PRICING),
            (900, _HIRING),
        ]

    def test_should_still_move_an_idea_across_the_edge_when_no_idea_stays_between(self) -> None:
        ideas = [
            _idea("Annual plans beat monthly plans on pricing", 100),
            _idea("Annual pricing plans for subscription tiers win", 640),
            _idea("We hired two senior engineers first", 700),
        ]

        shipped = _sectioned(ideas, _PAIR)

        assert [i["section"] for i in shipped] == [_PRICING, _PRICING, _HIRING]


# S02 p4v: outline sections 10 and 11 as merge_rank read them, and the four key ideas
# around their edge with the extraction's own labels.
_S02_DURATION = 4978
_S02_OUTLINE = (
    _section(3840, 4200, "quantum computing, love and human-AI connection",
             "quantum effects are unlikely in brains, and meaning comes from what we give "
             "the universe"),
    _section(4200, 4978, "optimism, cosmic future and the role of AI",
             "we should set positive goals for AI and expand life beyond Earth"),
)  # fmt: skip
_S02_IDEAS = [
    _idea("We don't need quantum computers to build AGI, and the brain is probably not a "
          "quantum computer", 3940, "Quantum computing"),
    _idea("We should aim for a future that keeps subjective experience, passion, inspiration "
          "and love, because meaning comes from us giving it to the universe", 4241,
          "Future and meaning"),
    _idea("Forecasts for AGI vary widely, but whether it happens is not for us to watch "
          "passively: we are creating the future and should ask what future we want", 4200,
          "Future and meaning"),
    _idea("Panicking about AI risk helps nothing, but neither does dismissing it — ask what "
          "we can do today that will actually help", 4366, "How to think about AI risk"),
]  # fmt: skip


class TestS02Regression:
    def test_should_list_4200_before_4241_when_s02s_edge_move_would_cross_it(self) -> None:
        shipped = _sectioned(copy.deepcopy(_S02_IDEAS), _S02_OUTLINE, _S02_DURATION)

        assert _stamps(shipped) == [3940, 4200, 4241, 4366]


# ─── B-W2: one question per idea before any second ───

_LINES = {
    30: "A card at 24 percent costs you more than any index fund returns at seven percent.",
    160: "Then and only then the same dollar goes to the index fund every single month.",
    250: "Remember the card at twenty four percent eats every return the fund could give you.",
    520: "Keep one month of expenses in cash so a surprise never lands on the card again.",
}
_SEGMENTS = [{"start": second, "text": text} for second, text in _LINES.items()]
_TRANSCRIPT = "\n".join(f"[{s // 60}:{s % 60:02d}] {text}" for s, text in _LINES.items())
_TARGETS = [
    {"claim": "Pay the card first", "why": "24% beats 7%", "timestamp": 30, "kind": "argument"},
    {"claim": "Fund second", "why": "Only then", "timestamp": 160, "kind": "thesis"},
    {"claim": "One month of cash", "why": "A buffer", "timestamp": 520, "kind": "number"},
]


def _item(name: str, second: int, idea: str) -> dict[str, Any]:
    return {
        "idea": idea,
        "question": f"What about {name}?",
        "options": [f"{name} a", f"{name} b", f"{name} c", f"{name} d"],
        "correctIndex": 0,
        "explanation": "Because.",
        "quote": _LINES[second],
        "timestamp": f"{second // 60}:{second % 60:02d}",
    }


async def _shipped_and_spares(*items: dict[str, Any]) -> tuple[list[str], list[str]]:
    request = QuizV2Request(
        primary_tag="learning",
        title="Pay the card first",
        duration_s=600,
        transcript=_TRANSCRIPT,
        extraction={"learning": {"keyIdeas": _TARGETS}},
    )
    reply = json.dumps({"quiz": list(items)})
    with patch(
        "src.services.pipeline.quiz_v2.call_llm_with_retry", new=AsyncMock(return_value=reply)
    ):
        result = await enrich_quiz_v2(MagicMock(), request, QuoteIndex.build(_SEGMENTS))
    assert result is not None
    return [q.question for q in result.quiz], [q.question for q in result.spares]


def _question(n: int, timestamp: int, key: str = "Answer") -> QuizQuestion:
    return QuizQuestion.model_validate(
        {
            "question": f"Why does the speaker say so (#{n})?",
            "options": [key, "Wrong one A", "Wrong one B", "Wrong one C"],
            "correctIndex": 0,
            "explanation": "The speaker gives this reason.",
            "timestamp": timestamp,
        }
    )


class TestOneQuestionPerIdea:
    async def test_should_ship_every_idea_once_when_a_repeat_alone_covers_a_third(self) -> None:
        # Thirds of 10 min: the K1 repeat at 4:10 is the middle third's only question.
        shipped, spares = await _shipped_and_spares(
            _item("card", 30, "K1"),
            _item("fund", 160, "K2"),
            _item("cash", 520, "K3"),
            _item("card again", 250, "K1"),
        )

        assert (shipped, spares) == (
            ["What about card?", "What about fund?", "What about cash?"],
            ["What about card again?"],
        )

    def test_should_leave_an_untested_outline_section_to_a_first_question_over_a_repeat(
        self,
    ) -> None:
        questions = [_question(0, 100), _question(1, 150), _question(2, 400)]

        shipped, _ = select_quiz(questions, 2, [], outline=(0, 300), repeats={2})

        assert [q.timestamp for q in shipped] == [100, 150]

    def test_should_ship_a_flagged_first_question_before_a_clean_repeat(self) -> None:
        told = _question(1, 150, key="An answer far longer than any of the wrong options")
        questions = [_question(0, 100), told, _question(2, 200)]

        shipped, _ = select_quiz(questions, 2, [], repeats={2})

        assert [q.timestamp for q in shipped] == [100, 150]


# ─── B-W4: Q&A twins inside the Q&A list ───

_PODCAST_S = 5400  # a timeline of 16 clears its band minimum (12) and fits its cap (18)


def _qa_request() -> tuple[dict[str, Any], MergeRankRequest]:
    positions = [_idea(f"position {k}", 450 * k, f"c{k}") for k in range(10)]
    questions = [
        {"question": f"question {k}", "answer": f"answer {k}", "timestamp": 300 * k + 50}
        for k in range(16)
    ]
    extraction = {"learning": {"keyIdeas": positions}, "podcast": {"qa": questions}}
    source = RenderInput(extraction, [_IDEAS_TAB, _QA_TAB], (), _chunks(_PODCAST_S), _PODCAST_S)
    request = build_request(source)
    assert request is not None
    return extraction, request


class TestQaTwinsUnderAPosition:
    def test_should_drop_a_qa_twin_of_the_held_question_when_the_group_keeps_a_position(
        self,
    ) -> None:
        extraction, request = _qa_request()
        data = {"duplicates": [{"keep": "K3", "drop": ["Q3", "Q4"]}]}

        result, record = apply_merge_rank(copy.deepcopy(extraction), read_answer(data, request))

        assert (
            len(result["podcast"]["qa"]),
            [(d["id"], d["keep"]) for d in record["dropped"]],
        ) == (
            15,
            [("Q4", "Q3")],
        )

    def test_should_hold_the_groups_first_qa_drop_when_the_group_keeps_a_position(self) -> None:
        extraction, request = _qa_request()
        data = {"duplicates": [{"keep": "K3", "drop": ["Q3", "Q4"]}]}

        _, record = apply_merge_rank(copy.deepcopy(extraction), read_answer(data, request))

        assert [(h["id"], h["guard"]) for h in record["held"]] == [("Q3", "qa_twin")]


# ─── B-W5: section titles ───


def _names(*titles: str) -> list[str]:
    return section_names([_section(n, n + 1, title) for n, title in enumerate(titles)])


class TestSectionTitles:
    def test_should_keep_two_cut_titles_apart_when_they_share_a_lead(self) -> None:
        names = _names(
            "audience Q&A: AI in medicine, longevity drugs and the health system…",
            "audience Q&A: exercise, sleep, protein targets and what to measure…",
        )

        assert names == [
            "Audience Q&A: AI in medicine, longevity drugs",
            "Audience Q&A: exercise, sleep, protein targets",
        ]

    def test_should_fall_back_to_the_cut_text_when_the_phrase_cut_still_repeats(self) -> None:
        names = _names("audience Q&A: health, sleep…", "audience Q&A: health, diet…")

        assert names == ["Audience Q&A: health, sleep…", "Audience Q&A: health, diet…"]

    def test_should_keep_a_shared_lead_when_one_title_was_not_cut(self) -> None:
        names = _names("Audience Q&A", "audience Q&A: exercise, sleep, protein targets and what…")

        assert names == ["Audience Q&A", "Audience Q&A: exercise, sleep, protein targets"]

    def test_should_keep_a_closing_acronym_when_it_strips_a_cut_title(self) -> None:
        names = _names("how large language models will change programming jobs with AI…")

        assert names == ["How large language models will change programming jobs with AI"]


# ─── A-W2: disagreements are quiz targets ───


class TestDisagreementTargets:
    def test_should_target_a_disagreement_key_idea(self) -> None:
        ideas = [
            {"claim": "AI will not replace doctors", "kind": "disagreement", "timestamp": 60},
            {"claim": "The host's dog barked", "kind": "story", "timestamp": 90},
            {"claim": "Metformin is overrated", "kind": "argument", "timestamp": 120},
        ]

        targets = quiz_targets("podcast", {"learning": {"keyIdeas": ideas}})

        assert [target.seconds for target in targets] == [60, 120]
