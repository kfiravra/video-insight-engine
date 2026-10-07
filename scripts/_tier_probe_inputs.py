"""Read-only input gathering for ``tier_probe_ab.py`` (pipeline-1min task 0.8).

Sources, all read-only, cached as one JSON file per video under ``--cache-dir``:
    * dev MongoDB (``docker exec vie-mongodb mongosh``) — title, channel,
      duration and stored frame captions where the golden video exists;
    * the summarizer's own code path inside ``vie-summarizer`` (dev proxy).
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

GOLDEN_PATH = Path(__file__).resolve().parent.parent / "dev" / "golden-dataset" / "videos.yaml"
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


_MONGO_JS = """
const ids = __IDS__;
db.videoSummaryCache.find({youtubeId: {$in: ids}}).forEach(x => {
  const caps = (JSON.stringify(x).match(/"frameCaption":"[^"]{1,200}/g) || [])
    .map(c => c.slice(15));
  print("@@PROBE@@" + JSON.stringify({id: x.youtubeId, title: x.title, channel: x.channel,
    duration: x.duration, language: x.language, frameCaptions: [...new Set(caps)].slice(0, 5)}));
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


def gather_inputs(golden: list[GoldenVideo], cache_dir: Path) -> dict[str, ProbeInput]:
    """Load cached inputs, fetching the missing ones sequentially."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    mongo_rows = fetch_mongo_rows([g.youtube_id for g in golden])
    inputs: dict[str, ProbeInput] = {}
    for video in golden:
        path = cache_dir / f"{video.youtube_id}.json"
        if path.exists():
            inputs[video.golden_id] = ProbeInput(**json.loads(path.read_text(encoding="utf-8")))
            continue
        print(f"fetching {video.golden_id} ({video.youtube_id})", file=sys.stderr)
        fetched = fetch_via_summarizer(video.youtube_id, video.language)
        item = build_input(video.youtube_id, fetched, mongo_rows.get(video.youtube_id))
        path.write_text(json.dumps(asdict(item), ensure_ascii=False), encoding="utf-8")
        inputs[video.golden_id] = item
    return inputs
