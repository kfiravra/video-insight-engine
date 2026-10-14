"""Vision inputs for the model A/B (pipeline-1min 4.0d; D3's measures, rebuilt).

D3 (1d.4, 2026-10-08) measured vision on uC45_4nnEAI + jMq8lEu-of0 with a
script that was never committed. This rebuilds its inputs on the same base
as ``spotcheck_frame_vision.py``: real frames from dev S3 (read-only), here the
frame manifest's uploaded frames — the 720p hi-res images — so uC45's card
text is legible to a model that can read it. The frames go through the
pipeline's own vision builders (``frame_analyzer``: top-N selection, strided
batches of ≤ 8, the frame labels and count line, max_tokens per batch) into
``recording_provider``; every arm then sends those bytes.

Stored alongside each batch: the frame metadata ``parse_vision_response``
maps labels back with, today's stored descriptions (the 360p pipeline run, for
reference) and the card labels of the frames that carry one.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import sys
from pathlib import Path
from typing import Any

import boto3  # type: ignore[import-untyped]
from _model_ab_capture import recording_provider
from _model_ab_types import SONNET_46, AbRequest
from PIL import Image

from src.config import settings
from src.services.llm_provider import LLMProvider
from src.services.media import frame_analyzer as fa

VISION_VIDEOS: tuple[str, ...] = ("uC45_4nnEAI", "jMq8lEu-of0")
VISION_FRAMES_PER_VIDEO = 16

# uC45 card frames → tokens the card's own text holds (hand-labelled from the
# 720p frames 2026-10-13: the card name and its crew line; a reading counts
# when every token appears in content or text_visible, case and punctuation
# folded). Keyed by manifest frame index. Today's stored 360p descriptions
# misread three of the five (Atmos as "Howling Gab", Gum-Gum Kong Gun as
# "Marco", Bonk Punch & Monster as "Sanji").
CARD_TEXT_LABELS: dict[str, dict[int, tuple[str, ...]]] = {
    "uC45_4nnEAI": {
        5: ("Atmos", "Whitebeard Pirates"),
        9: ("Ganzui", "Rocks Pirates"),
        11: ("Black Maria", "Animal Kingdom Pirates"),
        14: ("Gum-Gum Kong Gun", "Elbaph"),
        16: ("Bonk Punch", "Monster", "Red-Haired Pirates"),
    }
}

# Claude prices an image at about width × height / 750 tokens after it is
# fitted within 1568 px on the long edge and 1.15 megapixels.
_IMAGE_PIXELS_PER_TOKEN = 750
_IMAGE_MAX_EDGE = 1568
_IMAGE_MAX_PIXELS = 1_150_000
_DESCRIPTION_OVERHEAD_TOKENS = 30
_DEFAULT_FRAME_TOKENS = 150


def image_tokens(width: int, height: int) -> int:
    """Claude's image token count for a ``width`` × ``height`` image."""
    scale = min(1.0, _IMAGE_MAX_EDGE / max(width, height, 1))
    scale = min(scale, (_IMAGE_MAX_PIXELS / max(width * height, 1)) ** 0.5)
    return max(1, int(width * scale) * int(height * scale) // _IMAGE_PIXELS_PER_TOKEN)


def data_uri_tokens(uri: str) -> int:
    """Token count of a base64 image data URI (decoded to read its size)."""
    payload = uri.split(",", 1)[-1]
    with Image.open(io.BytesIO(base64.b64decode(payload))) as image:
        return image_tokens(*image.size)


# ─── S3 (read-only) ──────────────────────────────────────────────────────────


def _s3() -> Any:
    return boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        endpoint_url=settings.AWS_ENDPOINT_URL or None,
    )


def _manifest(s3: Any, video_id: str) -> dict[str, Any]:
    key = f"videos/{video_id}/{settings.SCENE_S3_PREFIX}/manifest.json"
    body = s3.get_object(Bucket=settings.S3_BUCKET, Key=key)["Body"].read()
    return json.loads(body)


def _top_entries(manifest: dict[str, Any], count: int) -> list[dict[str, Any]]:
    entries = sorted(manifest["frames"], key=lambda e: -(e.get("totalScore") or 0.0))
    return entries[:count]


