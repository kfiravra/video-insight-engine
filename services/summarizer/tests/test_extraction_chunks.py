"""Long videos on the split path (pipeline-1min 3.7): chunks × groups, per-chunk windows + warm-up.

The synthetic 90-minute video: 5-second segments (``[m:ss]`` markers every
20 s), a memory outline of six 15-minute sections → ``batch_chapters`` (40-min
cap) merges them into three 30-minute chunks. Its plan has three text groups
and one visual group, so a run is 3 chunks × 4 groups = 12 calls.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import settings
from src.models.memory_types import MemoryResult, OutlineSection
from src.models.pipeline_types import PlanResult
from src.services.pipeline import extraction_groups
from src.services.pipeline.context import PipelineContext
from src.services.pipeline.extraction_chunks import (
    ExtractionChunk,
    key_frames_by_chunk,
    plan_chunks,
)
from src.services.pipeline.extraction_group_prompt import build_group_messages
from src.services.pipeline.extraction_groups import GroupCall, GroupOutcome, SplitRun
from src.services.pipeline.phases import enrichment as enrichment_phase
from src.services.pipeline.phases.enrichment import cancel_quiz_task
from src.services.pipeline.phases.split_extraction import run_phase_split_extraction
from src.services.pipeline.pipeline_helpers import PipelineTimer, TranscriptData
from src.services.pipeline.triage import TriageResult
from src.services.transcript.render import marker_seconds, render_transcript
from src.services.transcription import transcript_chunker
from src.services.video.youtube import Chapter, VideoData

_SLOTS = 4
_TABS = [
    {
        "id": "concepts",
        "label": "Concepts",
        "component": "flash_deck",
        "dataSource": "learning.concepts",
    },
    {"id": "stack", "label": "Stack", "component": "info_grid", "dataSource": "tech.topics"},
    {
        "id": "takeaways",
        "label": "Takeaways",
        "component": "checklist",
        "dataSource": "learning.takeaways",
    },
    {"id": "code", "label": "Code", "component": "code_playground", "dataSource": "tech.snippets"},
]
_TAGS = ["learning", "tech"]
# One annotation + one key frame in each 30-minute chunk (two in the last).
_FRAME_SECONDS = (300, 2100, 3900, 5100)


# ─── The synthetic video ───


def _segments(minutes: int) -> list[dict[str, Any]]:
    return [
        {"text": f"we talk {start} seconds in.", "start": float(start), "duration": 5.0}
        for start in range(0, minutes * 60, 5)
    ]


def _clock(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def _annotations(frames: tuple[int, ...]) -> str:
    lines = [f"[{_clock(s)}] slide at {s} | CODE {s}" for s in frames]
    return "\n".join(["<visual_annotations>", *lines, "</visual_annotations>"])


def _gallery(frames: tuple[int, ...]) -> list[dict]:
    return [{"timestamp": s, "ocr_text": f"frame {s}"} for s in frames]


def _failing_chain() -> Any:
    return patch.object(
        transcript_chunker,
        "split_transcript_into_chapters",
        AsyncMock(side_effect=RuntimeError("chunker down")),
    )


def _outline(minutes: int) -> MemoryResult:
    sections = [
        OutlineSection(start=start, end=min(start + 900, minutes * 60), title=f"Part {n + 1}")
        for n, start in enumerate(range(0, minutes * 60, 900))
    ]
    return MemoryResult(outline=sections)


def _ctx(
    minutes: int = 90,
    *,
    outline: bool = True,
    frames: tuple[int, ...] = _FRAME_SECONDS,
    **video: Any,
) -> PipelineContext:
    ctx = PipelineContext("vsid", "yt", {}, MagicMock(), MagicMock(), PipelineTimer())
    segments = _segments(minutes)
    ctx.video_data = VideoData("yt", "A long talk", "chan", minutes * 60, None, "", **video)
    ctx.transcript_data = TranscriptData(segments, "", "auto", "api")
    ctx.prompt_segments = segments
    ctx.prompt_transcript = render_transcript(segments)
    ctx.memory = _outline(minutes) if outline else MemoryResult()
    ctx.plan_result = PlanResult.model_validate(
        {"contentTags": _TAGS, "primaryTag": _TAGS[0], "tabs": _TABS, "confidence": 0.9}
    )
    ctx.triage = TriageResult(content_tags=_TAGS, primary_tag=_TAGS[0], tabs=_TABS)
    ctx.triage_dict = ctx.plan_result.to_triage_dict(ctx.content_format)
    ctx.visual_annotations = _annotations(frames)
    ctx.scene_frames_gallery = _gallery(frames)
    return ctx


@pytest.fixture(autouse=True)
def _slots() -> Iterator[None]:
    with patch.object(settings, "EXTRACTION_PARALLEL_BATCHES", _SLOTS):
        yield


@pytest.fixture
def detect() -> Iterator[AsyncMock]:
    """chapter_detect, answering nothing (the chain then time-splits)."""
    mock = AsyncMock(return_value=None)
    with patch.object(transcript_chunker, "_detect_chapters_with_ai", mock):
        yield mock


# ─── Planning ───


class TestPlanChunks:
    async def test_should_make_one_chunk_when_the_video_is_under_40_min_and_50k_tokens(
        self,
    ) -> None:
        ctx = _ctx(30)

        chunks = await plan_chunks(ctx)

        assert chunks == [ExtractionChunk(index=0, transcript=ctx.prompt_transcript)]

    async def test_should_do_no_chapter_work_and_no_description_wait_for_one_chunk(self) -> None:
        ctx = _ctx(30)
        ctx.description_task = asyncio.create_task(asyncio.Event().wait())  # never lands

        try:
            await asyncio.wait_for(plan_chunks(ctx), timeout=1.0)
        finally:
            ctx.description_task.cancel()

        assert ctx.chapters is None

    async def test_should_cut_a_90_min_video_into_chunks_of_at_most_40_minutes(self) -> None:
        chunks = await plan_chunks(_ctx())

        spans = [
            max(marker_seconds(c.transcript)) - min(marker_seconds(c.transcript)) for c in chunks
        ]
        assert len(chunks) == 3 and max(spans) < settings.MAX_MINUTES_PER_BATCH * 60

    async def test_should_cut_chunks_when_the_tokens_exceed_one_batch(self) -> None:
        with patch.object(settings, "MAX_TOKENS_PER_BATCH", 2000):
            chunks = await plan_chunks(_ctx(30))

        assert len(chunks) == 2

    async def test_should_put_every_transcript_marker_in_exactly_one_chunk(self) -> None:
        ctx = _ctx()

        chunks = await plan_chunks(ctx)

        markers = [s for c in chunks for s in marker_seconds(c.transcript)]
        assert markers == marker_seconds(ctx.prompt_transcript)

    async def test_should_tell_each_chunk_which_part_of_the_video_it_holds(self) -> None:
        chunks = await plan_chunks(_ctx())

        assert [f"BATCH {c.index + 1} of 3" in c.batch_context for c in chunks] == [True] * 3

    async def test_should_open_the_windows_at_the_video_ends_and_chain_them(self) -> None:
        chunks = await plan_chunks(_ctx())

        windows = [(c.start_s, c.end_s) for c in chunks]
        assert windows == [(None, 1800.0), (1800.0, 3600.0), (3600.0, None)]

    async def test_should_cut_on_the_memory_outline_without_calling_chapter_detect(
        self, detect: AsyncMock
    ) -> None:
        await plan_chunks(_ctx(outline=True))

        detect.assert_not_awaited()

    async def test_should_call_chapter_detect_only_without_an_outline(
        self, detect: AsyncMock
    ) -> None:
        await plan_chunks(_ctx(outline=False))

        detect.assert_awaited_once()

    async def test_should_hand_the_chapter_chain_the_videos_description(self) -> None:
        ctx = _ctx(outline=False)
        assert ctx.video_data is not None
        ctx.video_data.description = "0:00 Intro\n30:00 Deep dive"
        split = AsyncMock(return_value=[])

        with patch.object(transcript_chunker, "split_transcript_into_chapters", split):
            await plan_chunks(ctx)

        video_data = split.await_args.kwargs["video_data"]
        assert video_data["description"] == "0:00 Intro\n30:00 Deep dive"

    async def test_should_cut_on_the_creators_chapters_ahead_of_the_outline(self) -> None:
        creator = [Chapter(s, s + 1800, f"Creator {s // 1800 + 1}") for s in range(0, 5400, 1800)]

        chunks = await plan_chunks(_ctx(chapters=creator))

        assert ["CHAPTER 1: Creator" in c.transcript for c in chunks].count(True) == 1

    async def test_should_force_split_a_long_video_when_the_chapter_chain_fails(self) -> None:
        with _failing_chain():
            chunks = await plan_chunks(_ctx())

        assert len(chunks) == 3  # 90 min over the 40-min cap

    async def test_should_keep_every_marker_once_when_force_splitting(self) -> None:
        ctx = _ctx()
        with _failing_chain():
            chunks = await plan_chunks(ctx)

        markers = [s for c in chunks for s in marker_seconds(c.transcript)]
        assert markers == marker_seconds(ctx.prompt_transcript)

    async def test_should_keep_one_chunk_when_the_force_split_gives_one_part(self) -> None:
        ctx = _ctx()
        ctx.prompt_transcript = ctx.prompt_transcript.replace(".", "")  # one sentence
        with _failing_chain():
            chunks = await plan_chunks(ctx)

        assert [c.transcript for c in chunks] == [ctx.prompt_transcript]


class TestKeyFramesByChunk:
    def test_should_leave_one_chunk_on_the_whole_videos_key_frames(self) -> None:
        chunks = [ExtractionChunk(0, "t")]

        assert key_frames_by_chunk(_gallery(_FRAME_SECONDS), [], chunks) == {}

    async def test_should_give_every_chunk_key_frames_when_the_video_has_more_than_the_cap(
        self,
    ) -> None:
        chunks = await plan_chunks(_ctx())
        gallery = _gallery(tuple(range(0, 5400, 120)))  # 45 captioned frames

        frames = key_frames_by_chunk(gallery, [], chunks)

        assert [bool(frames[c.index]) for c in chunks] == [True, True, True]

    async def test_should_format_each_chunk_from_the_frames_in_its_window(self) -> None:
        chunks = await plan_chunks(_ctx())

        frames = key_frames_by_chunk(_gallery(_FRAME_SECONDS), [], chunks)

        assert frames == {
            0: "5:00 — frame 300",
            1: "35:00 — frame 2100",
            2: ("1:05:00 — frame 3900\n1:25:00 — frame 5100"),
        }


# ─── The synthetic 90-min run ───


@dataclass
class _Groups:
    """``run_group_call`` stand-in: each call's request blocks, timing and slot use.

    Answers every field with one item tagged with the call's chunk; chunk
    ``slow_chunk`` lands last; chunk ``late_warm``'s text1 streams late.
    """

    slow_chunk: int | None = None
    late_warm: int | None = None
    blocks: dict[tuple[int, str], list[str]] = field(default_factory=dict)
    log: list[tuple[str, int, str, float]] = field(default_factory=list)
    in_flight: int = 0
    peak: int = 0

    def at(self, event: str, chunk: int, group_id: str) -> float:
        return next(t for e, c, g, t in self.log if (e, c, g) == (event, chunk, group_id))

    async def __call__(self, call: GroupCall, run: SplitRun, **kwargs: Any) -> GroupOutcome:
        request = build_group_messages(run.prompt, call.group, call.chunk, call.job)
        self.blocks[(call.chunk.index, call.group.group_id)] = [b["text"] for b in request]
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        self._note("start", call)
        try:
            await self._first_chunk(call, kwargs.get("on_first_chunk"))
            await asyncio.sleep(0.1 if call.chunk.index == self.slow_chunk else 0.02)
        finally:
            self.in_flight -= 1
        self._note("landed", call)
        return _answer(call)

    def _note(self, event: str, call: GroupCall) -> None:
        loop_time = asyncio.get_running_loop().time()
        self.log.append((event, call.chunk.index, call.group.group_id, loop_time))

    async def _first_chunk(self, call: GroupCall, on_first_chunk: Any) -> None:
        if on_first_chunk is None:
            return
        await asyncio.sleep(0.15 if call.chunk.index == self.late_warm else 0.01)
        self._note("first_chunk", call)
        on_first_chunk()


def _answer(call: GroupCall) -> GroupOutcome:
    data: dict[str, Any] = {}
    for request in call.group.fields:
        item = {"name": f"{request.field} c{call.chunk.index}", "definition": "d"}
        data.setdefault(request.domain, {})[request.field] = [item]
    report = {"dropped": {}, "defaulted": 0}
    return GroupOutcome(call.group, call.chunk.index, data, report, "ok", 1, 0)  # type: ignore[arg-type]


async def _run(
    groups: _Groups, frames: tuple[int, ...] = _FRAME_SECONDS
) -> tuple[PipelineContext, list[tuple[float, dict[str, Any]]]]:
    """The member over the synthetic 90-min video; every event with its arrival time."""
    ctx = _ctx(frames=frames)
    ctx.plan_done.set()
    ctx.frames_done.set()
    loop = asyncio.get_running_loop()
    events: list[tuple[float, dict[str, Any]]] = []
    with (
        patch.object(extraction_groups, "run_group_call", groups),
        patch.object(enrichment_phase, "enrich_quiz", AsyncMock(return_value=None)),
    ):
        async for chunk in run_phase_split_extraction(ctx):
            events.append((loop.time(), json.loads(chunk.removeprefix("data: "))))
        cancel_quiz_task(ctx)
    return ctx, events


def _complete(events: list[tuple[float, dict[str, Any]]]) -> dict[str, Any]:
    return next(e["data"] for _, e in events if e["event"] == "extraction_complete")


class TestChunksTimesGroups:
    async def test_should_fire_every_group_over_every_chunk_for_a_synthetic_90_min_transcript(
        self,
    ) -> None:
        groups = _Groups()

        await _run(groups)

        # The takeaways (~350 tokens) ride with the concepts since output-quality 1.7
        # (grouping.minGroupOutput): two text groups and the visual one per chunk.
        assert sorted(groups.blocks) == [
            (c, g) for c in range(3) for g in ("text1", "text2", "visual")
        ]

    async def test_should_send_every_call_of_every_chunk_the_same_rules_block(self) -> None:
        groups = _Groups()

        await _run(groups)

        assert len({blocks[0] for blocks in groups.blocks.values()}) == 1

    async def test_should_put_a_chunks_batch_context_in_the_job_of_each_of_its_calls(
        self,
    ) -> None:
        groups = _Groups()

        await _run(groups)

        jobs = {key: blocks[2] for key, blocks in groups.blocks.items()}
        assert all(f"BATCH {c + 1} of 3" in job for (c, _), job in jobs.items())

    async def test_should_never_exceed_extraction_parallel_batches_calls_in_flight(self) -> None:
        groups = _Groups()

        await _run(groups)

        assert groups.peak == _SLOTS

    async def test_should_merge_the_chunks_chunk_major_when_the_first_lands_last(self) -> None:
        groups = _Groups(slow_chunk=0)

        _, events = await _run(groups)

        names = [item["name"] for item in _complete(events)["learning"]["concepts"]]
        assert names == ["concepts c0", "concepts c1", "concepts c2"]

    async def test_should_preview_a_tab_only_once_every_chunks_call_for_it_landed(self) -> None:
        groups = _Groups(slow_chunk=2)

        _, events = await _run(groups)

        first_preview = next(
            t for t, e in events if e["event"] == "tab_ready" and e.get("id") == "concepts"
        )
        assert first_preview >= max(groups.at("landed", c, "text1") for c in range(3))


class TestVisualGroupPerChunk:
    @staticmethod
    def _seen(groups: _Groups, pattern: str) -> dict[int, list[int]]:
        """Per chunk: the frame seconds its visual call's job block names."""
        job = {c: groups.blocks[(c, "visual")][2] for c in range(3)}
        return {c: sorted({int(s) for s in re.findall(pattern, text)}) for c, text in job.items()}

    async def test_should_slice_annotations_per_chunk_for_the_visual_group(self) -> None:
        groups = _Groups()

        await _run(groups)

        assert self._seen(groups, r"slide at (\d+)") == {0: [300], 1: [2100], 2: [3900, 5100]}

    async def test_should_slice_key_frames_per_chunk_for_the_visual_group(self) -> None:
        groups = _Groups()

        await _run(groups)

        assert self._seen(groups, r"frame (\d+)") == {0: [300], 1: [2100], 2: [3900, 5100]}

    async def test_should_drop_key_frames_and_the_guide_when_a_chunks_window_has_no_frames(
        self,
    ) -> None:
        groups = _Groups()

        await _run(groups, frames=(300, 3900))  # nothing in chunk 1 (30:00–60:00)

        job = groups.blocks[(1, "visual")][2]
        assert ("<key_frames>" in job, "<visual_context_guide>" in job) == (False, False)


