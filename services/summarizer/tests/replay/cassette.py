"""Cassette model for the replay harness.

A cassette is one recorded pipeline run, reduced to what a replay needs: the
LLM outputs + latencies (keyed by ``llm_feature_var`` + span + ordinal), the
transcript the pipeline consumed, the frame list, the media downloads, the
non-LLM step durations the fakes sleep, the settings that shaped the run, and
the phase walls the run actually took (the replay's fidelity target).
``build_cassette.py`` writes them from Langfuse + Mongo + S3 + ``llm_usage``
dumps; they are committed under ``cassettes/``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CASSETTE_DIR = Path(__file__).parent / "cassettes"
CASSETTE_SCHEMA = 2

# Non-LLM steps the fakes sleep, in seconds of recorded wall time. Per-call
# latencies: ``frameSeek`` (one ffmpeg -ss extraction), ``s3Op`` (one S3 JSON
# get/put or exists), ``sceneUpload`` (one scene-frame PUT; the batch is
# concurrent, so it is also the batch wall).
SLEEP_KEYS = (
    "metadata",
    "transcript",
    "sceneDetect",
    "scoreSelect",
    "frameSeek",
    "s3Op",
    "sceneUpload",
    "ocr",
    "qdrantStore",
)


@dataclass(frozen=True)
class LLMKey:
    """Deterministic identity of one LLM call inside a run.

    ``feature`` is the task-local ``llm_feature_var`` (one per phase, so
    parallel phases never collide); ``span`` separates calls that share a
    feature (synthesis-fed retry under ``summarize:extraction``,
    ``extraction_batchN`` siblings); ``ordinal`` separates repeats of the
    same span (retries, second passes) in call order.
    """

    feature: str
    span: str | None
    ordinal: int

    def label(self) -> str:
        return f"{self.feature}/{self.span}#{self.ordinal}"


@dataclass(frozen=True)
class LLMEntry:
    """One recorded completion."""

    key: LLMKey
    model: str
    latency_ms: int
    output: str
    finish_reason: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class FrameRecord:
    index: int
    timestamp: float
    total_score: float


@dataclass(frozen=True)
class FramesSpec:
    """What scene extraction produced: ``standard``/``high`` frames, or ``zero``.

    ``candidates`` is the HIGH-tier over-selection the reselect hook (vision)
    saw, ordered so that sorting by score reproduces the recorded
    ``frame_index`` order of the vision output.
    """

    mode: str
    selected: list[FrameRecord]
    gallery_indices: list[int]
    candidates: list[FrameRecord] = field(default_factory=list)
    detected_count: int = 0


@dataclass(frozen=True)
class DownloadSpec:
    """One yt-dlp download: wall seconds and file size (None = not recorded)."""

    seconds: float
    size_bytes: int | None


@dataclass(frozen=True)
class DownloadsSpec:
    """The low-res pass-1 download and the 720p downloads in call order.

    720p calls past the end of the list reuse its last entry, so a code change
    that adds a download still pays for it and one that removes a download
    simply leaves entries unused.
    """

    lowres: DownloadSpec
    hires: list[DownloadSpec]


@dataclass(frozen=True)
class VideoSpec:
    title: str
    channel: str
    duration: int
    thumbnail_url: str | None
    description: str
    chapters: list[dict[str, Any]]
    category: str
    youtube_category: str | None
    tags: list[str]
    language: str
    caption_track: str | None
    caption_lang: str | None


@dataclass(frozen=True)
class RecordedRun:
    """The original run's phase walls, milestones and stored tab ids (fidelity target)."""

    total_ms: int
    phases_ms: dict[str, int]
    milestones_ms: dict[str, int]
    estimated: bool
    tab_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Cassette:
    video_id: str
    provenance: dict[str, str]
    settings: dict[str, Any]
    tier: str
    video: VideoSpec
    transcript_source: str
    segments: list[tuple[int, int, str]]
    frames: FramesSpec
    downloads: DownloadsSpec
    sleeps: dict[str, float]
    llm: list[LLMEntry]
    recorded: RecordedRun


# ─── Parsing ───


