"""4.2 (D12): a non-English run's quotes stay verbatim, their English beside them.

On a non-English run the extraction copies every ``quote`` (and a ``podcast.qa``
question) verbatim; the line that asks for it rides only in a non-English run's block 1,
so an English run sends the same bytes as before. The English beside the line
(``quoteEn`` / ``questionEn``) is written by the translation phase since 4.6c, never
asked of the extraction; the models keep the two fields and leave them out of the dump
when unset, so an English run's extraction keeps its bytes too.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.models.domain_types import (
    LearningTimestamp,
    PodcastQuote,
    PodcastSegment,
    ReviewVerdict,
    TravelSpot,
    validate_domain_output,
)
from src.models.idea_types import Claim, LearningKeyIdea, PodcastQA
from src.services.pipeline.extraction_group_prompt import prefix_block, with_group_rules
from src.services.pipeline.extraction_prompt import (
    ExtractionPromptInput,
    build_extraction_prompt,
    build_extraction_template,
)
from src.services.pipeline.extractor import _build_prompt
from src.services.pipeline.triage import TriageResult
from src.utils.language_utils import (
    ENGLISH_OUTPUT_DIRECTIVE,
    SOURCE_QUOTE_DIRECTIVE,
    output_language_directive,
)

_HEBREW = "הכסף הוא רק הסכמה בין אנשים"
_ENGLISH = "Money is only an agreement between people"


def _input(**overrides: Any) -> ExtractionPromptInput:
    values: dict[str, Any] = {
        "content_tags": ["podcast", "learning"],
        "modifiers": [],
        "quality_rules": "QUALITY RULES TEXT",
        "title": "The history of inflation",
        "duration_seconds": 4260,
        "tabs": [],
    }
    values.update(overrides)
    return ExtractionPromptInput(**values)


# ─── The directive ───


class TestOutputLanguageDirective:
    def test_should_add_the_verbatim_quote_line_when_the_source_is_hebrew(self) -> None:
        directive = output_language_directive("he")

        assert directive == f"{ENGLISH_OUTPUT_DIRECTIVE}\n{SOURCE_QUOTE_DIRECTIVE}"

    @pytest.mark.parametrize("source", [None, "", "en"])
    def test_should_send_only_the_english_line_when_the_source_is_english(
        self, source: str | None
    ) -> None:
        assert output_language_directive(source) == ENGLISH_OUTPUT_DIRECTIVE

    def test_should_ask_for_no_quote_en_or_question_en_when_the_source_is_not_english(
        self,
    ) -> None:
        # 4.6c (G4-1): the translation phase writes the English from the verified line.
        directive = output_language_directive("he")

        assert "quoteEn" not in directive and "questionEn" not in directive


class TestExtractionPrompt:
    @pytest.mark.parametrize("source", [None, "en"])
    def test_should_keep_the_english_prompt_byte_identical_when_the_source_is_english(
        self, source: str | None
    ) -> None:
        assert build_extraction_template(_input(source_language=source)) == (
            build_extraction_template(_input())
        )

    def test_should_ask_for_no_quote_en_when_the_run_is_english(self) -> None:
        assert "quoteEn" not in build_extraction_template(_input())

    def test_should_open_the_rules_with_the_quote_line_when_the_source_is_not_english(
        self,
    ) -> None:
        system = build_extraction_prompt(_input(source_language="he")).system

        assert system.startswith(f"{ENGLISH_OUTPUT_DIRECTIVE}\n{SOURCE_QUOTE_DIRECTIVE}\n\n")

    def test_should_carry_the_quote_line_in_block_one_when_the_groups_and_hero_read_it(
        self,
    ) -> None:
        # The hero rewrite reads the group calls' block 1: the line must sit there, or a
        # non-English run's hero would lose its cache read.
        block = prefix_block(
            with_group_rules(build_extraction_prompt(_input(source_language="he")))
        )

        assert SOURCE_QUOTE_DIRECTIVE in block["text"]

    def test_should_build_the_run_prompt_with_the_quote_line_when_the_run_names_its_source(
        self,
    ) -> None:
        prompt = _build_prompt(
            TriageResult(content_tags=["learning"]),
            {"title": "T", "duration": 600},
            video_memory="",
            frame_context="",
            visual_annotations="",
            source_language="he",
        )

        assert SOURCE_QUOTE_DIRECTIVE in prompt.system

    def test_should_keep_the_cached_head_unchanged_when_the_source_is_not_english(self) -> None:
        hebrew = build_extraction_prompt(_input(source_language="he"))
        english = build_extraction_prompt(_input())

        assert (hebrew.head, hebrew.tail) == (english.head, english.tail)


# ─── The models ───


_QUOTED_MODELS: list[tuple[type, dict[str, Any]]] = [
    (LearningKeyIdea, {"claim": "c", "quote": _HEBREW}),
    (PodcastQA, {"question": "q", "answer": "a", "quote": _HEBREW}),
    (Claim, {"claim": "c", "quote": _HEBREW}),
    (LearningTimestamp, {"description": "d", "quote": _HEBREW}),
    (PodcastSegment, {"title": "t", "quote": _HEBREW}),
    (PodcastQuote, {"quote": _HEBREW}),
    (TravelSpot, {"name": "n", "quote": _HEBREW}),
    (ReviewVerdict, {"bottomLine": "b", "quote": _HEBREW}),
]
_IDS = [model.__name__ for model, _ in _QUOTED_MODELS]


class TestQuoteEnOnTheModels:
    @pytest.mark.parametrize(("model", "item"), _QUOTED_MODELS, ids=_IDS)
    def test_should_keep_quote_en_when_the_extraction_writes_it(
        self, model: type, item: dict[str, Any]
    ) -> None:
        dumped = model.model_validate({**item, "quoteEn": _ENGLISH}).model_dump(by_alias=True)

        assert (dumped["quote"], dumped["quoteEn"]) == (_HEBREW, _ENGLISH)

    @pytest.mark.parametrize(("model", "item"), _QUOTED_MODELS, ids=_IDS)
    def test_should_leave_quote_en_out_of_the_dump_when_the_run_wrote_none(
        self, model: type, item: dict[str, Any]
    ) -> None:
        assert "quoteEn" not in model.model_validate(item).model_dump(by_alias=True)

    @pytest.mark.parametrize(("model", "item"), _QUOTED_MODELS, ids=_IDS)
    def test_should_drop_a_blank_quote_en_when_validated(
        self, model: type, item: dict[str, Any]
    ) -> None:
        dumped = model.model_validate({**item, "quoteEn": "   "}).model_dump(by_alias=True)

        assert "quoteEn" not in dumped

    def test_should_keep_question_en_when_an_exchange_was_asked_in_hebrew(self) -> None:
        qa = PodcastQA.model_validate(
            {"question": "למה דווקא עכשיו?", "questionEn": "Why now?", "answer": "a"}
        )

        assert qa.model_dump(by_alias=True)["questionEn"] == "Why now?"

    def test_should_leave_question_en_out_of_the_dump_when_the_run_is_english(self) -> None:
        dumped = PodcastQA.model_validate({"question": "Why now?", "answer": "a"}).model_dump(
            by_alias=True
        )

        assert "questionEn" not in dumped

    def test_should_keep_quote_en_through_domain_validation_when_a_key_idea_has_one(
        self,
    ) -> None:
        idea = {"claim": "c", "why": "w", "quote": _HEBREW, "quoteEn": _ENGLISH}

        data = validate_domain_output(["learning"], [], {"learning": {"keyIdeas": [idea]}})

        assert data["learning"]["keyIdeas"][0]["quoteEn"] == _ENGLISH

    def test_should_keep_an_english_key_idea_dump_unchanged_when_validated(self) -> None:
        idea = {"claim": "c", "why": "w", "quote": "q", "timestamp": 5}

        data = validate_domain_output(["learning"], [], {"learning": {"keyIdeas": [idea]}})

        assert set(data["learning"]["keyIdeas"][0]) == {
            "claim",
            "why",
            "quote",
            "timestamp",
            "section",
            "kind",
            "speaker",
            "figure",
        }
