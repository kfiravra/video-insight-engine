"""Tests for ``translate_to_source`` — the English-canonical translation engine.

Generation is English; this engine translates the English output INTO the
source language and nests it under ``sourceLanguage``. Covers:
  - successful translation: top level stays English, the translated artifact
    nests under ``sourceLanguage`` with code/native name/isRTL/payloads.
  - LLM failure → returns input unchanged (no ``sourceLanguage`` key).
  - mirror detection (LLM echoed English back) → returns input unchanged.
  - leaf-only deny-list: string values under denied keys are NOT collected,
    but containers under the same key names ARE recursed into their contents.
  - short strings (<= 3 chars after strip) not collected.
  - large string sets are batched (no fail-closed cap), nothing dropped.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.services.pipeline import translation_flat
from src.services.pipeline.translation import (
    _SKIP_KEYS,
    _collect_strings,
    translate_to_source,
)
from src.services.pipeline.translation_flat import is_mirror, translate_flat_list

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_llm():
    return AsyncMock()


@pytest.fixture
def english_output() -> dict[str, Any]:
    """An assembled English output dict, mirroring real pipeline shape."""
    return {
        "tabs": [
            {
                "id": "exercises",
                "label": "Exercises",
                "emoji": "💪",
                "component": "exercise_tracker",
                "props": {
                    "exercises": [
                        {
                            "name": "Crunches",
                            "description": "Basic ab exercise",
                            "formCues": ["Keep legs straight"],
                            "sets": 3,
                            "reps": "30 seconds",
                            "emoji": "🔄",
                        },
                    ],
                    "warmup": [{"name": "Light stretches"}],
                },
            },
        ],
        "meta": {
            "tldr": "Ab workout",
            "masterSummary": "Short home workout",
            "userGoal": "Tone your abs",
            "language": "en",
            "isRTL": False,
        },
    }


# ─── Walker / deny-list semantics ────────────────────────────────────────────


class TestCollectStrings:
    def test_collects_translatable_prose(self):
        pairs: list = []
        _collect_strings({"label": "Hello world"}, (), pairs)
        assert pairs == [(("label",), "Hello world")]

    def test_skips_short_strings(self):
        pairs: list = []
        _collect_strings({"label": "Hi", "id": "abc"}, (), pairs)
        # "Hi" is 2 chars, "abc" is in deny-list anyway. Both skipped.
        assert pairs == []

    def test_skip_keys_blocks_primitive_strings(self):
        pairs: list = []
        _collect_strings(
            {"url": "https://example.com/foo", "label": "Real text"},
            (),
            pairs,
        )
        # url is in deny-list (primitive string → skipped); label translates.
        paths = [p for p, _ in pairs]
        assert ("url",) not in paths
        assert ("label",) in paths

    def test_leaf_only_descends_into_containers_named_in_deny_list(self):
        """Critical rule: deny applies only when value is a primitive string.

        If a container has a deny-listed name, the walker MUST still recurse.
        This protects against accidentally listing list/dict keys.
        """
        pairs: list = []
        # ``code`` is in deny-list. As a primitive string → skipped.
        # As a dict containing prose → recursed and prose collected.
        _collect_strings(
            {"code": {"explanation": "This is an explanation"}},
            (),
            pairs,
        )
        assert (("code", "explanation"), "This is an explanation") in pairs

    def test_walker_recurses_lists(self):
        pairs: list = []
        _collect_strings(
            {"items": [{"label": "First item"}, {"label": "Second item"}]},
            (),
            pairs,
        )
        paths = [p for p, _ in pairs]
        assert ("items", 0, "label") in paths
        assert ("items", 1, "label") in paths

    def test_deny_list_includes_expected_keys(self):
        """Sanity-check the deny-list shape — guards against accidental removal."""
        for key in (
            "id",
            "component",
            "url",
            "thumbnailUrl",
            "s3Key",
            "emoji",
            "timestamp",
            "seconds",
            "language",
        ):
            assert key in _SKIP_KEYS

    def test_fitness_prose_fields_are_translated(self):
        """Regression: ``reps``/``rest``/``duration`` carry prose in fitness videos.

        Schema (services/summarizer/src/prompts/schemas/fitness.txt) produces
        values like ``"30 שניות"``, ``"AMRAP"``, ``"until failure"`` — these
        must flow through translation, not be deny-listed as numeric-shape.
        """
        for key in ("reps", "rest", "duration"):
            assert key not in _SKIP_KEYS, (
                f"{key} must be translatable — fitness schema emits prose at this key"
            )
        pairs: list = []
        _collect_strings(
            {"exercises": [{"reps": "30 שניות", "rest": "60 שניות", "duration": "AMRAP"}]},
            (),
            pairs,
        )
        collected = {p[-1]: v for p, v in pairs}
        assert collected.get("reps") == "30 שניות"
        assert collected.get("rest") == "60 שניות"
        assert collected.get("duration") == "AMRAP"


# ─── Mirror detection ────────────────────────────────────────────────────────


class TestIsMirror:
    def test_identical_strings_detected_as_mirror(self):
        originals = [
            "This is a long string A",
            "This is a long string B",
            "Short",
            "Medium length text",
        ]
        translations = list(originals)  # identical
        assert is_mirror(originals, translations) is True

    def test_different_translations_not_a_mirror(self):
        originals = ["This is a long string A", "This is a long string B", "Medium length"]
        translations = ["A", "B", "C"]  # different content (length 1)
        assert is_mirror(originals, translations) is False

    def test_empty_input_not_mirror(self):
        assert is_mirror([], []) is False


# ─── translate_flat_list (the one LLM call) ─────────────────────────────────


class TestTranslateFlatList:
    @staticmethod
    async def _answer(raw: str, strings: list[str]) -> list[str] | None:
        """``translate_flat_list`` when the model answers ``raw``."""
        with patch.object(translation_flat, "call_llm_with_retry", AsyncMock(return_value=raw)):
            return await translate_flat_list(AsyncMock(), strings, "en", "he")

    async def test_should_return_the_translated_strings(self):
        assert await self._answer('{"translations": ["שלום", "עולם"]}', ["hello", "world"]) == [
            "שלום",
            "עולם",
        ]

    async def test_should_unwrap_an_array_under_another_known_key(self):
        assert await self._answer('{"items": ["שלום"]}', ["hello"]) == ["שלום"]

    async def test_should_unwrap_the_only_list_of_an_unknown_wrapper(self):
        assert await self._answer('{"he": ["שלום"]}', ["hello"]) == ["שלום"]

    async def test_should_return_none_on_a_count_mismatch(self):
        assert await self._answer('{"translations": ["שלום"]}', ["hello", "world"]) is None

    async def test_should_return_none_on_non_string_items(self):
        assert await self._answer('{"translations": [1, 2]}', ["hello", "world"]) is None

    async def test_should_return_none_when_the_call_times_out(self):
        timeout = AsyncMock(side_effect=asyncio.TimeoutError)

        with patch.object(translation_flat, "call_llm_with_retry", timeout):
            assert await translate_flat_list(AsyncMock(), ["hello"], "en", "he") is None


# ─── translate_to_source orchestrator ────────────────────────────────────────


class TestTranslateToSource:
    @patch("src.services.pipeline.translation.translate_flat_list")
    async def test_keeps_english_primary_and_nests_translation_on_success(
        self,
        mock_flat,
        mock_llm,
        english_output,
    ):
        """Happy path: top level stays English, translation nests under sourceLanguage."""

        # Fake translation: prefix each English string with "HE:" so the result
        # is unambiguously different and passes mirror detection.
        async def fake(_llm, strings, *_a, **_k):
            return [f"HE: {s}" for s in strings]

        mock_flat.side_effect = fake

        result = await translate_to_source(mock_llm, english_output, "he")

        # Top level stays English (unchanged).
        assert result["tabs"][0]["label"] == "Exercises"
        assert result["meta"]["language"] == "en"
        assert result["meta"]["isRTL"] is False
        # sourceLanguage carries the translated copy.
        assert result["sourceLanguage"]["code"] == "he"
        assert result["sourceLanguage"]["name"] == "עברית"
        assert result["sourceLanguage"]["isRTL"] is True
        assert result["sourceLanguage"]["tabs"][0]["label"] == "HE: Exercises"
        assert result["sourceLanguage"]["meta"]["language"] == "he"
        assert result["sourceLanguage"]["meta"]["isRTL"] is True

    @patch("src.services.pipeline.translation.translate_flat_list")
    async def test_returns_input_unchanged_when_llm_fails(
        self,
        mock_flat,
        mock_llm,
        english_output,
    ):
        """LLM None → no sourceLanguage key → FE renders no toggle."""
        mock_flat.return_value = None
        result = await translate_to_source(mock_llm, english_output, "he")
        assert "sourceLanguage" not in result
        assert result is english_output  # exact same object returned

    @patch("src.services.pipeline.translation.translate_flat_list")
    async def test_returns_input_unchanged_when_llm_mirrors_english(
        self,
        mock_flat,
        mock_llm,
        english_output,
    ):
        """LLM echoed English back → mirror detection trips → no sourceLanguage."""

        async def echo(_llm, strings, *_a, **_k):
            return list(strings)  # byte-identical mirror

        mock_flat.side_effect = echo

        result = await translate_to_source(mock_llm, english_output, "he")
        assert "sourceLanguage" not in result

    @patch("src.services.pipeline.translation.translate_flat_list")
    async def test_skips_llm_call_when_no_translatable_strings(
        self,
        mock_flat,
        mock_llm,
    ):
        """Empty / structural-only inputs short-circuit before the LLM call."""
        result = await translate_to_source(
            mock_llm,
            {"tabs": [], "meta": {"language": "en", "isRTL": False}},
            "he",
        )
        mock_flat.assert_not_called()
        assert "sourceLanguage" not in result

    @patch("src.services.pipeline.translation.translate_flat_list")
    async def test_deny_listed_url_string_never_reaches_llm(
        self,
        mock_flat,
        mock_llm,
    ):
        """Sanity: a deny-listed primitive string is NOT in the strings list."""

        async def fake(_llm, strings, *_a, **_k):
            return [f"HE: {s}" for s in strings]

        mock_flat.side_effect = fake

        await translate_to_source(
            mock_llm,
            {
                "tabs": [{"label": "Exercises", "props": {"url": "https://example.com/asset.mp4"}}],
                "meta": {},
            },
            "he",
        )
        call_strings = mock_flat.call_args.args[1]
        assert "https://example.com/asset.mp4" not in call_strings
        assert "Exercises" in call_strings

    @patch("src.services.pipeline.translation._MAX_TRANSLATION_BATCH", 2)
    @patch("src.services.pipeline.translation.translate_flat_list")
    async def test_batches_large_string_set_without_dropping(
        self,
        mock_flat,
        mock_llm,
    ):
        """A large string set is split into batches and FULLY translated — the
        old fail-closed cap is gone, nothing is silently dropped.
        """

        async def fake(_llm, strings, *_a, **_k):
            return [f"HE: {s}" for s in strings]

        mock_flat.side_effect = fake

        output = {
            "tabs": [
                {
                    "id": "spots",
                    "label": "Recommended spots",
                    "component": "spot_explorer",
                    "props": {
                        "spots": [
                            {
                                "name": f"Place number {i}",
                                "description": f"A long description of place number {i}",
                            }
                            for i in range(5)
                        ],
                    },
                },
            ],
            "meta": {"masterSummary": "Overall summary of the video"},
        }

        result = await translate_to_source(mock_llm, output, "he")

        # sourceLanguage built, every collected string translated (none dropped).
        sl = result["sourceLanguage"]
        spots = sl["tabs"][0]["props"]["spots"]
        assert spots[0]["name"].startswith("HE: ")
        assert spots[4]["description"].startswith("HE: ")
        assert sl["meta"]["masterSummary"].startswith("HE: ")
        # The low batch size forced more than one LLM call.
        assert mock_flat.call_count > 1

    @patch("src.services.pipeline.translation._MAX_TRANSLATION_BATCH", 2)
    @patch("src.services.pipeline.translation.translate_flat_list")
    async def test_partial_batch_failure_still_yields_source_language(
        self,
        mock_flat,
        mock_llm,
    ):
        """One failing batch is salvaged as English; the rest still translate.

        Regression for the Hebrew podcast whose entire ``sourceLanguage`` block
        was discarded because a single batch hit a count mismatch.
        """

        async def fake(_llm, strings, *_a, **_k):
            # Any batch containing the poison string can't be translated; the
            # salvage path isolates it (size-1 retry) and keeps it English.
            if any("POISON" in s for s in strings):
                return None
            return [f"HE: {s}" for s in strings]

        mock_flat.side_effect = fake

        output = {
            "tabs": [
                {
                    "id": "t",
                    "label": "Recommended spots",
                    "component": "spot_explorer",
                    "props": {
                        "spots": [
                            {"name": "First place", "description": "A POISON description here"},
                            {"name": "Second place", "description": "A normal description here"},
                        ]
                    },
                },
            ],
            "meta": {"masterSummary": "Overall summary of the video"},
        }

        result = await translate_to_source(mock_llm, output, "he")

        assert "sourceLanguage" in result
        spots = result["sourceLanguage"]["tabs"][0]["props"]["spots"]
        # The poison string is salvaged verbatim (English); everything else is
        # translated — a mostly-translated toggle beats no toggle.
        assert spots[0]["description"] == "A POISON description here"
        assert spots[0]["name"] == "HE: First place"
        assert spots[1]["name"] == "HE: Second place"
        assert result["sourceLanguage"]["meta"]["masterSummary"].startswith("HE: ")

    @patch("src.services.pipeline.translation._MAX_TRANSLATION_BATCH", 2)
    @patch("src.services.pipeline.translation.translate_flat_list")
    async def test_full_batch_mismatch_recovers_via_half_size_retry(
        self,
        mock_flat,
        mock_llm,
    ):
        """A batch that fails at full size succeeds when retried split in half."""

        async def fake(_llm, strings, *_a, **_k):
            if len(strings) > 1:
                return None  # full 2-string batch "truncates" → count mismatch
            return [f"HE: {s}" for s in strings]

        mock_flat.side_effect = fake

        output = {
            "tabs": [],
            "meta": {"tldr": "First summary line", "masterSummary": "Second summary line"},
        }

        result = await translate_to_source(mock_llm, output, "he")

        sl = result["sourceLanguage"]
        assert sl["meta"]["tldr"].startswith("HE: ")
        assert sl["meta"]["masterSummary"].startswith("HE: ")
        # 1 full-batch attempt + 2 half-size retries for the single batch.
        assert mock_flat.call_count == 3

    @patch("src.services.pipeline.translation._MAX_TRANSLATION_BATCH", 2)
    @patch("src.services.pipeline.translation.translate_flat_list")
    async def test_all_batches_failing_returns_input_unchanged(
        self,
        mock_flat,
        mock_llm,
        english_output,
    ):
        """When nothing translates, fall back to no ``sourceLanguage`` (no toggle)."""
        mock_flat.return_value = None
        result = await translate_to_source(mock_llm, english_output, "he")
        assert "sourceLanguage" not in result

    @patch("src.services.pipeline.translation._MAX_TRANSLATION_BATCH", 1)
    @patch("src.services.pipeline.translation.translate_flat_list")
    async def test_salvaged_strings_excluded_from_mirror_gate(
        self,
        mock_flat,
        mock_llm,
    ):
        """Salvaged English strings must NOT trip the echo (mirror) gate.

        Regression: salvaged strings equal their English originals by
        construction. Counting them in the mirror ratio could discard a
        genuinely-translated surface — here half the strings are salvaged
        (over the small-payload mirror threshold), yet the toggle must survive
        because the other half is really translated.
        """

        async def fake(_llm, strings, *_a, **_k):
            if any("POISON" in s for s in strings):
                return None  # this string can never be translated -> salvaged
            return [f"HE: {s}" for s in strings]

        mock_flat.side_effect = fake

        output = {
            "tabs": [
                {
                    "id": "t",
                    "label": "Spots",
                    "component": "spot_explorer",
                    "props": {
                        "spots": [
                            {"name": "POISON one", "description": "POISON two"},
                            {"name": "Genuine name", "description": "Genuine description"},
                        ]
                    },
                },
            ],
            "meta": {},
        }

        result = await translate_to_source(mock_llm, output, "he")

        assert "sourceLanguage" in result  # would be discarded by the old gate
        spots = result["sourceLanguage"]["tabs"][0]["props"]["spots"]
        assert spots[0]["name"] == "POISON one"  # salvaged English
        assert spots[0]["description"] == "POISON two"  # salvaged English
        assert spots[1]["name"] == "HE: Genuine name"
        assert spots[1]["description"] == "HE: Genuine description"
