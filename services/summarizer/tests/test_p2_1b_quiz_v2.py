"""Quiz v2 — every answer proved by the speaker's words (output-quality p2.1b, D9 / D13).

One Haiku 5.5 call (``LLM_MEMORY_MODEL``, stage ``quiz_v2``) reads the marked
transcript + the key ideas and writes 3 / 5 / 8 questions (plus spares), each with
its answer's stamp and a ≤ 25-word evidence quote. The 2.0 quote check keeps a
question only when its quote is in the transcript (the stamp is then set from where
it was found); fewer than 3 survivors = no quiz. ``QUIZ_V2_ENABLED=false`` keeps the
flag-off ``enrich_quiz``. The LLM is faked at the provider seam or the retry wrapper.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import settings
from src.models.pipeline_types import EnrichmentData, PlanResult, QuizQuestion
from src.services.llm import LLMService
from src.services.llm_provider import LLMProvider
from src.services.pipeline.assembly.normalizers import _normalize_quiz_question
from src.services.pipeline.phases import enrichment as enrichment_phase
from src.services.pipeline.quiz_v2 import QuizV2Request, clip_quote, enrich_quiz_v2
from src.services.pipeline.quiz_v2_prompt import build_quiz_v2_prompts
from src.services.pipeline.quote_verify import QUOTE_TRANSLATED_KEY, QuoteIndex
from tests.replay.quiz_synth import quiz_v2_answer, transcript_lines

_PATCH_LLM = "src.services.pipeline.quiz_v2.call_llm_with_retry"
_HAIKU_55 = "anthropic/claude-haiku-5-5"
_TEN_MIN, _THIRTY_MIN, _HOUR_PLUS = 600, 1800, 3780
_LONG_LINE = (
    "Every spare dollar goes to the card first because the interest compounds against you "
    "every single month while the fund only compounds for you when the market agrees to it"
)
_SEGMENTS = [
    {"start": 0, "text": "Welcome back. Today: why you pay the card before you invest."},
    {"start": 30, "text": "A card at 24% costs you more than any index fund returns at 7%."},
    {"start": 95, "text": _LONG_LINE},
    {"start": 160, "text": "Then, and only then, the same dollar goes to the index fund."},
    {
        "start": 240,
        "text": "Keep one month of expenses in cash so a surprise never lands on the card.",
    },
]
_TRANSCRIPT = "\n".join(
    f"[{s['start'] // 60}:{s['start'] % 60:02d}] {s['text']}" for s in _SEGMENTS
)
_EXTRACTION = {
    "learning": {
        "keyIdeas": [
            {"claim": "Pay the card before investing", "why": "24% beats 7%", "timestamp": 30},
            {
                "claim": "Keep a cash buffer",
                "why": "A surprise never hits the card",
                "timestamp": 240,
            },
        ]
    }
}
_QUOTES = {
    30: "A card at 24% costs you more than any index fund returns at 7%.",
    160: "Then, and only then, the same dollar goes to the index fund.",
    240: "Keep one month of expenses in cash so a surprise never lands on the card.",
    95: "Every spare dollar goes to the card first",
    0: "why you pay the card before you invest",
}
_FABRICATED = "Index funds always lose money in a bad year, so never buy them."


def _question(n: int, quote: str | None, timestamp: int | None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "question": f"Why does the speaker pay the card first (#{n})?",
        "options": ["24% beats 7%", "Cards are forgiven", "Funds never grow", "The bank asks"],
        "correctIndex": 0,
        "explanation": "The card's interest costs more than the fund returns.",
        "timestamp": timestamp,
    }
    if quote is not None:
        item["quote"] = quote
    return item


def _verified(count: int) -> list[dict[str, Any]]:
    stamps = list(_QUOTES)
    return [_question(n, _QUOTES[stamps[n % 5]], stamps[n % 5]) for n in range(count)]


def _reply(*questions: dict[str, Any]) -> str:
    return json.dumps({"quiz": list(questions)})


def _request(duration_s: float | None = _TEN_MIN, **overrides: Any) -> QuizV2Request:
    fields: dict[str, Any] = {
        "primary_tag": "learning",
        "title": "Pay the card first",
        "duration_s": duration_s,
        "transcript": _TRANSCRIPT,
        "extraction": _EXTRACTION,
        "hero": "title: Pay the card first\ntldr: Debt before investing.",
    }
    fields.update(overrides)
    return QuizV2Request(**fields)


def _index(language: str | None = None) -> QuoteIndex:
    return QuoteIndex.build(_SEGMENTS, language)


async def _quiz(
    reply: str | None, duration_s: float | None = _TEN_MIN, language: str | None = None
) -> tuple[EnrichmentData | None, AsyncMock]:
    llm = AsyncMock(return_value=reply)
    with patch(_PATCH_LLM, new=llm):
        result = await enrich_quiz_v2(MagicMock(), _request(duration_s), _index(language))
    return result, llm


def _prompts(**overrides: Any) -> list[str]:
    return [call.text for call in build_quiz_v2_prompts(_request(**overrides))]


def _prompt(**overrides: Any) -> str:
    """The first call's prompt: a 10-minute video's 3-question quiz is one call over the
    whole video (p3 review A6); a longer one is written stretch by stretch."""
    first, *_ = _prompts(**overrides)
    return first


# ─── The request ───


class _FakeProvider:
    """``acompletion`` at the provider seam: records the request, answers ``reply``."""

    def __init__(self, reply: str) -> None:
        self.requests: list[dict[str, Any]] = []
        self._reply = reply

    async def acompletion(self, **kwargs: Any) -> MagicMock:
        self.requests.append(kwargs)
        response = MagicMock()
        response.choices = [MagicMock(finish_reason="stop", message=MagicMock(content=self._reply))]
        response.usage = SimpleNamespace(
            prompt_tokens=20_000,
            completion_tokens=900,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        response.model = _HAIKU_55.split("/", 1)[1]
        return response


class TestRequest:
    async def test_should_send_each_call_as_one_uncached_user_message_to_haiku(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "LLM_MEMORY_MODEL", _HAIKU_55)
        fake = _FakeProvider(_reply(*_verified(5)))
        service = LLMService(LLMProvider(model=_HAIKU_55, fast_model=_HAIKU_55))
        # Key ideas in two thirds of a 5-question video: two stretch calls (p3.0).
        ideas = [{"claim": "Card first", "timestamp": 30}, {"claim": "Buffer", "timestamp": 900}]
        request = _request(_THIRTY_MIN, extraction={"learning": {"keyIdeas": ideas}})

        with patch("src.services.llm_provider.acompletion", fake.acompletion):
            await enrich_quiz_v2(service, request, _index())

        assert [(r["model"], [m["role"] for m in r["messages"]]) for r in fake.requests] == [
            (_HAIKU_55, ["user"]),
            (_HAIKU_55, ["user"]),
        ]
        assert all(isinstance(r["messages"][0]["content"], str) for r in fake.requests)
        assert "cache_control" not in json.dumps([r["messages"] for r in fake.requests])

    async def test_should_call_stage_quiz_v2_on_the_fast_tier_with_one_retry_when_the_quiz_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "LLM_MEMORY_MODEL", _HAIKU_55)

        _, llm = await _quiz(_reply(*_verified(3)))

        kwargs = llm.await_args_list[-1].kwargs
        assert (kwargs["stage_name"], kwargs["model_override"], kwargs["max_retries"]) == (
            "quiz_v2",
            _HAIKU_55,
            1,
        )
        assert kwargs["use_fast_model"] and kwargs["json_mode"]

    def test_should_route_the_quiz_to_the_memory_model_when_it_is_set(self) -> None:
        assert settings.get_stage_model("quiz_v2") == settings.get_stage_model("memory")

    def test_should_follow_memory_s_fallback_when_the_memory_setting_is_blank(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "LLM_MEMORY_MODEL", None)

        assert settings.get_stage_model("quiz_v2") == settings.get_stage_model("extraction")

    def test_should_put_the_transcript_and_key_ideas_before_the_rules(self) -> None:
        prompt = _prompt()

        order = [prompt.index(tag) for tag in ("<key_ideas>", "<transcript>", "<quiz_rules>")]
        assert order == sorted(order) and _TRANSCRIPT.splitlines()[0] in prompt

    def test_should_render_each_key_idea_with_its_id_marker_and_reason(self) -> None:
        assert "- K1 [0:30] Pay the card before investing — 24% beats 7%" in _prompt()

    def test_should_carry_the_hero_no_question_may_be_answerable_from(self) -> None:
        assert "tldr: Debt before investing." in _prompt()

    async def test_should_make_no_call_when_the_run_has_no_transcript_index(self) -> None:
        llm = AsyncMock(return_value=_reply(*_verified(3)))

        with patch(_PATCH_LLM, new=llm):
            result = await enrich_quiz_v2(MagicMock(), _request(), None)

        assert result is None and not llm.await_count

    def test_should_build_no_prompt_when_the_transcript_is_empty(self) -> None:
        assert build_quiz_v2_prompts(_request(transcript="  ")) == []


# ─── Count by duration ───


class TestCount:
    @pytest.mark.parametrize(
        ("duration_s", "asked"), [(_TEN_MIN, 7), (_THIRTY_MIN, 9), (_HOUR_PLUS, 12)]
    )
    def test_should_ask_the_videos_count_plus_four_spares_across_the_calls_when_the_length_is_known(
        self, duration_s: int, asked: int
    ) -> None:
        calls = build_quiz_v2_prompts(_request(duration_s=duration_s))

        assert sum(call.asked for call in calls) == asked

    @pytest.mark.parametrize(("duration_s", "count"), [(_TEN_MIN, 3), (_THIRTY_MIN, 5)])
    async def test_should_ship_at_most_the_videos_count_when_spares_pass(
        self, duration_s: int, count: int
    ) -> None:
        result, _ = await _quiz(_reply(*_verified(count + 2)), duration_s)

        assert result is not None and len(result.quiz) == count


# ─── The quote check ───


class TestQuoteCheck:
    async def test_should_drop_a_question_whose_quote_is_not_in_the_transcript(self) -> None:
        reply = _reply(*_verified(3), _question(9, _FABRICATED, 30))

        result, _ = await _quiz(reply, _THIRTY_MIN)

        assert result is not None and _FABRICATED not in [q.quote for q in result.quiz]

    async def test_should_drop_a_question_without_a_quote(self) -> None:
        reply = _reply(*_verified(3), _question(9, None, 30))

        result, _ = await _quiz(reply, _THIRTY_MIN)

        assert result is not None and len(result.quiz) == 3

    async def test_should_set_the_stamp_where_the_quote_is_said_when_the_model_is_minutes_off(
        self,
    ) -> None:
        reply = _reply(_question(0, _QUOTES[160], 500), *_verified(3)[1:])

        result, _ = await _quiz(reply)

        assert result is not None and result.quiz[0].timestamp == 160

    async def test_should_set_the_stamp_where_the_quote_is_said_when_the_model_names_a_near_marker(
        self,
    ) -> None:
        reply = _reply(_question(0, _QUOTES[240], 225), *_verified(3)[1:])

        result, _ = await _quiz(reply)

        assert result is not None and result.quiz[0].timestamp == 240

    async def test_should_set_a_missing_stamp_from_the_located_quote(self) -> None:
        reply = _reply(_question(0, _QUOTES[95], None), *_verified(3)[1:])

        result, _ = await _quiz(reply)

        assert result is not None and result.quiz[0].timestamp == 95

    async def test_should_keep_a_long_quote_cut_to_25_words_when_its_words_are_said(self) -> None:
        reply = _reply(_question(0, _LONG_LINE, 95), *_verified(3)[1:])

        result, _ = await _quiz(reply)

        assert result is not None and result.quiz[0].quote == clip_quote(_LONG_LINE)

    def test_should_mark_the_cut_when_a_quote_runs_past_25_words(self) -> None:
        clipped = clip_quote(_LONG_LINE)

        assert len(clipped.rstrip("…").split()) == 25 and clipped.endswith("…")

    async def test_should_ship_no_quiz_when_fewer_than_three_questions_survive(self) -> None:
        reply = _reply(*_verified(2), _question(7, _FABRICATED, 30), _question(8, None, 95))

        result, _ = await _quiz(reply, _THIRTY_MIN)

        assert result is None

    async def test_should_ship_no_quiz_when_the_reply_is_not_json(self) -> None:
        result, _ = await _quiz("no quiz today")

        assert result is None

    async def test_should_ship_no_quiz_when_the_call_fails(self) -> None:
        result, _ = await _quiz(None)

        assert result is None


class TestTranslatedSource:
    async def test_should_stamp_an_unlocated_quote_at_its_key_idea_when_the_source_is_not_english(
        self,
    ) -> None:
        reply = _reply(*(_question(n, _FABRICATED, 30 + n) for n in range(3)))

        result, _ = await _quiz(reply, language="he")

        assert result is not None and [q.timestamp for q in result.quiz] == [30, 30, 30]

    async def test_should_drop_an_unlocated_quote_whose_stamp_is_past_the_end(self) -> None:
        reply = _reply(
            *(_question(n, _FABRICATED, 30 + n) for n in range(3)), _question(9, _FABRICATED, 5000)
        )

        result, _ = await _quiz(reply, _THIRTY_MIN, language="he")

        assert result is not None and 5000 not in [q.timestamp for q in result.quiz]

    async def test_should_drop_an_unlocated_quote_without_a_stamp(self) -> None:
        reply = _reply(
            *(_question(n, _FABRICATED, 30 + n) for n in range(3)), _question(9, _FABRICATED, None)
        )

        result, _ = await _quiz(reply, _THIRTY_MIN, language="he")

        assert result is not None and len(result.quiz) == 3


# Key ideas per domain: ``test_p2_1b_quiz_v2_ideas.py`` (split out at the p3 review, A8).


# ─── The phase: flag on → v2, flag off → today's call ───


def _ctx() -> Any:
    plan = PlanResult.model_validate(
        {
            "primaryTag": "learning",
            "contentTags": ["learning"],
            "tabs": [{"id": "quiz", "component": "quiz_arena", "dataSource": "enrichment.quiz"}],
        }
    )
    return SimpleNamespace(
        video_summary_id="vsid",
        plan_result=plan,
        content_format=None,
        extraction_data=_EXTRACTION,
        video_memory="",
        enrichment_data=None,
        llm_service=MagicMock(),
        quiz_task=None,
        video_data=SimpleNamespace(duration=_TEN_MIN, title="Pay the card first"),
        memory=None,
        prompt_transcript=_TRANSCRIPT,
        transcript_data=SimpleNamespace(segments=_SEGMENTS),
        source_language_code=None,
        quote_index=None,
    )


async def _phase(flag: bool, monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, AsyncMock]:
    monkeypatch.setattr(settings, "QUIZ_V2_ENABLED", flag)
    v1, v2 = AsyncMock(return_value=None), AsyncMock(return_value=None)
    with (
        patch.object(enrichment_phase, "enrich_quiz", v1),
        patch.object(enrichment_phase, "enrich_quiz_v2", v2),
    ):
        _ = [event async for event in enrichment_phase.run_phase_enrichment(_ctx())]
    return v1, v2


class TestPhaseWiring:
    async def test_should_call_quiz_v2_and_not_the_old_quiz_when_the_flag_is_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        v1, v2 = await _phase(True, monkeypatch)

        assert (v1.await_count, v2.await_count) == (0, 1)

    async def test_should_call_the_old_quiz_when_the_flag_is_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        v1, v2 = await _phase(False, monkeypatch)

        assert (v1.await_count, v2.await_count) == (1, 0)

    async def test_should_hand_quiz_v2_the_run_transcript_and_its_quote_index(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, v2 = await _phase(True, monkeypatch)

        _, request, index = v2.await_args_list[-1].args
        assert request.transcript == _TRANSCRIPT and isinstance(index, QuoteIndex)


# ─── Shapes: the stored question and the quiz_arena item ───


class TestShapes:
    def test_should_read_a_stored_question_without_a_quote(self) -> None:
        question = QuizQuestion.model_validate(_question(0, None, 30))

        assert question.quote is None

    def test_should_read_a_blank_quote_as_no_quote(self) -> None:
        assert QuizQuestion.model_validate(_question(0, "   ", 30)).quote is None

    def test_should_pass_the_quote_to_the_quiz_arena_item(self) -> None:
        item = _normalize_quiz_question(_question(0, _QUOTES[30], 30))

        assert item is not None and item["quote"] == _QUOTES[30]

    def test_should_flag_a_translated_quote_on_the_quiz_arena_item(self) -> None:
        raw = {**_question(0, "An English rendering", 30), QUOTE_TRANSLATED_KEY: True}

        item = _normalize_quiz_question(raw)

        assert item is not None and item[QUOTE_TRANSLATED_KEY] is True

    def test_should_ship_no_quote_key_when_the_check_cleared_the_quote(self) -> None:
        item = _normalize_quiz_question({**_question(0, None, 30), "quote": None})

        assert item is not None and "quote" not in item


# ─── The replay harness answers quiz v2 from a recorded quiz ───


class TestReplaySynth:
    def test_should_quote_the_transcript_line_at_a_stampless_questions_place(self) -> None:
        lines = transcript_lines(f"<transcript>\n{_TRANSCRIPT}\n</transcript>")
        recorded = {"quiz": [_question(0, None, None)]}

        (answered,) = quiz_v2_answer(recorded, lines)["quiz"]

        assert (
            answered["quote"]
            and _index().verify(answered["quote"]).seconds == answered["timestamp"]
        )
