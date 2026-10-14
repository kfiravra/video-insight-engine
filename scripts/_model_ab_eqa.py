"""EqaMrzd9nZU's plan request for the model A/B round 2 (pipeline-1min 4.0d, D30).

EqaMrzd9nZU (Hebrew) has no replay cassette. Its plan request is rebuilt by
the pipeline's own plan code — ``run_plan`` (``render_plan_parts`` +
``plan_user_content``) into ``recording_provider``, the plan's model pinned to
round 1's TODAY (Sonnet 4.6) — from the latest completed v9 dev run, all
read-only and cached under ``<cache-dir>/eqa/``:

* dev Mongo: title, channel, duration, the run's content format
  (``pipeline.triage.contentFormat``) and source language;
* dev S3 (the pipeline's transcript store): the transcript segments, rendered
  by ``render_transcript`` as the text branch renders them;
* that run's dev Langfuse trace (public read API): the two inputs Mongo/S3 do
  not keep — the tier probe's answer (→ the ``Hint:`` line and the playbook
  domain, through the pipeline's ``parse_tier_probe`` / ``probe_hint`` /
  ``PROBE_CONFIDENCE_THRESHOLD``) and the YouTube description (verbatim in the
  run's description-analysis request).

:func:`trace_check` compares the rebuilt messages with the plan request the
trace recorded; a video half that is not byte-identical means the input is not
faithful (the caller stops).
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from _model_ab_capture import recording_provider
from _model_ab_inputs import _MARKER, _mongosh, _segments
from _model_ab_types import GPT_4O_MINI, SONNET_46, AbRequest

from src.models.probe_types import TierProbe
from src.services.llm import LLMService
from src.services.llm_provider import LLMProvider
from src.services.pipeline.phases.triage import PROBE_CONFIDENCE_THRESHOLD, probe_hint
from src.services.pipeline.plan import run_plan
from src.services.pipeline.tier_probe import parse_tier_probe
from src.services.transcript.render import render_transcript
from src.services.video.description_analyzer import load_prompt

EQA_VIDEO = "EqaMrzd9nZU"
PIPELINE_VERSION = "v9"
_LANGFUSE_KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL")
_LANGFUSE_DEFAULT_HOST = "https://cloud.langfuse.com"
# Langfuse Cloud throttles its read API (≈ 4 reads in 3 s → 429): one read every 2 s.
_READ_SPACING_S = 2.0
_GENERATIONS = ("tier_probe", "description_analysis", "plan")
_PLAN_ANSWER = "{}"
# What Langfuse puts in a stored input: a cut-off long text, a masked PII-looking span.
_TRUNCATED = "[TRUNCATED]"
_REDACTED = "[REDACTED]"


# ─── Read (cached) ───────────────────────────────────────────────────────────


def _env_values(names: tuple[str, ...], env_path: Path) -> dict[str, str]:
    """``names`` from the repo ``.env`` — returned, never exported or printed."""
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() in names:
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _run_js(video_id: str) -> str:
    query = json.dumps(
        {"youtubeId": video_id, "status": "completed", "pipelineVersion": PIPELINE_VERSION}
    )
    return (
        f"const x = db.videoSummaryCache.find({query}).sort({{createdAt: -1}}).limit(1).next();"
        f"print({json.dumps(_MARKER)} + EJSON.stringify({{id: x._id.toHexString(), "
        "createdAt: x.createdAt, pipelineVersion: x.pipelineVersion, title: x.title, "
        "channel: x.channel, duration: x.duration, "
        "contentFormat: x.pipeline.triage.contentFormat, "
        "sourceLanguage: x.sourceLanguage ? x.sourceLanguage.code : null}, {relaxed: true}));"
    )


def _get(client: httpx.Client, url: str, params: dict[str, Any]) -> dict[str, Any]:
    time.sleep(_READ_SPACING_S)
    response = client.get(url, params=params)
    response.raise_for_status()
    return response.json()


def _trace_generations(summary_id: str, env_path: Path) -> dict[str, Any]:
    """The run's tier probe, description-analysis and plan generations (read-only)."""
    creds = _env_values(_LANGFUSE_KEYS, env_path)
    host = (creds.get("LANGFUSE_BASE_URL") or _LANGFUSE_DEFAULT_HOST).rstrip("/")
    auth = (creds["LANGFUSE_PUBLIC_KEY"], creds["LANGFUSE_SECRET_KEY"])
    with httpx.Client(base_url=host, auth=auth, timeout=30.0) as client:
        traces = _get(client, "/api/public/traces", {"name": f"pipeline:{summary_id}"})["data"]
        if len(traces) != 1:
            raise ValueError(f"pipeline:{summary_id}: {len(traces)} traces, expected 1")
        params = {"traceId": traces[0]["id"], "type": "GENERATION", "limit": 100}
        found = _get(client, "/api/public/observations", params)["data"]
    by_name = {o["name"]: o for o in found if o.get("name") in _GENERATIONS}
    missing = sorted(set(_GENERATIONS) - set(by_name))
    if missing:
        raise ValueError(f"trace {traces[0]['id']} lacks generations {missing}")
    return {
        "trace_id": traces[0]["id"],
        "probe_output": by_name["tier_probe"]["output"],
        "description_request": by_name["description_analysis"]["input"],
        "plan_request": by_name["plan"]["input"],
        "plan_model": by_name["plan"].get("model"),
        "plan_usage": by_name["plan"].get("usageDetails") or by_name["plan"].get("usage"),
        "plan_metadata": by_name["plan"].get("metadata"),
    }


