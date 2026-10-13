"""Model A/B inputs beyond the cassettes (pipeline-1min 4.0d), all read-only, cached.

* **Golden tier probe** (Source 2): the 18 live golden videos with 0.8's
  labels. Title/channel/duration from dev Mongo, the transcript from the S3
  transcript store (the pipeline's reader, cleaned by its cleaner); YouTube
  description/tags/category only with ``fetch_metadata`` — 0.8's fetch inside
  ``vie-summarizer`` (yt-dlp through the dev proxy). Without it the probe sees
  "none" for those three fields, on every arm alike.
* **Hebrew translation** (Source 3): EqaMrzd9nZU's English output (dev Mongo,
  latest completed version) and its transcript segments (S3).

Requests are emitted by the pipeline's own builders (``run_tier_probe``,
``translate_flat_list``) into ``recording_provider``, the stage's model pinned
to arm A's (today's: config defaults move with 4.0d's picks); batching is the
callers' (``char_batches`` with the translation phase's and the RAG text's limits).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

from _model_ab_capture import recording_provider
from _model_ab_types import GPT_4O_MINI, HAIKU_45, AbRequest
from _tier_probe_inputs import (
    GoldenVideo,
    ProbeInput,
    fetch_mongo_rows,
    fetch_via_summarizer,
    load_live_golden,
    to_probe_input,
)
from tier_probe_ab import VISUAL_DEMO_LABELS

from src.config import settings
from src.services.llm import LLMService
from src.services.llm_provider import LLMProvider
from src.services.pipeline import translation
from src.services.pipeline.tier_probe import run_tier_probe
from src.services.pipeline.translation_flat import (
    TRANSLATION_BATCH_CHARS,
    answer_size,
    char_batches,
    translate_flat_list,
)
from src.services.transcript import english
from src.services.transcription.transcript import clean_transcript
from src.services.transcription.transcript_store import transcript_store

HEBREW_VIDEO = "EqaMrzd9nZU"
_SOURCE_LANGUAGE = "he"
# Output tokens per answer character: gpt-4o-mini answers ≈ 0.32 tokens per
# source character either way (translation_flat, EqaMrzd9nZU 2026-10-12).
_TRANSLATION_TOKENS_PER_CHAR = 0.33
_PROBE_ANSWER = (
    '{"domain": "tech", "format": "tutorial", "has_visual_demo": true, "confidence": 0.9}'
)
_PROBE_OUTPUT_TOKENS = 60  # 0.8: 47–49 for 4 keys + ≈ 10 for transcript_usable
_MARKER = "@@AB@@"


# ─── Dev Mongo (mongosh in the container; port 27017 refuses the host) ───────


def _mongosh(script: str) -> list[dict[str, Any]]:
    cmd = [
        "docker",
        "exec",
        "vie-mongodb",
        "sh",
        "-c",
        'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" '
        '--authenticationDatabase admin video-insight-engine --eval "$0"',
        script,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)
    lines = [line for line in proc.stdout.splitlines() if line.startswith(_MARKER)]
    return [json.loads(line[len(_MARKER) :]) for line in lines]


def _latest_output_js(video_id: str) -> str:
    return (
        f"const x = db.videoSummaryCache.find({{youtubeId: {json.dumps(video_id)}, "
        "status: 'completed'}).sort({createdAt: -1}).limit(1).next();"
        f"print({json.dumps(_MARKER)} + EJSON.stringify({{tabs: x.tabs, meta: x.meta, "
        "language: x.language, version: x.version, sourceLanguage: x.sourceLanguage}, "
        "{relaxed: true}));"
    )


# ─── S3 transcript ───────────────────────────────────────────────────────────


async def _segments(video_id: str) -> tuple[list[dict[str, Any]], str | None]:
    raw = await transcript_store.get(video_id)
    if raw is None or not raw.segments:
        return [], None
    return list(raw.segments), raw.language


def _clean_text(segments: list[dict[str, Any]], language: str | None) -> str:
    text = " ".join(str(seg.get("text", "")) for seg in segments)
    return clean_transcript(text, source_language=language)


# ─── Golden tier probe ───────────────────────────────────────────────────────


def _gather_one(video: GoldenVideo, mongo: dict[str, Any] | None, fetch: bool) -> ProbeInput:
    segments, language = asyncio.run(_segments(video.youtube_id))
    fetched = fetch_via_summarizer(video.youtube_id, video.language) if fetch else {}
    base = mongo or {}
    transcript = (
        _clean_text(segments, language) if segments else str(fetched.get("transcript") or "")
    )
    return ProbeInput(
        youtube_id=video.youtube_id,
        title=str(base.get("title") or fetched.get("title") or ""),
        channel=str(base.get("channel") or fetched.get("channel") or ""),
        duration=int(base.get("duration") or fetched.get("duration") or 0),
        youtube_category=str(fetched.get("youtube_category") or ""),
        tags=[str(tag) for tag in fetched.get("tags") or []],
        description=str(fetched.get("description") or ""),
        transcript=transcript,
        metadata_source="mongo+summarizer" if fetch else "mongo",
        transcript_source="s3" if segments else str(fetched.get("transcript_source") or "none"),
        transcript_language=language or fetched.get("transcript_language"),
    )


def gather_golden(cache_dir: Path, *, fetch_metadata: bool) -> dict[str, ProbeInput]:
    """Each live golden video's probe input, from the cache or read once (sequentially)."""
    golden = load_live_golden()
    folder = cache_dir / "golden"
    folder.mkdir(parents=True, exist_ok=True)
    missing = [v.youtube_id for v in golden if not (folder / f"{v.youtube_id}.json").exists()]
    # Dev Mongo is read only for videos the cache lacks (a cached run needs no container).
    mongo = fetch_mongo_rows(missing) if missing else {}
    inputs: dict[str, ProbeInput] = {}
    for video in golden:
        path = folder / f"{video.youtube_id}.json"
        if path.exists():
            inputs[video.golden_id] = ProbeInput(**json.loads(path.read_text(encoding="utf-8")))
            continue
        print(f"reading golden {video.golden_id} ({video.youtube_id})", file=sys.stderr)
        item = _gather_one(video, mongo.get(video.youtube_id), fetch_metadata)
        path.write_text(json.dumps(asdict(item), ensure_ascii=False), encoding="utf-8")
        inputs[video.golden_id] = item
    return inputs


