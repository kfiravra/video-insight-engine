"""Probe inputs for ``tier_probe_ab.py`` (pipeline-1min task 0.8): gather + render.

Sources, all read-only, cached as one JSON file per video under ``--cache-dir``:
    * dev MongoDB (``docker exec vie-mongodb mongosh``) — title, channel,
      duration and stored frame captions where the golden video exists;
    * the summarizer's own code path inside ``vie-summarizer`` (dev proxy).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

GOLDEN_PATH = Path(__file__).resolve().parent.parent / "dev" / "golden-dataset" / "videos.yaml"
WINDOW_CHARS = 700
DESCRIPTION_CHARS = 500
MAX_TAGS = 15
# Frame annotations the pipeline splices into the transcript:
# "[VISUAL at 3:42: ...]" and "[ON-SCREEN TEXT at 3:42: ...]" (scene_frames.py).
_ANNOTATION_START = re.compile(r"\[(?:VISUAL|ON-SCREEN TEXT)\b")
_FETCH_MARKER = "@@PROBE@@"  # prefixes the one result line the snippets print


@dataclass
class GoldenVideo:
    """A live golden entry reduced to what the probe is scored against."""

    golden_id: str
    youtube_id: str
    domain: str
    format: str
    language: str = "en"


@dataclass
class ProbeInput:
    """Everything Appendix B.1 feeds the probe, plus provenance."""

    youtube_id: str
    title: str
    channel: str
    duration: int
    youtube_category: str
    tags: list[str]
    description: str
    transcript: str
    metadata_source: str
    transcript_source: str
    transcript_language: str | None = None
    frame_captions: list[str] = field(default_factory=list)


def load_live_golden(path: Path = GOLDEN_PATH) -> list[GoldenVideo]:
    """Return the live (not disabled) golden entries in file order."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    videos: list[GoldenVideo] = []
    for entry in data["videos"]:
        if entry.get("disabled"):
            continue
        youtube_id = entry["url"].split("v=")[-1].split("&")[0]
        videos.append(
            GoldenVideo(
                entry["id"],
                youtube_id,
                entry["domain"],
                entry["format"],
                entry.get("language", "en"),
            )
        )
    return videos


# ─── Prompt input (B.1) ──────────────────────────────────────────────────────


def _annotation_end(text: str, start: int) -> int:
    """Index after the bracket closing the annotation at ``start`` (one line max)."""
    line_end = text.find("\n", start)
    line_end = len(text) if line_end < 0 else line_end
    depth = 0
    for index in range(start, line_end):
        if text[index] == "[":
            depth += 1
        elif text[index] == "]":
            depth -= 1
            if depth == 0:
                return index + 1
    last = text.rfind("]", start, line_end)
    return last + 1 if last > start else line_end


def strip_visual_annotations(text: str) -> str:
    """Drop ``[VISUAL ...]`` / ``[ON-SCREEN TEXT ...]`` spans; collapse whitespace."""
    kept: list[str] = []
    position = 0
    for match in _ANNOTATION_START.finditer(text):
        if match.start() < position:
            continue
        kept.append(text[position : match.start()])
        position = _annotation_end(text, match.start())
    kept.append(text[position:])
    return " ".join("".join(kept).split())


def _window(text: str, start: int, size: int) -> str:
    """Slice ``size`` chars from ``start`` and trim partial words at both edges."""
    start = max(0, min(start, len(text) - size))
    chunk = text[start : start + size]
    if start > 0 and " " in chunk:
        chunk = chunk.split(" ", 1)[1]
    if start + size < len(text) and " " in chunk:
        chunk = chunk.rsplit(" ", 1)[0]
    return chunk.strip()


