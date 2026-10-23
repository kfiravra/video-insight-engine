"""Phase-C review fixes, lane FB (output-quality): one failed call costs only its own work.

- W1: a quiz v2 part call that raises (a provider 400 the retry wrapper re-raises on the
  primary model, a parse bug) loses only its part; the other parts' questions still
  ship, and no sibling call is left running unawaited.
- W2: the optional 4.6c pass (the English beneath verbatim lines) raising leaves the
  EN→source copy and the `completed` save in place; a raising copy is raised only once
  the English pass is done.

The LLM is faked at the retry wrapper (quiz); the translation phase's two passes at their
entry points, the English pass's calls at the flat translation call.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litellm.exceptions import BadRequestError

from src.services.pipeline.phases import translation as phase
from tests.test_p3_0_quiz_stretch_calls import _ANSWERS, _quiz, _stretch_in, _stretches
from tests.test_p4_6c_quote_english import _FLAT, _ctx, _fake_flat, _ideas, _run


def _bad_request() -> BadRequestError:
    return BadRequestError("400 bad request", model="m", llm_provider="anthropic")


# ─── W1: quiz v2 part calls ───


async def _middle_part_raises(_service: object, prompt: str, **_kwargs: Any) -> str:
    if _stretch_in(prompt) == 1:
        raise _bad_request()
    return _ANSWERS[_stretch_in(prompt)]


class TestQuizPartCallRaises:
    async def test_should_keep_the_other_parts_questions_when_one_part_call_raises(self) -> None:
        result = await _quiz(AsyncMock(side_effect=_middle_part_raises))

        assert _stretches(result) == {0, 2}

    async def test_should_finish_every_other_part_call_before_returning_when_one_raises(
        self,
    ) -> None:
        finished: list[int] = []

        async def answer(_service: object, prompt: str, **_kwargs: Any) -> str:
            stretch = _stretch_in(prompt)
            if stretch == 1:
                raise _bad_request()
            await asyncio.sleep(0.05)
            finished.append(stretch)
            return _ANSWERS[stretch]

        await _quiz(AsyncMock(side_effect=answer))

        assert sorted(finished) == [0, 2]

    async def test_should_log_the_failed_part_call_when_one_part_call_raises(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.ERROR, logger="src.services.pipeline.quiz_v2"):
            await _quiz(AsyncMock(side_effect=_middle_part_raises))

        assert any("call failed" in record.getMessage() for record in caplog.records)


# ─── W2: the English beneath verbatim lines ───
# The phase's two passes are faked at their own entry points (``phase.english_of_lines``,
# ``phase.translate_to_source``): the batch layer below them now keeps a raising batch as
# English (lane FA), so a raise reaching the phase is a bug outside the batches.


async def _saved_when_the_english_pass_raises() -> dict[str, Any]:
    repo = MagicMock()
    with (
        patch(_FLAT, side_effect=_fake_flat),
        patch.object(phase, "english_of_lines", AsyncMock(side_effect=_bad_request())),
    ):
        await _run(_ctx(_ideas()), repo)
    return repo.save_structured_result.call_args.args[1]


class TestEnglishPassRaises:
    async def test_should_still_attach_source_language_when_the_verbatim_line_pass_raises(
        self,
    ) -> None:
        saved = await _saved_when_the_english_pass_raises()

        assert saved["sourceLanguage"]["code"] == "he"

    async def test_should_save_the_run_completed_when_the_verbatim_line_pass_raises(
        self,
    ) -> None:
        saved = await _saved_when_the_english_pass_raises()

        assert saved["status"] == "completed"

    async def test_should_write_no_english_beneath_a_line_when_the_verbatim_line_pass_raises(
        self,
    ) -> None:
        saved = await _saved_when_the_english_pass_raises()

        assert "quoteEn" not in saved["tabs"][0]["props"]["spots"][0]

    async def test_should_finish_the_english_pass_before_raising_when_the_source_copy_raises(
        self,
    ) -> None:
        finished: list[str] = []

        async def english_pass(
            _llm: Any, strings: list[str], source: str, target: str, **_k: Any
        ) -> list[str]:
            await asyncio.sleep(0.05)
            finished.append(target)
            return [f"EN: {s}" for s in strings]

        with (
            patch(_FLAT, side_effect=english_pass),
            patch.object(phase, "translate_to_source", AsyncMock(side_effect=_bad_request())),
            pytest.raises(BadRequestError),
        ):
            await _run(_ctx(_ideas()), MagicMock())

        assert finished == ["en"]