def _golden_context(video: GoldenVideo, item: ProbeInput) -> dict[str, Any]:
    visual = VISUAL_DEMO_LABELS.get(video.golden_id)
    return {
        "golden_id": video.golden_id,
        "labels": {
            "domain": video.domain,
            "format": video.format,
            "has_visual_demo": visual[0] if visual else None,
            # No golden label: every live golden row has a coherent transcript (proposed).
            "transcript_usable": True,
        },
        "metadata_source": item.metadata_source,
        "transcript_source": item.transcript_source,
        "transcript_chars": len(item.transcript),
    }


def golden_probe_requests(inputs: dict[str, ProbeInput]) -> list[AbRequest]:
    """One tier-probe request per golden video, built by ``run_tier_probe`` itself."""
    service = LLMService(LLMProvider(model=HAIKU_45, fast_model=HAIKU_45))
    by_id = {video.golden_id: video for video in load_live_golden()}
    requests: list[AbRequest] = []
    for golden_id, item in inputs.items():
        with (
            recording_provider(lambda _kwargs: _PROBE_ANSWER) as captured,
            patch.object(settings, "LLM_CLASSIFIER_MODEL", HAIKU_45),
        ):
            asyncio.run(run_tier_probe(to_probe_input(item), service))
        requests.append(
            AbRequest(
                request_id=f"golden/{golden_id}/tier_probe",
                call_type="tier_probe",
                source="golden",
                video_id=item.youtube_id,
                kwargs=captured[0],
                expected_output_tokens=_PROBE_OUTPUT_TOKENS,
                context=_golden_context(by_id[golden_id], item),
            )
        )
    return requests


# ─── Hebrew translation ──────────────────────────────────────────────────────


def gather_hebrew(cache_dir: Path, video_id: str = HEBREW_VIDEO) -> dict[str, Any]:
    """The video's English output + transcript segments (cached as one JSON file)."""
    path = cache_dir / f"hebrew-{video_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    doc = _mongosh(_latest_output_js(video_id))[0]
    segments, language = asyncio.run(_segments(video_id))
    data = {"video_id": video_id, "doc": doc, "segments": segments, "segmentsLanguage": language}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def _canned_translation(_kwargs: dict[str, Any]) -> str:
    """The recorder's reply to a translation request: never parsed as an answer."""
    return "[]"


def _translation_requests(
    batches: list[list[str]], source: str, target: str, stage: str, call_type: str, video_id: str
) -> list[AbRequest]:
    service = LLMService(LLMProvider(model=GPT_4O_MINI, fast_model=GPT_4O_MINI))
    requests: list[AbRequest] = []
    for number, batch in enumerate(batches):
        with (
            recording_provider(_canned_translation) as captured,
            patch.object(settings, "LLM_TRANSLATION_MODEL", GPT_4O_MINI),
        ):
            asyncio.run(translate_flat_list(service, batch, source, target, stage_name=stage))
        out = int(sum(map(answer_size, batch)) * _TRANSLATION_TOKENS_PER_CHAR) + 20
        requests.append(
            AbRequest(
                request_id=f"hebrew/{video_id}/{call_type}#{number}",
                call_type=call_type,
                source="hebrew",
                video_id=video_id,
                kwargs=captured[0],
                expected_output_tokens=out,
                context={"originals": batch, "source": source, "target": target},
            )
        )
    return requests


def output_translation_requests(data: dict[str, Any]) -> list[AbRequest]:
    """The translation phase's batches of the English output → Hebrew (first pass)."""
    output = {"tabs": data["doc"]["tabs"], "meta": data["doc"]["meta"]}
    pairs: list[tuple[Any, str]] = []
    translation._collect_strings(output, (), pairs)
    originals = [text for _, text in pairs]
    batches = char_batches(originals, TRANSLATION_BATCH_CHARS, translation._MAX_TRANSLATION_BATCH)
    return _translation_requests(
        batches, "en", _SOURCE_LANGUAGE, "translation_full", "translation_output", data["video_id"]
    )


def rag_translation_requests(data: dict[str, Any]) -> list[AbRequest]:
    """The RAG text's batches of Hebrew transcript blocks → English (first pass)."""
    texts = [block["text"] for block in english._blocks(data["segments"])]
    batches = char_batches(texts, TRANSLATION_BATCH_CHARS)
    stage = english.RAG_TRANSLATION_STAGE
    return _translation_requests(
        batches, _SOURCE_LANGUAGE, "en", stage, "translation_rag", data["video_id"]
    )