def _parse_entry(raw: dict[str, Any]) -> LLMEntry:
    usage = raw.get("usage") or {}
    return LLMEntry(
        key=LLMKey(feature=raw["feature"], span=raw.get("span"), ordinal=int(raw["ordinal"])),
        model=raw["model"],
        latency_ms=int(raw["latencyMs"]),
        output=raw["output"],
        finish_reason=raw.get("finishReason") or "stop",
        input_tokens=int(usage.get("input", 0)),
        output_tokens=int(usage.get("output", 0)),
        cache_read_tokens=int(usage.get("cacheRead", 0)),
        cache_write_tokens=int(usage.get("cacheWrite", 0)),
    )


def _parse_frame_list(raw: list[dict[str, Any]]) -> list[FrameRecord]:
    return [
        FrameRecord(
            index=int(f["index"]),
            timestamp=float(f["timestamp"]),
            total_score=float(f["totalScore"]),
        )
        for f in raw
    ]


def _parse_frames(raw: dict[str, Any]) -> FramesSpec:
    return FramesSpec(
        mode=raw["mode"],
        selected=_parse_frame_list(raw.get("selected", [])),
        gallery_indices=[int(i) for i in raw.get("galleryIndices", [])],
        candidates=_parse_frame_list(raw.get("candidates", [])),
        detected_count=int(raw.get("detectedCount", 0)),
    )


def _parse_download(raw: dict[str, Any]) -> DownloadSpec:
    size = raw.get("bytes")
    return DownloadSpec(seconds=float(raw["seconds"]), size_bytes=int(size) if size else None)


def _parse_downloads(raw: dict[str, Any]) -> DownloadsSpec:
    return DownloadsSpec(
        lowres=_parse_download(raw["lowres"]),
        hires=[_parse_download(d) for d in raw.get("hires720p", [])],
    )


def _parse_video(raw: dict[str, Any]) -> VideoSpec:
    return VideoSpec(
        title=raw["title"],
        channel=raw["channel"],
        duration=int(raw["duration"]),
        thumbnail_url=raw.get("thumbnailUrl"),
        description=raw.get("description", ""),
        chapters=list(raw.get("chapters", [])),
        category=raw.get("category", "standard"),
        youtube_category=raw.get("youtubeCategory"),
        tags=list(raw.get("tags", [])),
        language=raw.get("language", "en"),
        caption_track=raw.get("captionTrack"),
        caption_lang=raw.get("captionLang"),
    )


def parse_cassette(raw: dict[str, Any]) -> Cassette:
    """Build a :class:`Cassette` from its JSON form; rejects unknown schemas."""
    if raw.get("schema") != CASSETTE_SCHEMA:
        raise ValueError(f"Unsupported cassette schema {raw.get('schema')!r}")
    recorded = raw["recorded"]
    sleeps = {key: float(raw["sleeps"].get(key, 0.0)) for key in SLEEP_KEYS}
    return Cassette(
        video_id=raw["videoId"],
        provenance=dict(raw.get("provenance", {})),
        settings=dict(raw.get("settings", {})),
        tier=raw["tier"],
        video=_parse_video(raw["video"]),
        transcript_source=raw["transcript"]["source"],
        segments=[(int(s[0]), int(s[1]), str(s[2])) for s in raw["transcript"]["segments"]],
        frames=_parse_frames(raw["frames"]),
        downloads=_parse_downloads(raw["downloads"]),
        sleeps=sleeps,
        llm=[_parse_entry(e) for e in raw["llm"]],
        recorded=RecordedRun(
            total_ms=int(recorded["totalMs"]),
            phases_ms={k: int(v) for k, v in recorded["phasesMs"].items()},
            milestones_ms={k: int(v) for k, v in recorded.get("milestonesMs", {}).items()},
            estimated=bool(recorded.get("estimated", False)),
            tab_ids=[str(t) for t in recorded.get("tabIds", [])],
            notes=list(recorded.get("notes", [])),
        ),
    )


def available_cassettes() -> list[str]:
    """Video ids with a committed cassette."""
    return sorted(p.stem for p in CASSETTE_DIR.glob("*.json"))


def load_cassette(video_id: str) -> Cassette:
    """Load ``cassettes/<video_id>.json``; the error lists what exists."""
    path = CASSETTE_DIR / f"{video_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No cassette for {video_id!r}; available: {', '.join(available_cassettes())}"
        )
    return parse_cassette(json.loads(path.read_text(encoding="utf-8")))
