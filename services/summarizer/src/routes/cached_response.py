"""Cached response helpers — resolve document shapes and stream cached SSE events.

Handles three document shapes:
1. New: meta + tabs (clean architecture)
2. v2: assembledMeta + assembledTabs
3. Legacy: triage + output + summary
"""

from typing import Any, AsyncGenerator

from src.services.pipeline.pipeline_helpers import sse_event


def build_frontend_response(doc: dict) -> dict:
    """Strip pipeline data before sending to frontend.

    Handles both new shape (meta + tabs) and old shape (assembledMeta + assembledTabs).
    """
    # New format: meta + tabs at top level
    meta = doc.get("meta")
    tabs = doc.get("tabs")

    # Backward compat: old format had assembledMeta + assembledTabs
    if not meta and doc.get("assembledMeta"):
        meta = dict(doc["assembledMeta"])
        # Merge synthesis fields into meta
        synthesis = doc.get("synthesis", {})
        if isinstance(synthesis, dict) and synthesis:
            meta.setdefault("tldr", synthesis.get("tldr", ""))
            meta.setdefault("seoDescription", synthesis.get("seoDescription", ""))
            meta.setdefault("masterSummary", synthesis.get("masterSummary", ""))
            meta.setdefault("keyTakeaways", synthesis.get("keyTakeaways", []))
        if doc.get("descriptionAnalysis"):
            meta.setdefault("descriptionAnalysis", doc["descriptionAnalysis"])

    if not tabs:
        tabs = doc.get("assembledTabs", [])

    result: dict = {
        "youtubeId": doc.get("youtubeId", ""),
        "title": doc.get("title", ""),
        "creator": doc.get("creator") or doc.get("channel", ""),
        "duration": doc.get("duration"),
        "thumbnailUrl": doc.get("thumbnailUrl"),
        "status": doc.get("status", "completed"),
        "meta": meta or {},
        "tabs": tabs or [],
    }
    if doc.get("language"):
        result["language"] = doc["language"]
    if doc.get("isRTL") is not None:
        result["isRTL"] = doc["isRTL"]
    if doc.get("sourceLanguage"):
        result["sourceLanguage"] = doc["sourceLanguage"]
    return result


def resolve_triage_event(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve triage data from any document shape (new > triage > assembledMeta > pipeline)."""
    meta = entry.get("meta", {})
    if meta and meta.get("contentTags"):
        return {
            "contentTags": meta.get("contentTags", []),
            "modifiers": meta.get("modifiers", []),
            "primaryTag": meta.get("primaryTag", "learning"),
            "userGoal": meta.get("userGoal", ""),
            "tabs": [],
            "confidence": 1,
        }
    if entry.get("triage"):
        return entry["triage"]
    if entry.get("assembledMeta"):
        am = entry["assembledMeta"]
        return {
            "contentTags": am.get("contentTags", []),
            "modifiers": am.get("modifiers", []),
            "primaryTag": am.get("primaryTag", "learning"),
            "userGoal": am.get("userGoal", ""),
            "tabs": [],
            "confidence": 1,
        }
    pipeline = entry.get("pipeline", {})
    if pipeline.get("triage"):
        return pipeline["triage"]
    return None


def resolve_tabs(entry: dict[str, Any]) -> list[dict]:
    """Resolve tabs from any document shape (new > assembledTabs)."""
    tabs = entry.get("tabs")
    if tabs and isinstance(tabs, list):
        return tabs
    tabs = entry.get("assembledTabs")
    if tabs and isinstance(tabs, list):
        return tabs
    return []


def resolve_synthesis(entry: dict[str, Any]) -> dict[str, Any]:
    """Resolve synthesis data from any document shape.

    ``meta`` wins for every field: it is what the run finally showed, while
    the stored ``synthesis`` may hold an early partial (the memory-done
    emission the API relay persists).
    """
    meta = entry.get("meta", {})
    pipeline = entry.get("pipeline", {})
    synthesis = entry.get("synthesis") or pipeline.get("synthesis") or {}
    summary = entry.get("summary") or {}
    return {
        "tldr": meta.get("tldr") or synthesis.get("tldr") or summary.get("tldr", ""),
        "keyTakeaways": meta.get("keyTakeaways")
        or synthesis.get("keyTakeaways")
        or summary.get("keyTakeaways", []),
        "masterSummary": meta.get("masterSummary")
        or synthesis.get("masterSummary")
        or summary.get("masterSummary", ""),
        "seoDescription": meta.get("seoDescription") or synthesis.get("seoDescription", ""),
    }


async def stream_cached_structured(
    video_summary_id: str, entry: dict[str, Any]
) -> AsyncGenerator[str, None]:
    """Stream a cached structured result as SSE events.

    Handles three document shapes:
    1. New: meta + tabs (clean architecture)
    2. v2: assembledMeta + assembledTabs
    3. Legacy: triage + output + summary
    """
    yield sse_event("cached", {"videoSummaryId": video_summary_id})
    yield sse_event(
        "metadata",
        {
            "title": entry.get("title"),
            "channel": entry.get("creator") or entry.get("channel"),
            "thumbnailUrl": entry.get("thumbnailUrl") or entry.get("thumbnail_url"),
            "duration": entry.get("duration"),
        },
    )

    triage_data = resolve_triage_event(entry)
    if triage_data:
        yield sse_event("triage_complete", triage_data)

    tabs = resolve_tabs(entry)
    if tabs:
        meta = entry.get("meta", {})
        meta_source = (
            meta
            if meta.get("contentTags")
            else entry.get("assembledMeta") or entry.get("triage", {})
        )
        meta_event = {
            "title": entry.get("title", ""),
            "contentTags": meta_source.get("contentTags", []),
            "modifiers": meta_source.get("modifiers", []),
            "primaryTag": meta_source.get("primaryTag", "learning"),
            "tabCount": len(tabs),
            "tabLabels": [
                {"id": t.get("id", ""), "label": t.get("label", ""), "emoji": t.get("emoji", "")}
                for t in tabs
                if isinstance(t, dict)
            ],
        }
        # Degraded docs stay degraded on cached serves — forward the flag so
        # the FE retry affordance shows without a separate doc fetch.
        if meta_source.get("degraded"):
            meta_event["degraded"] = True
        yield sse_event("meta", meta_event)
        for tab in tabs:
            yield sse_event("tab_ready", tab)

    yield sse_event("synthesis_complete", resolve_synthesis(entry))
    yield sse_event("done", {"videoSummaryId": video_summary_id, "cached": True})
    yield "data: [DONE]\n\n"
