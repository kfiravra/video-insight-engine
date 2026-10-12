"""What the readers get around the 2.5 fallback: frames, and the order against translation.

The probe must read the native transcript before anything translates it, and
after a fallback the frames branch must read the second probe task. Shares the
text-branch harness of ``test_transcript_fallback.py`` (boundaries faked, the
text branch, transcript phase and probe as production code).
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from src.services.pipeline.phases import frames as frames_phase
from tests.test_transcript_fallback import (
    GARBAGE_PL_ASR,
    HEBREW_CAPTIONS,
    Fakes,
    _ctx,
    _ProbeLLM,
    _transcript,
    run_text_branch,
)

# Every binding the text pipeline could reach the three translation entry
# points through (defining module + each importer).
_TRANSLATION_BINDINGS = (
    ("src.services.pipeline.translation", "translate_to_source"),
    ("src.services.pipeline.phases.translation", "translate_to_source"),
    ("src.services.transcript.english", "english_blocks"),
    ("src.services.pipeline.phases.qdrant_index", "english_blocks"),
    ("src.services.pipeline.translation_flat", "translate_flat_list"),
    ("src.services.pipeline.translation", "translate_flat_list"),
    ("src.services.transcript.english", "translate_flat_list"),
)


def _spy(name: str, order: list[str]) -> AsyncMock:
    return AsyncMock(side_effect=lambda *_args, **_kwargs: order.append(name))


class TestProbeInput:
    async def test_should_give_the_probe_the_untranslated_native_transcript(self) -> None:
        fakes = Fakes()

        await run_text_branch(_ctx(), _transcript(HEBREW_CAPTIONS, language="he"), fakes)

        windows = fakes.probe_llm.prompts[0].split("<transcript_windows>")[1]
        assert "כוח הבחירה החופשית" in windows

    @pytest.mark.parametrize(
        ("text", "language"),
        [(HEBREW_CAPTIONS, "he"), (GARBAGE_PL_ASR, "pl")],
        ids=["hebrew-captions", "garbage-captions"],
    )
    async def test_should_run_no_translation_entry_point_before_the_probe(
        self, text: str, language: str
    ) -> None:
        order: list[str] = []
        fakes = Fakes(probe_llm=_ProbeLLM(order=order))

        with ExitStack() as stack:
            for module, name in _TRANSLATION_BINDINGS:
                stack.enter_context(patch(f"{module}.{name}", _spy(name, order)))
            await run_text_branch(_ctx(), _transcript(text, language=language), fakes)

        assert order.index("probe") == 0


class TestFramesAfterTheFallback:
    async def test_should_take_the_tier_from_the_second_probe_task(self) -> None:
        # Metadata ("coding") says standard; only the second answer (food + demo,
        # read from the Whisper text) says high — the first one read garbage.
        ctx = _ctx()
        fakes = Fakes(probe_llm=_ProbeLLM(domain="food"))
        await run_text_branch(ctx, _transcript(GARBAGE_PL_ASR), fakes)
        gate = frames_phase._TierGate(ctx)

        await gate.reselect()

        assert gate.tier == "high"

    async def test_should_take_the_metadata_tier_while_only_the_garbage_probe_exists(
        self,
    ) -> None:
        ctx = _ctx()
        fakes = Fakes(probe_llm=_ProbeLLM(domain="food"))
        await run_text_branch(ctx, _transcript(GARBAGE_PL_ASR, "whisper"), fakes)
        gate = frames_phase._TierGate(ctx)

        await gate.reselect()

        assert gate.tier == "standard"