class TestWarmUpPerChunk:
    async def test_should_warm_each_chunks_prefix_with_its_own_text1(self) -> None:
        groups = _Groups(late_warm=1)

        await _run(groups)

        released = groups.at("start", 1, "text2")
        assert released >= groups.at("first_chunk", 1, "text1")

    async def test_should_not_hold_a_chunk_back_for_another_chunks_warm_up(self) -> None:
        groups = _Groups(late_warm=1)

        await _run(groups)

        assert groups.at("start", 0, "text2") < groups.at("first_chunk", 1, "text1")

    @staticmethod
    def _heads(groups: _Groups) -> dict[int, set[str]]:
        """Per chunk: the distinct head blocks (block 2) its group calls sent."""
        heads: dict[int, set[str]] = {}
        for (chunk, _group), blocks in groups.blocks.items():
            heads.setdefault(chunk, set()).add(blocks[1])
        return heads

    async def test_should_send_every_group_of_a_chunk_the_same_head_block(self) -> None:
        groups = _Groups()

        await _run(groups)

        assert [len(heads) for heads in self._heads(groups).values()] == [1, 1, 1]

    async def test_should_put_each_chunks_own_transcript_in_its_head_block(self) -> None:
        groups = _Groups()

        ctx, _ = await _run(groups)

        chunks = await plan_chunks(ctx)
        heads = self._heads(groups)
        assert [chunks[c].transcript in next(iter(heads[c])) for c in range(3)] == [True] * 3
