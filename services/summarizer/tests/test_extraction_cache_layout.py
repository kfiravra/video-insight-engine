"""Extraction cache layout (pipeline-1min 1c.3, brief B.5 / A26).

System = rules only (identical for every video); user = ``[video + transcript +
video_memory]`` ending the one cache breakpoint, then the job. Per-call values
(transcript slice, batch context, annotation slice) are bound in one pass.
Phase 1 makes one call per batch and every batch's head differs, so cross-call
reads arrive with phase-3 groups — the simulated cache below proves the layout
reads on a second call over the same head (accepted alternative to the
"cache-read on batch 2 in replay" check).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.services import llm_provider as lp
from src.services.llm import LLMService
from src.services.llm_messages import TextBlock
from src.services.pipeline import pipeline_timing
from src.services.pipeline.extraction_prompt import (
    ExtractionPrompt,
    ExtractionPromptInput,
    build_extraction_prompt,
    slice_visual_annotations,
)
from src.services.pipeline.extractor import _chunked_extraction, extract
from src.services.pipeline.pipeline_timing import PipelineTimingRecorder, start_run_timing
from src.services.pipeline.triage import TriageResult
from src.services.transcription.transcript_chunker import ChapterChunk
from src.utils.llm_retry import call_llm_with_retry

_HAIKU = "anthropic/claude-haiku-4-5-20251001"
_ANNOTATIONS = (
    "<visual_annotations>\n"
    "[0:05] Title slide | Birria 101\n"
    "[5:10] Blender | 3 guajillo\n"
    "  2 ancho\n"
    "[9:00] Plated tacos\n"
    "</visual_annotations>"
)


def _video_a() -> ExtractionPromptInput:
    return ExtractionPromptInput(
        content_tags=["food"],
        modifiers=["narrative"],
        quality_rules="RULES",
        primary_tag="food",
        title="Birria Tacos at Home",
        duration_seconds=1205,
        tabs=[
            {"label": "🛒 Ingredients", "component": "checklist", "dataSource": "food.ingredients"}
        ],
        video_memory="<video_memory>\ndomains: food · goal: cook birria\n</video_memory>",
        frame_context="0:12 — chili paste [demo]",
        visual_annotations=_ANNOTATIONS,
    )


def _video_b() -> ExtractionPromptInput:
    return ExtractionPromptInput(
        content_tags=["tech", "learning"],
        modifiers=[],
        quality_rules="RULES",
        primary_tag="tech",
        title="Rust Ownership Explained",
        duration_seconds=3725,
        tabs=[{"label": "💻 Code", "component": "code_playground", "dataSource": "tech.snippets"}],
        video_memory="<video_memory>\ndomains: tech · goal: learn borrowing\n</video_memory>",
    )


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class TestStaticSystemBlock:
    def test_should_hash_identically_when_videos_differ(self):
        system_a = build_extraction_prompt(_video_a()).system
        system_b = build_extraction_prompt(_video_b()).system

        assert _sha(system_a) == _sha(system_b)

    @pytest.mark.parametrize(
        "per_video",
        [
            "Birria Tacos",
            "20:05",
            "cook birria",
            "🛒 Ingredients",
            "FOOD DOMAIN",
            "Blender",
            "{transcript}",
        ],
    )
    def test_should_carry_no_per_video_text(self, per_video):
        assert per_video not in build_extraction_prompt(_video_a()).system

    def test_should_hold_the_rules(self):
        system = build_extraction_prompt(_video_a()).system

        assert "<quality_rules>\nRULES\n</quality_rules>" in system
        assert system.rstrip().endswith("</output_rules>")


class TestUserBlocks:
    def test_should_end_the_cached_prefix_after_transcript_and_memory(self):
        head, tail = build_extraction_prompt(_video_a()).user_blocks("[0:00] hello")

        assert head.get("cache_control") == {"type": "ephemeral"}
        assert "[0:00] hello" in head["text"]
        assert head["text"].rstrip().endswith("</video_memory>")
        assert "cache_control" not in tail
        assert tail["text"].startswith("<your_job>")

    def test_should_keep_head_byte_identical_when_only_the_job_varies(self):
        prompt = build_extraction_prompt(_video_a())

        first = prompt.user_blocks("[0:00] hi", batch_context="BATCH 1\n")
        second = prompt.user_blocks("[0:00] hi", batch_context="BATCH 2\n", visual_annotations="")

        assert first[0] == second[0]
        assert first[1] != second[1]

    def test_should_bind_in_one_pass_when_onscreen_text_looks_like_placeholders(self):
        prompt = build_extraction_prompt(_video_a())
        tricky = (
            "<visual_annotations>\n[0:01] | {batch_context} {transcript}\n</visual_annotations>"
        )

        _, tail = prompt.user_blocks("T", batch_context="BC\n", visual_annotations=tricky)

        assert "[0:01] | {batch_context} {transcript}" in tail["text"]
        assert "<your_job>\nBC\nEmit:" in tail["text"]

    def test_should_send_one_uncached_block_when_template_lost_its_sections(self):
        prompt = ExtractionPrompt(system="", head="", tail="ALL {transcript} {batch_context}")

        blocks = prompt.user_blocks("T", batch_context="B")

        assert blocks == [{"type": "text", "text": "ALL T B"}]


class TestSliceVisualAnnotations:
    def test_should_keep_entries_inside_the_range_with_their_continuation_lines(self):
        sliced = slice_visual_annotations(_ANNOTATIONS, 300, 540)

        assert (
            sliced
            == "<visual_annotations>\n[5:10] Blender | 3 guajillo\n  2 ancho\n</visual_annotations>"
        )

    def test_should_keep_open_ends_when_bounds_are_none(self):
        assert slice_visual_annotations(_ANNOTATIONS, None, None).count("\n[") == 3

    def test_should_return_empty_when_no_entry_falls_inside(self):
        assert slice_visual_annotations(_ANNOTATIONS, 600, 700) == ""


# ─── Chunked batches ───


def _chapter(index: int) -> ChapterChunk:
    return ChapterChunk(
        index=index,
        title=f"Part {index + 1}",
        start_seconds=index * 300.0,
        end_seconds=(index + 1) * 300.0,
        text=f"[{index * 5}:00] words of part {index + 1}",
        source="youtube",
        token_estimate=60_000,  # one chapter per batch
    )


def _triage() -> TriageResult:
    return TriageResult(content_tags=["food"], primary_tag="food", tabs=[])


def _layout_prompt() -> ExtractionPrompt:
    return ExtractionPrompt(
        system="RULES",
        head="<transcript>\n{transcript}\n</transcript>",
        tail="<your_job>\n{batch_context}JOB\n</your_job>\n{visual_annotations}",
        visual_annotations=_ANNOTATIONS,
    )


async def _run_chunked(
    prompt: ExtractionPrompt, chapters: list[ChapterChunk]
) -> list[list[TextBlock]]:
    sent: list[list[TextBlock]] = []

    async def fake_call(_llm: Any, blocks: list[TextBlock], **_kwargs: Any) -> str:
        sent.append(blocks)
        return json.dumps({"food": {}})

    with (
        patch("src.services.pipeline.extractor.call_llm_with_retry", new=fake_call),
        patch("src.services.pipeline.extractor.validate_domain_output", return_value={"food": {}}),
    ):
        async for _ in _chunked_extraction(MagicMock(), _triage(), prompt, chapters):
            pass
    return sent


class TestChunkedBatches:
    async def test_should_cache_each_batch_own_transcript_slice(self):
        sent = await _run_chunked(_layout_prompt(), [_chapter(0), _chapter(1)])

        heads = sorted(blocks[0]["text"] for blocks in sent)
        assert "part 1" in heads[0] and "part 2" not in heads[0]
        assert "part 2" in heads[1] and "part 1" not in heads[1]

    async def test_should_give_each_batch_its_slice_of_annotations(self):
        sent = await _run_chunked(_layout_prompt(), [_chapter(0), _chapter(1)])

        tails = {("BATCH 1" in b[1]["text"]): b[1]["text"] for b in sent}
        assert "[0:05]" in tails[True] and "[5:10]" not in tails[True]
        assert (
            "[5:10]" in tails[False] and "[9:00]" in tails[False] and "[0:05]" not in tails[False]
        )

    @pytest.mark.parametrize(("parallel", "expected"), [(False, 1), (True, 3)])
    async def test_should_cap_concurrency_by_extraction_parallel(
        self, monkeypatch, parallel, expected
    ):
        from src.services.pipeline import extractor

        monkeypatch.setattr(extractor.settings, "EXTRACTION_PARALLEL", parallel)
        monkeypatch.setattr(extractor.settings, "EXTRACTION_PARALLEL_BATCHES", 6)
        state = {"now": 0, "peak": 0}

        async def fake_call(*_args: Any, **_kwargs: Any) -> str:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
            await asyncio.sleep(0.01)
            state["now"] -= 1
            return json.dumps({"food": {}})

        with (
            patch("src.services.pipeline.extractor.call_llm_with_retry", new=fake_call),
            patch("src.services.pipeline.extractor.validate_domain_output", return_value={}),
        ):
            async for _ in _chunked_extraction(
                MagicMock(), _triage(), _layout_prompt(), [_chapter(i) for i in range(3)]
            ):
                pass

        assert state["peak"] == expected


# ─── Simulated Anthropic prefix cache through the real provider ───


class _PrefixCache:
    """Fake ``acompletion``: caches the prefix up to the breakpoint, reads it on a repeat."""

    def __init__(self, content: str = "ok") -> None:
        self._seen: set[str] = set()
        self._content = content
        self.messages: list[list[dict[str, Any]]] = []

    @staticmethod
    def _prefix(messages: list[dict[str, Any]]) -> str | None:
        parts: list[str] = []
        for message in messages:
            content = message["content"]
            blocks = [{"text": content}] if isinstance(content, str) else content
            for block in blocks:
                parts.append(block["text"])
                if "cache_control" in block:
                    return "".join(parts)
        return None

    async def acompletion(self, **kwargs: Any) -> MagicMock:
        self.messages.append(kwargs["messages"])
        prefix = self._prefix(kwargs["messages"])
        tokens = len(prefix) // 4 if prefix else 0
        hit = prefix is not None and prefix in self._seen
        if prefix is not None:
            self._seen.add(prefix)
        response = MagicMock()
        response.choices = [
            MagicMock(finish_reason="stop", message=MagicMock(content=self._content))
        ]
        response.usage = SimpleNamespace(
            prompt_tokens=tokens + 100,
            completion_tokens=10,
            cache_read_input_tokens=tokens if hit else 0,
            cache_creation_input_tokens=0 if hit else tokens,
        )
        response.model = _HAIKU.split("/", 1)[1]
        return response


@pytest.fixture
def recorder() -> Iterator[PipelineTimingRecorder]:
    token = pipeline_timing._recorder_var.set(None)
    try:
        yield start_run_timing()
    finally:
        pipeline_timing._recorder_var.reset(token)


def _service() -> LLMService:
    return LLMService(lp.LLMProvider(model=_HAIKU, fast_model=_HAIKU, fallback_models=[]))


async def _send(
    service: LLMService, prompt: ExtractionPrompt, transcript: str, batch_context: str
) -> None:
    await call_llm_with_retry(
        service,
        prompt.user_blocks(transcript, batch_context),
        system_prompt=prompt.system,
        stage_name="extraction",
        max_retries=0,
    )


class TestSimulatedPrefixCache:
    async def test_should_read_cache_on_second_call_over_same_head(self, recorder):
        cache = _PrefixCache()
        prompt = build_extraction_prompt(_video_a())

        with patch("src.services.llm_provider.acompletion", cache.acompletion):
            await _send(_service(), prompt, "[0:00] same transcript", "")
            await _send(_service(), prompt, "[0:00] same transcript", "BATCH 2 of 2\n")

        first, second = recorder.llm_calls
        assert first["cacheWriteTokens"] > 0 and first["cacheReadTokens"] == 0
        assert second["cacheReadTokens"] > 0

    async def test_should_not_read_cache_across_videos(self, recorder):
        cache = _PrefixCache()

        with patch("src.services.llm_provider.acompletion", cache.acompletion):
            await _send(_service(), build_extraction_prompt(_video_a()), "[0:00] a", "")
            await _send(_service(), build_extraction_prompt(_video_b()), "[0:00] b", "")

        assert [call["cacheReadTokens"] for call in recorder.llm_calls] == [0, 0]

    async def test_should_send_rules_as_system_and_cache_transcript_block_from_extract(self):
        cache = _PrefixCache(content=json.dumps({"keyPoints": []}))
        triage = TriageResult(content_tags=["learning"], primary_tag="learning", tabs=[])

        with patch("src.services.llm_provider.acompletion", cache.acompletion):
            async for _ in extract(
                _service(), triage, "[0:00] short talk", {"title": "T", "duration": 60}
            ):
                pass

        system, user = cache.messages[0]
        assert system["role"] == "system" and isinstance(system["content"], str)
        assert "<quality_rules>" in system["content"]
        assert [("cache_control" in block) for block in user["content"]] == [True, False]
        assert "[0:00] short talk" in user["content"][0]["text"]
