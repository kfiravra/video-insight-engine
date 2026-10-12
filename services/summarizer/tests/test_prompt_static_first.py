"""Static-first prompt layout (pipeline-1min 2.0): synthesis, enrich_quiz, description_analysis.

OpenAI caches a request's prefix automatically once that prefix is identical
to an earlier request's and at least 1,024 tokens long. These prompts put
their instructions, schema and example first and the per-video content last,
so two different videos send the same bytes up to the first per-video value.
Before 2.0 the shared prefix ended at the first per-video block, ahead of
the instructions.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest

from src.services.pipeline import enrichment, synthesis
from src.services.pipeline.enrichment import build_quiz_prompt
from src.services.pipeline.synthesis import SynthesisInput, build_synthesis_prompt
from src.services.video import description_analyzer


def _shared_prefix(first: str, second: str) -> str:
    return os.path.commonprefix([first, second])


@pytest.fixture(autouse=True)
def disk_prompts() -> Iterator[None]:
    """Render from the files on disk, never a registry version."""
    with (
        patch.object(
            synthesis, "_load_synthesis_prompt", lambda: synthesis.PROMPT_PATH.read_text()
        ),
        patch.object(enrichment, "load_prompt_text", lambda path: path.read_text()),
        patch.object(
            description_analyzer,
            "load_prompt",
            lambda name: (description_analyzer.PROMPTS_DIR / f"{name}.txt").read_text(),
        ),
    ):
        yield


def _synthesis_prompt(tag: str, *, hero_fallback: bool = False) -> str:
    return build_synthesis_prompt(
        SynthesisInput(
            title=f"Title {tag}",
            channel=f"Channel {tag}",
            duration=600 if tag == "A" else 1800,
            output_type="food" if tag == "A" else "tech",
            extraction_summary=f'{{"items":["extracted {tag}"]}}',
            video_context=f"<video_memory>\nmemory {tag}\n</video_memory>",
            tab_labels=(f"Tab {tag}",),
            hero_fallback=hero_fallback,
        )
    )


class TestSynthesisStaticFirst:
    def test_should_share_every_static_block_when_videos_differ(self) -> None:
        shared = _shared_prefix(_synthesis_prompt("A"), _synthesis_prompt("B"))

        assert ("</instructions>" in shared, "</example>" in shared) == (True, True)

    def test_should_keep_video_content_after_the_shared_prefix(self) -> None:
        shared = _shared_prefix(_synthesis_prompt("A"), _synthesis_prompt("B"))

        assert [tag in shared for tag in ("Title A", "memory A", "extracted A")] == [False] * 3

    def test_should_share_the_hero_rules_when_both_runs_need_the_hero_fallback(self) -> None:
        shared = _shared_prefix(
            _synthesis_prompt("A", hero_fallback=True), _synthesis_prompt("B", hero_fallback=True)
        )

        assert "keyTakeaways: 3-5 items" in shared


def _quiz_prompt(tag: str, domain: str = "tech") -> str:
    prompt = build_quiz_prompt(
        domain,
        {"key_points": [{"text": f"extracted point {tag}"}]},
        f"<video_memory>\nmemory {tag}\n</video_memory>",
        [{"id": f"tab_{tag}", "label": f"Tab {tag}", "component": "quiz_arena", "goal": "Check"}],
    )
    assert prompt is not None
    return prompt


class TestEnrichQuizStaticFirst:
    def test_should_share_the_rules_and_output_format_when_videos_differ(self) -> None:
        shared = _shared_prefix(_quiz_prompt("A"), _quiz_prompt("B"))

        assert ("</quiz_rules>" in shared, "</output_format>" in shared) == (True, True)

    def test_should_keep_video_content_after_the_shared_prefix(self) -> None:
        shared = _shared_prefix(_quiz_prompt("A"), _quiz_prompt("B"))

        assert [tag in shared for tag in ("memory A", "extracted point A", "Tab A")] == [False] * 3


async def _description_prompt(description: str) -> str:
    send = AsyncMock(return_value="{}")
    with patch.object(description_analyzer, "call_llm_with_retry", send):
        await description_analyzer.analyze_description(description, fast_model="openai/gpt-4o-mini")
    return send.call_args.args[1]


class TestDescriptionAnalysisStaticFirst:
    async def test_should_share_the_schema_and_rules_when_descriptions_differ(self) -> None:
        first = await _description_prompt("Links: https://example.com/a — gear list " * 3)
        second = await _description_prompt("Chapters: 0:00 intro, 2:30 setup, 5:00 tips " * 3)

        assert "Keep labels concise (2-5 words max)" in _shared_prefix(first, second)

    async def test_should_put_the_description_last(self) -> None:
        prompt = await _description_prompt("Chapters: 0:00 intro, 2:30 setup " * 3)

        assert prompt.rstrip().endswith("2:30 setup")