def gather_frames(cache_dir: Path, videos: tuple[str, ...], per_video: int) -> dict[str, Any]:
    """Each video's manifest (cached) and its top frames' images, downloaded once."""
    s3 = None
    gathered: dict[str, Any] = {}
    for video_id in videos:
        folder = cache_dir / "frames" / video_id
        folder.mkdir(parents=True, exist_ok=True)
        manifest_path = folder / "manifest.json"
        if not manifest_path.exists():
            s3 = s3 or _s3()
            manifest_path.write_text(json.dumps(_manifest(s3, video_id)), encoding="utf-8")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = _top_entries(manifest, per_video)
        for entry in entries:
            local = folder / Path(entry["s3Key"]).name
            if not local.exists():
                s3 = s3 or _s3()
                print(f"downloading {entry['s3Key']}", file=sys.stderr)
                s3.download_file(settings.S3_BUCKET, entry["s3Key"], str(local))
            entry["path"] = str(local)
        gathered[video_id] = {
            "entries": entries,
            "stored": manifest.get("visionDescriptions") or [],
        }
    return gathered


# ─── Requests ────────────────────────────────────────────────────────────────


def _frame_dicts(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Manifest entries in the shape the vision stage reads (no OCR score: 250 tok/frame)."""
    return [
        {
            "index": entry["index"],
            "path": entry["path"],
            "timestamp": float(entry["timestamp"]),
            "total_score": float(entry.get("totalScore") or 0.0),
            "s3_url": entry["s3Key"],
        }
        for entry in entries
    ]


def _expected_tokens(metadata: list[dict[str, Any]], stored: dict[int, dict[str, Any]]) -> int:
    """Today's stored descriptions' size for these frames (a default where none is stored)."""
    total = _DESCRIPTION_OVERHEAD_TOKENS
    for meta in metadata:
        desc = stored.get(meta["original_index"])
        total += len(json.dumps(desc)) // 4 if desc else _DEFAULT_FRAME_TOKENS
    return total


def _batch_request(
    video_id: str, batch: Any, stored: dict[int, dict[str, Any]], kwargs: dict[str, Any]
) -> AbRequest:
    labels = CARD_TEXT_LABELS.get(video_id, {})
    indices = [meta["original_index"] for meta in batch.metadata]
    return AbRequest(
        request_id=f"frames/{video_id}/vision#{batch.number - 1}",
        call_type="vision",
        source="frames",
        video_id=video_id,
        kwargs=kwargs,
        expected_output_tokens=_expected_tokens(batch.metadata, stored),
        context={
            "metadata": batch.metadata,
            "stored": {str(i): stored[i] for i in indices if i in stored},
            "cards": {str(i): list(labels[i]) for i in indices if i in labels},
        },
    )


async def _video_requests(video_id: str, gathered: dict[str, Any]) -> list[AbRequest]:
    frames = _frame_dicts(gathered["entries"])
    to_send, encoded = fa._encode_frames(fa._select_top_frames(frames, len(frames)))
    batches = fa._prepare_batches(
        fa.load_vision_prompt(), to_send, encoded, settings.FRAME_VISION_TIMEOUT
    )
    stored = {int(d["original_index"]): d for d in gathered["stored"] if "original_index" in d}
    provider = LLMProvider(model=SONNET_46, fast_model=SONNET_46)
    requests: list[AbRequest] = []
    for batch in batches:
        timeout = fa.vision_call_timeout(batch.max_tokens, batch.timeout_cap)
        with recording_provider(lambda _kwargs: "[]") as captured:
            await fa._send_batch(provider, batch, fa._Attempt(1, batch.max_tokens, timeout))
        requests.append(_batch_request(video_id, batch, stored, captured[0]))
    return requests


def vision_requests(frames: dict[str, Any]) -> list[AbRequest]:
    """The vision stage's batches for each video, as the pipeline sends them."""
    requests: list[AbRequest] = []
    for video_id, gathered in frames.items():
        requests.extend(asyncio.run(_video_requests(video_id, gathered)))
    return requests
