"""One prod admin run (cold): SSE timeline client-side + pipeline.timing from prod Mongo
over SSH (read-only).

Env: EVAL_USER_EMAIL / EVAL_USER_PASSWORD = the prod admin login (mapped by prodruns.sh,
never printed). VIE_PROD_API_URL (prod API base URL), VIE_PROD_SSH (ssh destination,
user@host) and VIE_PROD_SSH_KEY (identity file) — no defaults; prodruns.sh loads all of
them from the repo .env.
usage: python prodrun.py <ytid> <label>  → appends to tools/out/prod/prodruns.jsonl and
prints a summary
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
OUT = TOOLS / "out" / "prod"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS.parents[3] / "scripts"))
import devrun  # noqa: E402
from _eval_api import _client, _submit, authenticate  # noqa: E402

API = os.environ.get("VIE_PROD_API_URL", "")
devrun.API = API  # phase 2's reader streamed from the dev API by mistake
SSH = [
    "ssh",
    "-o",
    "ConnectTimeout=10",
    "-o",
    "BatchMode=yes",
    "-i",
    os.environ.get("VIE_PROD_SSH_KEY", ""),
    os.environ.get("VIE_PROD_SSH", ""),
]
JS = """const d=db.videoSummaryCache.findOne({_id:ObjectId('%s')}); const t=(d.pipeline||{}).timing||{};
print(JSON.stringify({status:d.status, version:d.version, pv:d.pipelineVersion, degraded:d.degraded||null,
 tabs:(d.tabs||[]).map(x=>[x.id,x.component]), milestones:t.milestones, costUsd:t.costUsd, counts:t.counts, totalMs:t.totalMs,
 llmFailures:(t.llmFailures||[]).map(f=>({span:f.span,model:f.model,startMs:f.startMs,wallMs:f.wallMs,attempt:f.attempt,error:f.error,rateLimited:f.rateLimited,refusedModel:f.refusedModel,refusalCategory:f.refusalCategory,in:f.inputTokens,out:f.outputTokens,usd:f.costUsd})),
 phases:(t.phases||[]).map(p=>[p.name,p.startMs,p.wallMs]),
 llm:(t.llmCalls||[]).map(c=>({f:c.feature,span:c.span,model:c.model,rm:c.responseModel,s:c.startMs,w:c.wallMs,ttft:c.ttftMs,in:c.inputTokens,out:c.outputTokens,cr:c.cacheReadTokens,cw:c.cacheWriteTokens,cw1h:c.cacheWrite1hTokens,cw5m:c.cacheWrite5mTokens,usd:c.costUsd,att:c.attempt,fb:c.fallbackUsed,fin:c.finishReason,refusedModel:c.refusedModel,refusalCategory:c.refusalCategory})),
 downloads:(t.downloads||[]).map(x=>[x.kind,x.purpose,x.wallMs,x.bytes,x.ok]),
 tm:d.transcriptMeta, src:d.sourceLanguage?d.sourceLanguage.code:null, error:d.error||d.errorCode||null}))"""  # noqa: E501


def _prod_doc(summary_id: str) -> dict:
    inner = (
        'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" '
        '--authenticationDatabase admin video-insight-engine --eval "$(cat)"'
    )
    cmd = SSH + [f"docker exec -i vie-mongodb sh -c '{inner}'"]
    out = (
        subprocess.run(cmd, input=JS % summary_id, capture_output=True, text=True, timeout=90)
        .stdout.strip()
        .splitlines()
    )
    return json.loads(out[-1]) if out else {}


def _log_429(since: str) -> dict:
    cmd = SSH + [
        "for c in vie-summarizer vie-summarizer-worker vie-api; do printf '%s ' $c; "
        f"docker logs --since {since} $c 2>&1 | grep -ciE '(\\b429\\b|rate.?limit)'; done"
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.split()
    return dict(zip(out[::2], out[1::2]))


def _summary(ytid: str, label: str, events: list, doc: dict, log429: dict) -> dict:
    tabs_ev = [e for e in events if e["event"] == "tab_ready"]
    by = {e["event"]: e["t"] for e in reversed(events)}
    m = doc.get("milestones") or {}
    sec = lambda k: round(m[k] / 1000, 1) if isinstance(m.get(k), (int, float)) else None  # noqa: E731
    fails = doc.get("llmFailures") or []
    return {
        "ytid": ytid,
        "label": label,
        "status": doc.get("status"),
        "pv": doc.get("pv"),
        "degraded": doc.get("degraded"),
        "hero_s": sec("synthesisCompleteMs"),
        "provisional_s": sec("firstTabReadyMs"),
        "firstExtracted_s": sec("firstAssembledTabMs"),
        "complete_s": sec("completeMs"),
        "done_s": sec("doneMs"),
        "client": {
            "firstTab": next((e["t"] for e in tabs_ev), None),
            "complete": by.get("complete"),
            "done": by.get("done"),
        },
        "errors": [e for e in events if e["event"] in ("error", "stream_error")],
        "tabs": doc.get("tabs"),
        "costUsd": doc.get("costUsd"),
        "counts": doc.get("counts"),
        "subSecondTimeouts": sum(
            1 for f in fails if (f.get("wallMs") or 0) < 1000 and "Timeout" in str(f.get("error"))
        ),
        "llmFailures": fails,
        "log429": log429,
        "tm": {
            k: (doc.get("tm") or {}).get(k)
            for k in ("source", "captionLang", "captionFetchError", "attempted")
        },
        "src": doc.get("src"),
        "error": doc.get("error"),
    }


async def main() -> int:
    missing = [
        k for k in ("VIE_PROD_API_URL", "VIE_PROD_SSH", "VIE_PROD_SSH_KEY") if not os.environ.get(k)
    ]
    if missing:
        print(f"missing env: {', '.join(missing)} (add to the repo .env)", file=sys.stderr)
        return 2
    ytid, label = sys.argv[1], sys.argv[2]
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    token = await authenticate(API)
    t0 = time.monotonic()
    async with _client(token) as client:
        try:
            sub = await _submit(
                client, API, f"https://www.youtube.com/watch?v={ytid}", bypass_cache=True, cold=True
            )
        except devrun.httpx.HTTPStatusError as exc:
            # the admin's daily cost cap (or any refusal) — recorded, never retried
            refusal = {"status": exc.response.status_code, "body": exc.response.text[:500]}
            print(json.dumps({"ytid": ytid, "label": label, "refused": refusal}))
            OUT.mkdir(parents=True, exist_ok=True)
            with open(OUT / "prodruns.jsonl", "a") as fh:
                fh.write(
                    json.dumps({"ytid": ytid, "label": label, "at": since, "refused": refusal})
                    + "\n"
                )
            return 4
        events = (
            await devrun._stream(client, sub.video_summary_id or "", t0)
            if sub.video_summary_id
            else []
        )
    if not sub.video_summary_id:
        print(json.dumps({"ytid": ytid, "label": label, "refused": str(sub)[:500]}))
        return 3
    await asyncio.sleep(70)  # the job end (RAG wait ≤ 60 s) persists pipeline.timing after `done`
    doc = _prod_doc(sub.video_summary_id)
    log429 = _log_429(since)
    rec = {
        "ytid": ytid,
        "label": label,
        "at": since,
        "videoSummaryId": sub.video_summary_id,
        "events": events,
        "doc": doc,
        "log429": log429,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "prodruns.jsonl", "a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(_summary(ytid, label, events, doc, log429), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
