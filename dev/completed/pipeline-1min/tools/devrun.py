"""Coordinator dev-run tool: one pipeline run as the eval user, SSE timeline + pipeline.timing.

Usage (repo root):
  . dev/completed/pipeline-1min/tools/_env.sh; load_env_keys '^(EVAL_USER_[A-Z_]+|MONGODB_URI)='
  services/summarizer/.venv/bin/python dev/completed/pipeline-1min/tools/devrun.py <ytid> \
      [--cold] [--no-bypass]
Appends one JSON line per run to tools/out/runs/devruns.jsonl and prints a summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from bson import ObjectId
from pymongo import MongoClient

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parents[3]
sys.path.insert(0, str(REPO / "scripts"))
from _eval_api import _client, _submit, authenticate  # noqa: E402

OUT = TOOLS / "out" / "runs"
API = os.environ.get("DEVRUN_API", "http://localhost:3000")


def _event_extras(event: str, data: dict[str, Any]) -> dict[str, Any]:
    if event == "tab_ready":
        keys = ("id", "label", "component", "position", "final", "provisional", "replaces")
        out = {k: data.get(k) for k in keys if k in data}
        props = data.get("props") or {}
        out["propKeys"] = sorted(props)[:6] if isinstance(props, dict) else None
        items = props.get("items") if isinstance(props, dict) else None
        if isinstance(items, list):
            out["nItems"] = len(items)
        return out
    if event in ("phase", "progress"):
        return {k: data.get(k) for k in ("phase", "status", "message") if k in data}
    if event == "complete":
        return {k: data.get(k) for k in ("tabCount", "tabIds", "processingTimeMs", "degraded")}
    if event in ("error", "stream_error"):
        return {"error": str(data)[:300]}
    return {}


async def _stream(client: httpx.AsyncClient, summary_id: str, t0: float) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    timeout = httpx.Timeout(30.0, read=300.0)
    async with client.stream("GET", f"{API}/api/videos/{summary_id}/stream", timeout=timeout) as s:
        s.raise_for_status()
        async for line in s.aiter_lines():
            if not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict) or not data.get("event"):
                continue
            event = str(data["event"])
            row = {"t": round(time.monotonic() - t0, 1), "event": event}
            row.update(_event_extras(event, data))
            events.append(row)
            if event in ("done", "error", "stream_error"):
                return events
    return events


def _timing(summary_id: str) -> dict[str, Any]:
    uri = os.environ["MONGODB_URI"]
    parts = urlsplit(uri)
    host = parts.hostname or ""
    if host.startswith("vie-"):
        netloc = parts.netloc.replace(host, "localhost")
        uri = urlunsplit(parts._replace(netloc=netloc))
    db = MongoClient(uri, serverSelectionTimeoutMS=8000).get_default_database(
        "video-insight-engine"
    )
    doc = db.videoSummaryCache.find_one({"_id": ObjectId(summary_id)}) or {}
    timing = (doc.get("pipeline") or {}).get("timing") or {}
    tabs = (
        doc.get("tabs") or doc.get("assembledTabs") or (doc.get("output") or {}).get("tabs") or []
    )
    return {
        "status": doc.get("status"),
        "pipelineVersion": doc.get("pipelineVersion"),
        "totalMs": timing.get("totalMs"),
        "milestones": timing.get("milestones"),
        "costUsd": timing.get("costUsd"),
        "counts": timing.get("counts"),
        "phases": [(p["name"], p["startMs"], p["wallMs"]) for p in timing.get("phases", [])],
        "llm": [
            {
                k: c.get(k)
                for k in (
                    "span",
                    "model",
                    "startMs",
                    "wallMs",
                    "inputTokens",
                    "outputTokens",
                    "cacheReadTokens",
                    "cacheWrite1hTokens",
                    "cacheWrite5mTokens",
                    "costUsd",
                )
            }
            for c in timing.get("llmCalls", [])
        ],
        "downloads": timing.get("downloads"),
        "tabs": [
            (t.get("id"), t.get("label"), t.get("component")) for t in tabs if isinstance(t, dict)
        ],
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ytid")
    ap.add_argument("--cold", action="store_true")
    ap.add_argument("--no-bypass", action="store_true")
    args = ap.parse_args()
    token = await authenticate(API)
    t0 = time.monotonic()
    async with _client(token) as client:
        sub = await _submit(
            client,
            API,
            f"https://www.youtube.com/watch?v={args.ytid}",
            bypass_cache=not args.no_bypass,
            cold=args.cold,
        )
        events = (
            await _stream(client, sub.video_summary_id or "", t0) if sub.video_summary_id else []
        )
    await asyncio.sleep(3)
    timing = _timing(sub.video_summary_id) if sub.video_summary_id else {}
    rec = {
        "ytid": args.ytid,
        "cold": args.cold,
        "at": sub.submitted_at.isoformat(),
        "videoSummaryId": sub.video_summary_id,
        "events": events,
        "timing": timing,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "devruns.jsonl", "a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")
    tabs_ev = [e for e in events if e["event"] == "tab_ready"]
    first_visible = next((e["t"] for e in tabs_ev), None)
    by = {e["event"]: e["t"] for e in reversed(events)}
    print(
        json.dumps(
            {
                "ytid": args.ytid,
                "status": timing.get("status"),
                "v": timing.get("pipelineVersion"),
                "firstTabReady_s": first_visible,
                "complete_s": by.get("complete"),
                "done_s": by.get("done"),
                "totalMs": timing.get("totalMs"),
                "milestones": timing.get("milestones"),
                "costUsd": timing.get("costUsd"),
                "tabs": timing.get("tabs"),
                "tabEvents": [
                    (
                        e["t"],
                        e.get("id"),
                        e.get("final"),
                        e.get("provisional"),
                        e.get("replaces"),
                        e.get("nItems"),
                    )
                    for e in tabs_ev
                ],
            },
            indent=1,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