def transcript_windows(transcript: str, size: int = WINDOW_CHARS) -> tuple[str, str, str]:
    """Clean start/middle/end windows; a short transcript is not duplicated."""
    clean = strip_visual_annotations(transcript)
    if len(clean) <= 3 * size:
        thirds = [clean[i * len(clean) // 3 : (i + 1) * len(clean) // 3] for i in range(3)]
        return thirds[0].strip(), thirds[1].strip(), thirds[2].strip()
    mid_start = len(clean) // 2 - size // 2
    return (
        _window(clean, 0, size),
        _window(clean, mid_start, size),
        _window(clean, len(clean), size),
    )


def _sanitize(text: str, max_len: int) -> str:
    """Same rule as ``pipeline_helpers.sanitize_for_prompt``: no braces, no tags."""
    return text.replace("{", "").replace("}", "").replace("<", "‹").replace(">", "›")[:max_len]


def render_prompt(template: str, item: ProbeInput) -> str:
    """Fill the B.1 template for one video."""
    start, mid, end = transcript_windows(item.transcript)
    duration = str(round(item.duration / 60)) if item.duration > 0 else "unknown"
    values = {
        "{title}": _sanitize(item.title, 200),
        "{channel}": _sanitize(item.channel or "Unknown", 100),
        "{duration_minutes}": duration,
        "{youtube_category}": _sanitize(item.youtube_category or "unknown", 60),
        "{tags}": _sanitize(", ".join(item.tags[:MAX_TAGS]) or "none", 500),
        "{description}": _sanitize(item.description.strip(), DESCRIPTION_CHARS) or "none",
        "{window_start}": _sanitize(start, WINDOW_CHARS) or "(no transcript)",
        "{window_mid}": _sanitize(mid, WINDOW_CHARS) or "(no transcript)",
        "{window_end}": _sanitize(end, WINDOW_CHARS) or "(no transcript)",
    }
    prompt = template
    for key, value in values.items():
        prompt = prompt.replace(key, value)
    return prompt


# ─── Gathering (read-only, cached) ───────────────────────────────────────────

_MONGO_JS = """
const ids = __IDS__;
// Walk the document's own fields: every string value under a "frameCaption" key.
function collectCaptions(node, out) {
  if (Array.isArray(node)) { node.forEach(v => collectCaptions(v, out)); return; }
  if (node === null || typeof node !== "object" || node instanceof Date) return;
  for (const [key, value] of Object.entries(node)) {
    if (key === "frameCaption" && typeof value === "string" && value) out.add(value);
    else collectCaptions(value, out);
  }
}
db.videoSummaryCache.find({youtubeId: {$in: ids}}).forEach(x => {
  const caps = new Set();
  collectCaptions(x, caps);
  print("@@PROBE@@" + JSON.stringify({id: x.youtubeId, title: x.title, channel: x.channel,
    duration: x.duration, language: x.language, frameCaptions: [...caps].slice(0, 5)}));
});
"""

_CONTAINER_FETCH = """
import asyncio, json, sys
from src.services.transcription.transcript import get_transcript
from src.services.transcription.transcript_store import transcript_store
from src.services.video.youtube import (
    _build_yt_dlp_opts, _extract_with_retry, _fetch_picked_track, _pick_subtitle_url,
    extract_video_data)

def golden_track(vid, lang):
    info = _extract_with_retry(f"https://www.youtube.com/watch?v={vid}", _build_yt_dlp_opts())
    info = info or {}
    manual, auto = info.get("subtitles") or {}, info.get("automatic_captions") or {}
    track = _pick_subtitle_url(lang, manual, auto)
    segs, _err = _fetch_picked_track(vid, track, lang)
    return " ".join(s.text for s in segs), track.lang if track else None

async def transcript(vid, lang, d):
    raw = await transcript_store.get(vid)
    if raw and raw.segments:
        return " ".join(str(s.get("text", "")) for s in raw.segments), "s3", raw.language
    if d.subtitles and d.language == lang:
        return d.transcript_text, "ytdlp-captions", d.caption_lang
    text, key = await asyncio.to_thread(golden_track, vid, lang)
    if text:
        return text, f"ytdlp-captions ({lang} track; resolver said {d.language})", key
    try:
        _segs, full, _kind, code = await get_transcript(vid)
        return full, "youtube-transcript-api", code
    except Exception as exc:  # reported as the source, never silent
        return "", f"none ({type(exc).__name__})", None

async def main(vid, lang):
    d = await extract_video_data(vid)
    ctx = d.context
    out = {"title": d.title, "channel": d.channel, "duration": d.duration,
           "description": d.description, "tags": ctx.tags if ctx else [],
           "youtube_category": (ctx.youtube_category if ctx else None) or ""}
    text, source, key = await transcript(vid, lang, d)
    out.update(transcript=text, transcript_source=source, transcript_language=key)
    print("@@PROBE@@" + json.dumps(out))

asyncio.run(main(sys.argv[1], sys.argv[2]))
"""


def _marked_lines(stdout: str) -> list[dict[str, object]]:
    return [
        json.loads(line[len(_FETCH_MARKER) :])
        for line in stdout.splitlines()
        if line.startswith(_FETCH_MARKER)
    ]


def fetch_mongo_rows(youtube_ids: list[str]) -> dict[str, dict[str, object]]:
    """Read the golden rows that exist in the dev DB (mongosh in the container)."""
    script = _MONGO_JS.replace("__IDS__", json.dumps(youtube_ids))
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
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True)
    return {str(row["id"]): row for row in _marked_lines(proc.stdout)}


def fetch_via_summarizer(youtube_id: str, language: str) -> dict[str, object]:
    """Metadata + transcript through the summarizer's code path (dev proxy).

    Transcript layers in pipeline order: the S3 blob, then yt-dlp captions —
    re-picked for the golden language when the resolver chose another one
    (the known auto-dub/translated-subs mislabel) — then youtube-transcript-api.
    """
    cmd = ["docker", "exec", "-i", "vie-summarizer", "python", "-", youtube_id, language]
    proc = subprocess.run(
        cmd,
        input=_CONTAINER_FETCH,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    rows = _marked_lines(proc.stdout)
    if not rows:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["no output"]
        raise RuntimeError(f"summarizer fetch failed for {youtube_id}: {tail[0][:300]}")
    return rows[0]


def build_input(
    youtube_id: str, fetched: dict[str, object], mongo: dict[str, object] | None
) -> ProbeInput:
    """Merge Mongo (where present) over the summarizer fetch."""
    base = mongo or fetched
    return ProbeInput(
        youtube_id=youtube_id,
        title=str(base.get("title") or fetched["title"]),
        channel=str(base.get("channel") or fetched["channel"]),
        duration=int(base.get("duration") or fetched["duration"] or 0),
        youtube_category=str(fetched.get("youtube_category") or ""),
        tags=[str(t) for t in fetched.get("tags") or []],
        description=str(fetched.get("description") or ""),
        transcript=str(fetched.get("transcript") or ""),
        metadata_source="mongo+summarizer" if mongo else "summarizer",
        transcript_source=str(fetched.get("transcript_source")),
        transcript_language=fetched.get("transcript_language"),
        frame_captions=list(mongo.get("frameCaptions") or []) if mongo else [],
    )


def _with_mongo_captions(item: ProbeInput, mongo: dict[str, object] | None) -> ProbeInput:
    """Cached inputs take their frame captions from today's (local) Mongo read."""
    if mongo is not None:
        item.frame_captions = [str(c) for c in mongo.get("frameCaptions") or []]
    return item


def gather_inputs(golden: list[GoldenVideo], cache_dir: Path) -> dict[str, ProbeInput]:
    """Load cached inputs, fetching the missing ones sequentially."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    mongo_rows = fetch_mongo_rows([g.youtube_id for g in golden])
    inputs: dict[str, ProbeInput] = {}
    for video in golden:
        path = cache_dir / f"{video.youtube_id}.json"
        mongo = mongo_rows.get(video.youtube_id)
        if path.exists():
            item = ProbeInput(**json.loads(path.read_text(encoding="utf-8")))
            inputs[video.golden_id] = _with_mongo_captions(item, mongo)
            continue
        print(f"fetching {video.golden_id} ({video.youtube_id})", file=sys.stderr)
        fetched = fetch_via_summarizer(video.youtube_id, video.language)
        item = build_input(video.youtube_id, fetched, mongo)
        path.write_text(json.dumps(asdict(item), ensure_ascii=False), encoding="utf-8")
        inputs[video.golden_id] = item
    return inputs


def input_sources(inputs: dict[str, ProbeInput]) -> dict[str, dict[str, object]]:
    """Per-video provenance block stored in the report."""
    return {
        gid: {
            "metadata": i.metadata_source,
            "transcript": i.transcript_source,
            "transcriptLanguage": i.transcript_language,
            "transcriptChars": len(i.transcript),
            "frameCaptions": i.frame_captions,
        }
        for gid, i in inputs.items()
    }


def refresh_report_inputs(out_json: Path, sources: dict[str, dict[str, object]]) -> None:
    """Replace only the inputs block of an existing report (answers untouched)."""
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    payload["inputs"] = sources
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
