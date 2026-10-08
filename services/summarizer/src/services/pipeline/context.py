"""Pipeline context dataclass — shared mutable state across pipeline phases.

Each phase reads/writes fields on the context. Immutable inputs are set once
at construction; phase outputs are populated as the pipeline progresses.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.models.memory_types import MemoryResult
    from src.models.pipeline_types import PlanResult
    from src.models.probe_types import TierProbe
    from src.repositories.mongodb_repository import MongoDBVideoRepository
    from src.services.llm import LLMService
    from src.services.media.hires_prefetch import LocalHiresSource, LocalLowresSource
    from src.services.pipeline.pipeline_helpers import (
        PipelineTimer,
        TranscriptData,
        TranscriptTrail,
    )
    from src.services.pipeline.triage import TriageResult
    from src.services.video.description_analyzer import DescriptionAnalysis
    from src.services.video.youtube import VideoData


@dataclass
class PipelineContext:
    """Shared state for the summarization pipeline.

    Immutable inputs are set at construction. Phase outputs are populated
    by each phase function as the pipeline progresses.
    """

    # ── Immutable inputs (set once at construction) ──────────────────────
    video_summary_id: str
    youtube_id: str
    entry: dict[str, Any]
    repository: MongoDBVideoRepository
    llm_service: LLMService
    timer: PipelineTimer

    # ── Phase outputs (set by phase functions) ───────────────────────────
    video_data: VideoData | None = None
    transcript_data: TranscriptData | None = None
    # Provenance of the transcript fetch. The transcript phase sets it in a
    # ``finally`` so it exists for failed runs too; the runner persists it
    # as ``transcriptMeta``.
    transcript_trail: TranscriptTrail | None = None
    clean_text: str = ""
    # The segments every prompt render reads (``render_transcript``): the
    # SponsorBlock-filtered list when sponsor reads were cut (``clean_text``
    # drops the same reads), else the raw ``transcript_data.segments``.
    prompt_segments: list[dict[str, Any]] = field(default_factory=list)
    # ``prompt_segments`` rendered once with ``[m:ss]`` markers (``clean_text``
    # for a segment-less transcript) at probe-done: plan and memory read the
    # same string.
    prompt_transcript: str = ""

    # Tier probe (pipeline-1min 1b.1). The task starts with phase 2 and makes
    # its call once ``transcript_ready`` is set (clean_text final); the frames
    # branch waits for it at most 3 s before Step 6b, the plan reads it as
    # ``probe`` (None = no answer — every reader falls back to metadata).
    transcript_ready: asyncio.Event = field(default_factory=asyncio.Event)
    tier_probe_task: asyncio.Task[TierProbe | None] | None = None
    probe: TierProbe | None = None

    # t=0 background work, started by the metadata phase once the video is
    # accepted: the caption fetch (the transcript phase awaits it) and the
    # description analysis (readers await it via await_description_analysis).
    caption_task: asyncio.Task[None] | None = None
    description_task: asyncio.Task[None] | None = None

    # Scene extraction
    scene_task: asyncio.Task | None = None
    # The run's two video downloads, started by the metadata phase unless the
    # frames are cached. The runner creates both; scene extraction closes the
    # low-res file after detection, assembly closes the 720p file after moment
    # fill, and the runner closes both again when the run ends, so no exit
    # path leaks a download.
    lowres_video: LocalLowresSource | None = None
    hires_video: LocalHiresSource | None = None
    scene_frames_for_assembly: list[dict] = field(default_factory=list)
    scene_frames_all: list[dict] = field(
        default_factory=list
    )  # All frames (for thumbnail matching)
    scene_frames_gallery: list[dict] = field(default_factory=list)  # Gallery subset

    # Vision LLM frame descriptions (from frame_analyzer)
    frame_descriptions: list[dict] = field(default_factory=list)
    # Rendered <visual_annotations> block (descriptions + OCR), set at frames-done (1c.2).
    visual_annotations: str = ""

    # Memory stage (1b.3; None = failed or not run) and the <video_memory>
    # block rendered once from plan + memory (1b.4) for every writer's
    # ``{video_context}`` slot.
    memory: MemoryResult | None = None
    video_memory: str = ""

    # Plan inputs
    override: dict | None = None
    category_hint: str | None = None
    content_format: str | None = (
        None  # Presentation format from the tier probe (tutorial, commentary, etc.)
    )
    plan_result: PlanResult | None = None  # Merged plan result (replaces manifest + triage)
    description_analysis: DescriptionAnalysis | None = None

    # Triage outputs
    triage: TriageResult | None = None
    triage_dict: dict[str, Any] = field(default_factory=dict)

    # Chapter splitting (populated by extraction phase for long videos)
    chapters: list[Any] | None = (
        None  # list[ChapterChunk] — loose coupling to avoid circular imports
    )

    # Extraction / synthesis / enrichment outputs
    extraction_data: dict[str, Any] | None = None
    synthesis_dict: dict[str, Any] = field(default_factory=dict)
    enrichment_data: dict[str, Any] | None = None
    # Coverage metric: how far into the video the timestamped extraction
    # reaches vs. duration, plus dropped-batch counts. Surfaced into meta.
    extraction_coverage: dict[str, Any] | None = None

    # Assembly outputs (populated by assembly phase)
    assembled_tabs: list[dict] | None = None
    assembled_meta: dict[str, Any] | None = None

    # Language support. The pipeline is English-canonical: ALL generation runs
    # in English, so ``language``/``is_rtl`` always describe the English-primary
    # output. ``source_language_code`` is the detected original language (e.g.
    # "he") that drives the final English→source translation pass, RAG
    # transcript translation, and cache ownership. It is None for English-source
    # and sound-only music-override videos (no translation, no toggle).
    language: str = "en"  # ISO 639-1 code of the primary (English) output
    is_rtl: bool = False  # Whether the primary output is right-to-left (always False)
    source_language_code: str | None = None  # detected original language, or None
    audio_path: Path | None = None  # Cached audio file for reuse by translation

    # Source-language artifact for non-English videos. The top-level tabs/meta/
    # synthesis on this context are ALWAYS the English-primary payload after
    # the translation phase runs; this dict stashes the original-language
    # version so it can be persisted alongside as ``sourceLanguage``.
    # Shape: {"code", "name", "isRTL", "tabs", "meta", "synthesis"}.
    # None for English-source videos and for sound-only music-override videos.
    source_language: dict[str, Any] | None = None

    # Per-phase timing (phase_name → seconds)
    phase_times: dict[str, float] = field(default_factory=dict)
    # Set when a save finds no row: a global purge deleted the video mid-run.
    # Later phases and the runner must not re-create artifacts or emit `done`.
    row_deleted: bool = False
    # Eval-user run (D25): its result is a private eval version, so it never
    # reaches the shared Redis response cache or the Qdrant collection.
    eval_run: bool = False
    # Cold-media benchmark run (1a.7, the API's admin/eval-only `cold` flag):
    # the S3 transcript cache and the scene-frame manifest are not read, so
    # the run measures cold media; fresh results are still written to both.
    cold_media: bool = False