def gather_eqa(cache_dir: Path, env_path: Path, video_id: str = EQA_VIDEO) -> dict[str, Any]:
    """The latest completed v9 dev run's plan inputs (cached as one JSON file)."""
    path = cache_dir / "eqa" / "inputs.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    print(f"reading {video_id}: dev Mongo, S3, Langfuse trace (read-only)", file=sys.stderr)
    run = _mongosh(_run_js(video_id))[0]
    segments, language = asyncio.run(_segments(video_id))
    data = {
        "video_id": video_id,
        "run": run,
        "segments": segments,
        "segments_language": language,
        "trace": _trace_generations(run["id"], env_path),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


# ─── Build ───────────────────────────────────────────────────────────────────


def description_of(request: list[dict[str, Any]]) -> str:
    """The YouTube description the run's description-analysis request carried.

    Cut out of that request by the template's own text around ``{description}``.
    """
    prefix, _, suffix = load_prompt("description_analysis").partition("{description}")
    content = str(request[0]["content"])
    if not (content.startswith(prefix) and content.endswith(suffix)):
        raise ValueError("the traced description request does not match description_analysis.txt")
    return content[len(prefix) : len(content) - len(suffix)]


def run_probe(data: dict[str, Any]) -> TierProbe:
    """The run's probe answer; it must have set the content format and the playbook domain."""
    probe = parse_tier_probe(str(data["trace"]["probe_output"]))
    if probe is None or probe.format != data["run"]["contentFormat"]:
        raise ValueError(f"probe answer {probe} does not match the run's content format")
    if probe.confidence <= PROBE_CONFIDENCE_THRESHOLD:
        raise ValueError("the playbook domain is the YouTube category, which the run did not keep")
    return probe


def _plan_kwargs(data: dict[str, Any]) -> dict[str, Any]:
    """The plan request ``run_plan`` sends for the run's inputs (Sonnet 4.6)."""
    run = data["run"]
    probe = run_probe(data)
    transcript = render_transcript(data["segments"], source_language=run["sourceLanguage"])
    service = LLMService(LLMProvider(model=SONNET_46, fast_model=GPT_4O_MINI))
    with recording_provider(lambda _kwargs: _PLAN_ANSWER) as captured:
        asyncio.run(
            run_plan(
                title=run["title"],
                channel=run["channel"] or "",
                description=description_of(data["trace"]["description_request"]),
                duration=int(run["duration"] or 0),
                category_hint=probe.domain,
                content_format=probe.format,
                transcript=transcript,
                llm_service=service,
                probe_hint=probe_hint(probe),
            )
        )
    if len(captured) != 1:
        raise ValueError(f"run_plan sent {len(captured)} requests, expected 1")
    return captured[0]


def eqa_plan_request(data: dict[str, Any]) -> AbRequest:
    """Eqa's plan request as an A/B request (expected output = the run's own answer size)."""
    run = data["run"]
    usage = data["trace"].get("plan_usage") or {}
    return AbRequest(
        request_id=f"dev/{data['video_id']}/plan#0",
        call_type="plan",
        source="dev",
        video_id=data["video_id"],
        kwargs=_plan_kwargs(data),
        expected_output_tokens=int(usage.get("output") or 0),
        context={
            "category_hint": run_probe(data).domain,
            "content_format": run["contentFormat"],
            "span": "plan",
            "duration": int(run["duration"] or 0),
            "dev_run": run["id"],
            "trace_id": data["trace"]["trace_id"],
        },
    )


def _blocks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = messages[0]["content"]
    return content if isinstance(content, list) else [{"type": "text", "text": content}]


def match_traced(ours: str, traced: str) -> dict[str, Any]:
    """``ours`` against Langfuse's copy of it, which may end in ``[TRUNCATED]`` (long
    text) and hold ``[REDACTED]`` spans (its PII masking): every char Langfuse kept
    must be ours, in order; a redaction stands for up to 200 of our chars."""
    cut = traced.rfind(_TRUNCATED)
    kept = traced[:cut].removesuffix("\n\n") if cut != -1 else traced
    pattern = "(.{1,200}?)".join(re.escape(part) for part in kept.split(_REDACTED))
    matcher = re.match if cut != -1 else re.fullmatch
    found = matcher(pattern, ours, re.DOTALL)
    return {
        "matches": found is not None,
        "identical": ours == traced,
        "truncated_at": len(kept) if cut != -1 else None,
        "redacted": list(found.groups()) if found else [],
        "chars": [len(ours), len(traced)],
    }


def trace_check(request: AbRequest, data: dict[str, Any]) -> dict[str, Any]:
    """The rebuilt plan messages against the ones the dev run sent, block by block.

    Faithful = every block matches what the trace kept and the video half (the
    last block, the only per-video one) is byte-identical, untruncated.
    """
    ours, theirs = _blocks(request.kwargs["messages"]), _blocks(data["trace"]["plan_request"])
    rows = [
        {
            "block": index,
            **match_traced(str(mine.get("text")), str(sent.get("text"))),
            "cache_control_equal": mine.get("cache_control") == sent.get("cache_control"),
        }
        for index, (mine, sent) in enumerate(zip(ours, theirs, strict=False))
    ]
    same_shape = len(ours) == len(theirs) and bool(rows)
    return {
        "blocks": [len(ours), len(theirs)],
        "rows": rows,
        "model_traced": data["trace"].get("plan_model"),
        "cache_read_traced": (data["trace"].get("plan_metadata") or {}).get("cacheReadTokens"),
        "faithful": same_shape
        and all(r["matches"] and r["cache_control_equal"] for r in rows)
        and rows[-1]["identical"],
    }
