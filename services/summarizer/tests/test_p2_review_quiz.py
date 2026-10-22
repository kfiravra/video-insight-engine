"""Phase-2 review fixes, the quiz lane (output-quality p2 review D6 / D7 / D8 / A5 / B7).

* D6 — quiz v2 asks for no "Which is NOT / FALSE" question: its keyed option is
  something the video never says, so no quote can prove it;
* D7 — once the hero and the quiz both landed, a quiz v2 question whose keyed option
  restates a shipped hero takeaway goes and the spares refill (flag-off hero = memory's
  takeaways); the flag-off quiz stays today's;
* D8 — both quiz prompts ask for the marker as written ("m:ss"), the code converts;
* A5 — a bare "(N)" in an explanation is a clock only after at / around / from or when
  it agrees with the stamp; never 1–2 digits, never a 1000–2099 year off the stamp;
* B7 part 2 — a question without a quote dumps no ``"quote"`` key (wire + stored).

No LLM, no I/O: the quiz call is faked at the retry wrapper or the phase seam.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import settings
from src.models.memory_types import MemoryResult
from src.models.pipeline_types import EnrichmentData, QuizQuestion
from src.services.pipeline.assembly.quiz_stamps import reconcile_quiz_time, with_reconciled_quiz
from src.services.pipeline.enrichment import build_quiz_prompt, parse_quiz
from src.services.pipeline.phases import assembly as phase
from src.services.pipeline.phases import enrichment as enrichment_phase
from src.services.pipeline.phases import synthesis as synthesis_phase
from src.services.pipeline.quiz_v2 import QuizV2Request, enrich_quiz_v2, restates_takeaway
from src.services.pipeline.quiz_v2_prompt import build_quiz_v2_prompts
from src.services.pipeline.quote_verify import QuoteIndex
from tests.test_phase_assembly_order import _SYNTHESIS, _events, _saved, _tech_ctx

_PATCH_LLM = "src.services.pipeline.quiz_v2.call_llm_with_retry"
_TEN_MIN, _FORTY_MIN, _HOUR_PLUS = 600, 2400, 3780
_SEGMENTS = [
    {"start": 0, "text": "Welcome back. Today: why you pay the card before you invest."},
    {"start": 30, "text": "A card at 24% costs you more than any index fund returns at 7%."},
    {"start": 160, "text": "Then, and only then, the same dollar goes to the index fund."},
    {"start": 240, "text": "Keep one month of expenses in cash so a surprise never lands."},
]
_TRANSCRIPT = "\n".join(
    f"[{s['start'] // 60}:{s['start'] % 60:02d}] {s['text']}" for s in _SEGMENTS
)
_FABRICATED = "Index funds always lose money in a bad year, so never buy them."
# S01's p2i1 page (Rails World keynote): the shipped takeaway 6 and the quiz question 7
# whose keyed option restates it, plus a question on a takeaway's topic that does not.
_S01_CLI_TAKEAWAY = (
    "Apps should ship a CLI so users bring their own agent, instead of embedding a "
    "chatbot concierge. [45:23]"
)
_S01_CLI_ANSWER = "Let users bring their own agent by shipping a CLI"
_S01_RUST_TAKEAWAY = (
    "Treat agent output, even Rust, as a black box and judge the result from the outside "
    "rather than reading every line. [35:46]"
)
_S01_RUST_ANSWER = "He never has to look at the code, and agents like writing it"


def _question(n: int, answer: str = "", **fields: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "question": f"What does the speaker say (#{n})?",
        "options": [answer or f"Answer {n}", "Cards are forgiven", "Funds never grow", "Banks ask"],
        "correctIndex": 0,
        "explanation": "The card's interest costs more than the fund returns.",
        "timestamp": 30 + n,
    }
    item.update(fields)
    return item


def _dumped(n: int, answer: str = "") -> dict[str, Any]:
    """A checked quiz v2 question as the record stores it."""
    quote = _SEGMENTS[1]["text"]
    return QuizQuestion.model_validate(_question(n, answer, quote=quote)).model_dump(by_alias=True)


# One key idea per quoted line: a question tests the idea stamped near it (4.0 b).
_IDEAS = [
    {"claim": "Card first", "timestamp": 30},
    {"claim": "Fund second", "timestamp": 160},
    {"claim": "Cash buffer", "timestamp": 240},
]


def _request(
    duration_s: float = _TEN_MIN, ideas: list[dict[str, Any]] | None = None
) -> QuizV2Request:
    return QuizV2Request(
        primary_tag="learning",
        title="Pay the card first",
        duration_s=duration_s,
        transcript=_TRANSCRIPT,
        extraction={"learning": {"keyIdeas": _IDEAS if ideas is None else ideas}},
    )


async def _quiz_v2(
    reply: list[dict[str, Any]],
    language: str | None = None,
    ideas: list[dict[str, Any]] | None = None,
) -> EnrichmentData:
    with patch(_PATCH_LLM, new=AsyncMock(return_value=json.dumps({"quiz": reply}))):
        result = await enrich_quiz_v2(
            MagicMock(), _request(ideas=ideas), QuoteIndex.build(_SEGMENTS, language)
        )
    assert result is not None
    return result


def _example(prompt: str) -> dict[str, Any]:
    line = next(line for line in prompt.splitlines() if line.startswith('{"quiz"'))
    return json.loads(line)["quiz"][0]


def _v1_prompt() -> str:
    prompt = build_quiz_prompt("learning", {}, "", [], duration_s=_TEN_MIN)
    assert prompt is not None
    return prompt


def _v2_prompt() -> str:
    (call,) = build_quiz_v2_prompts(_request())
    return call.text


# ─── D6: no "Which is NOT / FALSE" question in quiz v2 ───


class TestNoNegativeQuestion:
    @pytest.mark.parametrize("phrase", ["Which is NOT", "Which is FALSE"])
    def test_should_not_ask_for_a_negative_question_when_quiz_v2_is_prompted(
        self, phrase: str
    ) -> None:
        assert phrase not in _v2_prompt()


# ─── D8: the marker as written, the code converts ───


class TestMarkerAsWritten:
    def test_should_show_a_clock_string_stamp_when_quiz_v2_gives_its_example(self) -> None:
        assert _example(_v2_prompt())["timestamp"] == "4:32"

    def test_should_not_ask_quiz_v2_to_convert_the_marker_to_seconds(self) -> None:
        assert "in SECONDS" not in _v2_prompt()

    def test_should_ask_quiz_v2_to_copy_the_marker_as_written(self) -> None:
        assert "copied as written" in _v2_prompt()

    def test_should_show_a_clock_string_stamp_when_the_flag_off_quiz_gives_its_example(
        self,
    ) -> None:
        assert _example(_v1_prompt())["timestamp"] == "4:32"

    def test_should_not_ask_the_flag_off_quiz_to_convert_the_marker_to_seconds(self) -> None:
        assert "in SECONDS" not in _v1_prompt()

    @pytest.mark.parametrize(("marker", "seconds"), [("3:22", 202), ("1:02:05", 3725)])
    def test_should_read_the_marker_as_seconds_when_the_flag_off_quiz_copies_it(
        self, marker: str, seconds: int
    ) -> None:
        (question,) = parse_quiz({"quiz": [_question(0, timestamp=marker)]}, 8, _HOUR_PLUS)

        assert question.timestamp == seconds

    async def test_should_set_the_stamp_from_the_quote_when_quiz_v2_copies_a_marker(
        self,
    ) -> None:
        quotes = [s["text"] for s in _SEGMENTS[1:]]
        reply = [_question(n, quote=quote, timestamp="0:00") for n, quote in enumerate(quotes)]

        result = await _quiz_v2(reply)

        assert [q.timestamp for q in result.quiz] == [30, 160, 240]

    async def test_should_read_the_marker_as_seconds_when_a_translated_quote_keeps_it(
        self,
    ) -> None:
        # Its idea has no stamp and no quote, so the translated quote keeps the model's.
        reply = [_question(n, quote=_FABRICATED, timestamp=f"2:4{n}", idea="K1") for n in range(3)]

        result = await _quiz_v2(reply, language="he", ideas=[{"claim": "Card first"}])

        assert [q.timestamp for q in result.quiz] == [160, 161, 162]


# ─── A5: a bare "(N)" is a clock only with a lead-in or the stamp's agreement ───


class TestBareNumbers:
    def test_should_leave_a_year_in_parentheses_alone_when_the_video_is_long(self) -> None:
        text = "A study (2008) found the same effect."

        assert reconcile_quiz_time(500, text, _FORTY_MIN) == (500, text)

    def test_should_leave_a_year_alone_when_it_follows_a_lead_in(self) -> None:
        text = "At (2019), the study shows it."

        assert reconcile_quiz_time(100, text, 1500) == (100, text)

    def test_should_leave_list_numbers_alone(self) -> None:
        text = "Two levers: (1) stress and (2) sleep."

        assert reconcile_quiz_time(None, text, _FORTY_MIN)[1] == text

    def test_should_leave_a_two_digit_number_alone_when_it_equals_the_stamp(self) -> None:
        text = "At (15), he says so."

        assert reconcile_quiz_time(15, text, _FORTY_MIN) == (15, text)

    def test_should_leave_a_bare_number_alone_without_a_lead_in_or_the_stamps_agreement(
        self,
    ) -> None:
        text = "The test (450) proves it."

        assert reconcile_quiz_time(100, text, _TEN_MIN) == (100, text)

    @pytest.mark.parametrize("lead", ["at", "around", "from"])
    def test_should_read_a_bare_count_as_a_clock_when_a_lead_in_names_it(self, lead: str) -> None:
        assert reconcile_quiz_time(None, f"He says it {lead} (452).", _TEN_MIN)[1] == (
            f"He says it {lead} (7:32)."
        )

    def test_should_read_a_bare_count_as_a_clock_when_it_agrees_with_the_stamp(self) -> None:
        assert reconcile_quiz_time(455, "He says it (452).", _TEN_MIN)[1] == "He says it (7:32)."

    def test_should_read_a_year_range_count_as_a_clock_when_it_agrees_with_the_stamp(
        self,
    ) -> None:
        # G1-1: S03's explanation printed its own stamp as "(1322)".
        assert reconcile_quiz_time(1322, "At (1322), x.", 3434)[1] == "At (22:02), x."

    def test_should_keep_a_year_when_the_assembler_reconciles_a_stored_quiz(self) -> None:
        stored = {"quiz": [_question(0, explanation="A study (2008) found it.", timestamp=500)]}

        (item,) = with_reconciled_quiz(stored, _FORTY_MIN)["quiz"]

        assert item["explanation"] == "A study (2008) found it."


# ─── B7 part 2: no "quote": null on the flag-off wire or stored shape ───


class TestQuoteOmittedWhenUnset:
    def test_should_dump_no_quote_key_when_the_question_has_no_quote(self) -> None:
        dumped = QuizQuestion.model_validate(_question(0)).model_dump(by_alias=True)

        assert set(dumped) == {"question", "options", "correctIndex", "explanation", "timestamp"}

    def test_should_dump_the_quote_when_the_question_has_one(self) -> None:
        dumped = QuizQuestion.model_validate(_question(0, quote="He says so.")).model_dump()

        assert dumped["quote"] == "He says so."

    def test_should_serialize_no_quote_key_when_the_record_is_json(self) -> None:
        record = EnrichmentData(quiz=[QuizQuestion.model_validate(_question(0))])

        assert '"quote"' not in record.model_dump_json(by_alias=True)

    async def test_should_store_and_emit_no_quote_key_when_the_flag_off_quiz_lands(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "QUIZ_V2_ENABLED", False)
        result = EnrichmentData(quiz=[QuizQuestion.model_validate(_question(0))])
        ctx: Any = SimpleNamespace(quiz_task=None, extraction_data={}, enrichment_data=None)

        with patch.object(enrichment_phase, "_quiz", AsyncMock(return_value=result)):
            (event,) = [e async for e in enrichment_phase.run_phase_enrichment(ctx)]

        assert '"quote"' not in event and "quote" not in ctx.enrichment_data["quiz"][0]


# ─── D7: no shipped quiz question restates a shipped hero takeaway ───


class TestRestatesTakeaway:
    def test_should_flag_a_keyed_option_that_restates_a_takeaway(self) -> None:
        assert restates_takeaway(_S01_CLI_ANSWER, _S01_CLI_TAKEAWAY) is True

    def test_should_not_flag_an_answer_on_the_same_topic_in_other_words(self) -> None:
        assert restates_takeaway(_S01_RUST_ANSWER, _S01_RUST_TAKEAWAY) is False

    def test_should_flag_a_number_the_takeaway_states(self) -> None:
        takeaway = "The best programmer with agents versus the worst without is about 1,000x."

        assert restates_takeaway("About 1,000x", takeaway) is True


def _settle_ctx(
    quiz: list[dict[str, Any]], spares: list[dict[str, Any]], takeaways: list[str]
) -> Any:
    """A late-group context: the quiz record (spares in it) and the shipped hero."""
    record: dict[str, Any] = {"quiz": quiz}
    if spares:
        record[enrichment_phase.QUIZ_SPARES_KEY] = spares
    synthesis = {"brief": "The hero rewrite's brief.", "keyTakeaways": takeaways}
    return SimpleNamespace(
        enrichment_data=record, synthesis_dict=synthesis, video_summary_id="v", video_data=None
    )


def _keyed(record: dict[str, Any] | None) -> list[str]:
    assert record is not None
    return [q["options"][q["correctIndex"]] for q in record["quiz"]]


class TestSettleLateQuiz:
    def test_should_refill_from_a_spare_when_a_question_restates_a_shipped_takeaway(
        self,
    ) -> None:
        quiz = [_dumped(0), _dumped(1, _S01_CLI_ANSWER), _dumped(2)]
        ctx = _settle_ctx(quiz, [_dumped(3), _dumped(4)], [_S01_CLI_TAKEAWAY])

        enrichment_phase.settle_late_quiz(ctx)

        assert _keyed(ctx.enrichment_data) == ["Answer 0", "Answer 2", "Answer 3"]

    def test_should_ship_no_quiz_when_fewer_than_three_questions_are_left(self) -> None:
        quiz = [_dumped(0), _dumped(1, _S01_CLI_ANSWER), _dumped(2)]
        ctx = _settle_ctx(quiz, [], [_S01_CLI_TAKEAWAY])

        enrichment_phase.settle_late_quiz(ctx)

        assert ctx.enrichment_data is None

    def test_should_leave_a_short_quiz_alone_when_nothing_restates_the_hero(self) -> None:
        quiz = [_dumped(0), _dumped(1)]
        ctx = _settle_ctx(quiz, [], [_S01_CLI_TAKEAWAY])

        enrichment_phase.settle_late_quiz(ctx)

        assert _keyed(ctx.enrichment_data) == ["Answer 0", "Answer 1"]

    def test_should_take_the_spares_out_of_the_record_when_it_settles(self) -> None:
        ctx = _settle_ctx([_dumped(0), _dumped(1), _dumped(2)], [_dumped(3)], [])

        enrichment_phase.settle_late_quiz(ctx)

        assert enrichment_phase.QUIZ_SPARES_KEY not in ctx.enrichment_data

    def test_should_keep_the_flag_off_quiz_as_written_when_it_restates_the_hero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "QUIZ_V2_ENABLED", False)
        quiz = [_dumped(0), _dumped(1, _S01_CLI_ANSWER), _dumped(2)]
        ctx = _settle_ctx(quiz, [], [_S01_CLI_TAKEAWAY])

        enrichment_phase.settle_late_quiz(ctx)

        assert _keyed(ctx.enrichment_data) == ["Answer 0", _S01_CLI_ANSWER, "Answer 2"]


class TestQuizV2Spares:
    async def test_should_hand_the_checked_questions_past_the_count_on_as_spares(self) -> None:
        quote = _SEGMENTS[1]["text"]

        result = await _quiz_v2([_question(n, quote=quote) for n in range(5)])

        assert (len(result.quiz), len(result.spares)) == (3, 2)

    async def test_should_emit_no_spares_when_quiz_v2_lands(self) -> None:
        quiz = [QuizQuestion.model_validate(_dumped(n)) for n in range(4)]
        result = EnrichmentData(quiz=quiz[:3], spares=quiz[3:])
        ctx: Any = SimpleNamespace(quiz_task=None, extraction_data={}, enrichment_data=None)

        with patch.object(enrichment_phase, "_quiz", AsyncMock(return_value=result)):
            (event,) = [e async for e in enrichment_phase.run_phase_enrichment(ctx)]

        assert "spares" not in event and len(json.loads(event[6:])["quiz"]) == 3


# ─── D7 through assembly: the late group lands, then the quiz tab is built ───


def _quiz_ctx(memory_takeaways: list[str]) -> Any:
    ctx = _tech_ctx()
    ctx.memory = MemoryResult(tldr="Ship a CLI.", takeaways=memory_takeaways)
    ctx.quiz_task = None
    return ctx


def _late_quiz() -> EnrichmentData:
    """Quiz v2's answer: question 1 restates S01's CLI takeaway; two spares behind it."""
    quiz = [QuizQuestion.model_validate(_dumped(n)) for n in (0, 2, 3, 4)]
    restating = QuizQuestion.model_validate(_dumped(1, _S01_CLI_ANSWER))
    return EnrichmentData(quiz=[quiz[0], restating, quiz[1]], spares=quiz[2:])


def _hero_writer(hero: dict[str, Any]) -> Any:
    """The synthesis slot's writer, faked: the hero rewrite's meta lands in the late group."""

    async def _write(ctx: Any) -> str:
        ctx.synthesis_dict = hero
        return "hero_rewrite"

    return _write


async def _assemble_with_quiz(ctx: Any, hero: dict[str, Any] | None = None) -> list:
    with ExitStack() as stack:
        stack.enter_context(patch.object(phase, "needs_quiz", lambda *_a: True))
        stack.enter_context(
            patch.object(enrichment_phase, "_quiz", AsyncMock(return_value=_late_quiz()))
        )
        stack.enter_context(patch.object(phase, "fill_moment_frames", AsyncMock(return_value=0)))
        stack.enter_context(
            patch.object(synthesis_phase, "synthesize", AsyncMock(return_value=_SYNTHESIS))
        )
        stack.enter_context(patch.object(phase.settings, "REDIS_ENABLED", False))
        stack.enter_context(patch.object(phase.settings, "QDRANT_ENABLED", False))
        if hero is not None:
            stack.enter_context(
                patch.object(synthesis_phase, "_synthesis_dict", _hero_writer(hero))
            )
        return _events([chunk async for chunk in phase.run_phase_assembly(ctx)])


def _shipped_answers(ctx: Any) -> list[str]:
    tab = next(t for t in _saved(ctx)["tabs"] if t["component"] == "quiz_arena")
    return [q["options"][q["correctIndex"]] for q in tab["props"]["questions"]]


class TestLateQuizAgainstTheShippedHero:
    async def test_should_drop_a_question_restating_the_rewritten_hero_when_both_landed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "QUIZ_V2_ENABLED", True)
        ctx = _quiz_ctx(["Pay the card first. [0:30]"])
        hero = {"brief": "Brief.", "tldr": "Ship a CLI.", "keyTakeaways": [_S01_CLI_TAKEAWAY]}

        await _assemble_with_quiz(ctx, hero)

        assert _shipped_answers(ctx) == ["Answer 0", "Answer 2", "Answer 3"]

    async def test_should_check_memorys_takeaways_when_the_hero_rewrite_is_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "QUIZ_V2_ENABLED", True)
        monkeypatch.setattr(settings, "HERO_REWRITE_ENABLED", False)
        ctx = _quiz_ctx([_S01_CLI_TAKEAWAY])

        await _assemble_with_quiz(ctx)

        assert _shipped_answers(ctx) == ["Answer 0", "Answer 2", "Answer 3"]

    async def test_should_store_no_spares_when_the_run_is_saved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "QUIZ_V2_ENABLED", True)
        monkeypatch.setattr(settings, "HERO_REWRITE_ENABLED", False)
        ctx = _quiz_ctx([_S01_CLI_TAKEAWAY])

        await _assemble_with_quiz(ctx)

        assert list(_saved(ctx)["pipeline"]["enrichment"]) == ["quiz"]
