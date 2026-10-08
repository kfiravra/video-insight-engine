"""Synthesis reads the run's ``<video_memory>`` block (pipeline-1min 1b.4).

The quiz call's copy of the block is covered in ``test_phase_enrichment.py``.

The block is rendered once per run (``phases/text.py``) and handed, byte for
byte, to every ``{video_context}`` slot; the old compact DNA is gone. The
LLM-facing stage functions are patched at the phase modules' seams.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.pipeline_types import SynthesisResult
from src.services.pipeline.phases import synthesis as synthesis_phase

_VIDEO_MEMORY = "<video_memory>\ndomains: food · goal: Cook lasagna\n</video_memory>"


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        video_summary_id="vsid",
        video_data=SimpleNamespace(title="Lasagna", channel="Chef", duration=600),
        triage=SimpleNamespace(
            primary_tag="food", content_tags=["food"], tabs=[{"id": "steps", "label": "Steps"}]
        ),
        extraction_data={"food": {"steps": [{"text": "boil the pasta"}]}},
        synthesis_dict={},
        enrichment_data=None,
        chapters=None,
        content_format="tutorial",
        llm_service=MagicMock(),
        video_memory=_VIDEO_MEMORY,
        memory=None,
        assembled_tabs=None,
        assembled_meta=None,
    )


async def _drain(phase: Any, ctx: SimpleNamespace) -> None:
    async for _ in phase(ctx):
        pass


async def test_synthesis_should_read_the_video_memory_block() -> None:
    synthesize = AsyncMock(
        return_value=SynthesisResult(
            tldr="t", key_takeaways=["k"], master_summary="m", seo_description="s"
        )
    )

    with patch.object(synthesis_phase, "synthesize", synthesize):
        await _drain(synthesis_phase.run_phase_synthesis, _ctx())

    assert synthesize.await_args.kwargs["video_context"] is _VIDEO_MEMORY
