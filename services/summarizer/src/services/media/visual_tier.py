"""Adaptive frame-pipeline effort tier — how visual-critical is this video?

HIGH tier (unboxings, travel, food, …): the visuals ARE the content, so the
extractor over-selects candidates and vision-describes them all, letting
subject-matter frames win over presenter shots. LOW tier (podcasts, news):
frames are decoration — skip vision entirely. Everything else is STANDARD.

The tier is first needed at Step 6b of scene extraction, long after the
download starts, so the frames branch waits there for the tier probe
(``pipeline/tier_probe.py``) for at most ``PROBE_WAIT_CAP_SECONDS``
(``resolve_tier``). With a probe answer the tier comes from its domain, format
and ``has_visual_demo`` plus title keywords; without one, from cheap metadata
(category + title/tag keywords). ``visualCriticality`` in
packages/shared/src/config/domains.json single-sources the tier table.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from src.models.probe_types import TierProbe
from src.shared_config.domain_config import map_category_to_tag, visual_criticality_config

logger = logging.getLogger(__name__)

VisualTier = Literal["high", "standard", "low"]

# Step 6b comes well after the probe starts (brief: ~10 s vs ~25 s) and the
# probe answers in ~1 s, so the cap only bites when the transcript (hence the
# probe) is late — Whisper/Gemini videos — and then the metadata rule decides.
PROBE_WAIT_CAP_SECONDS = 3.0


def _title_keyword_hit(cfg: dict, *texts: str) -> bool:
    haystack = " ".join(text.lower() for text in texts)
    return any(keyword in haystack for keyword in cfg.get("highTitleKeywords", []))


def metadata_tier(category: str | None, title: str, tags: list[str] | None = None) -> VisualTier:
    """The pre-probe rule: category-derived domain + title/tag keywords.

    Keyword hits win over domain lists (an "unboxing" title in any domain is
    visual-critical); the category-derived domain decides otherwise.
    Unknown/missing config degrades to "standard".
    """
    cfg = visual_criticality_config()
    if not cfg:
        return "standard"
    if _title_keyword_hit(cfg, title, *(tags or [])):
        return "high"
    domain = map_category_to_tag(category) if category else None
    if domain in set(cfg.get("highDomains", [])):
        return "high"
    if domain in set(cfg.get("lowDomains", [])):
        return "low"
    return "standard"


def _probe_tier(probe: TierProbe, title: str) -> VisualTier:
    """Probe rule: title keyword or a reveal format → HIGH; otherwise the domain
    lists decide, gated by ``has_visual_demo`` (a talking-head travel or food
    video stays STANDARD; a news piece built on footage is not LOW).
    """
    cfg = visual_criticality_config()
    if not cfg:
        return "standard"
    if _title_keyword_hit(cfg, title) or probe.format in set(cfg.get("highFormats", [])):
        return "high"
    if probe.domain in set(cfg.get("highDomains", [])) and probe.has_visual_demo:
        return "high"
    if probe.domain in set(cfg.get("lowDomains", [])) and not probe.has_visual_demo:
        return "low"
    return "standard"


def derive_tier(
    probe: TierProbe | None,
    title: str,
    tags: list[str] | None = None,
    *,
    category: str | None = None,
) -> VisualTier:
    """Pick the frame-effort tier: from the probe when there is one, else metadata.

    ``tags`` and ``category`` feed only the metadata fallback (the probe has
    already read them).
    """
    if probe is None:
        return metadata_tier(category, title, tags)
    return _probe_tier(probe, title)


async def await_probe(
    probe_task: asyncio.Future[TierProbe | None] | None,
    cap_seconds: float = PROBE_WAIT_CAP_SECONDS,
) -> TierProbe | None:
    """The probe's answer if it lands within ``cap_seconds``, else ``None``.

    Never cancels the task: the plan still consumes the probe after the
    frames branch has stopped waiting for it.
    """
    if probe_task is None:
        return None
    done, _pending = await asyncio.wait({probe_task}, timeout=cap_seconds)
    if probe_task not in done:
        logger.info("Tier probe not ready after %.1f s — metadata tier", cap_seconds)
        return None
    if probe_task.cancelled():
        return None
    error = probe_task.exception()
    if error is not None:
        logger.warning("Tier probe task failed (metadata tier): %s", error)
        return None
    return probe_task.result()


async def resolve_tier(
    probe_task: asyncio.Future[TierProbe | None] | None,
    title: str,
    *,
    category: str | None = None,
    tags: list[str] | None = None,
    cap_seconds: float = PROBE_WAIT_CAP_SECONDS,
) -> VisualTier:
    """Await the probe (capped), then derive the tier; metadata rule on timeout/None."""
    probe = await await_probe(probe_task, cap_seconds)
    tier = derive_tier(probe, title, tags, category=category)
    logger.info("Visual tier %s from %s", tier, "probe" if probe is not None else "metadata")
    return tier


def tier_settings(tier: VisualTier) -> dict:
    """The tier's knob values from config (overselect / visionMax / keep)."""
    cfg = visual_criticality_config()
    return dict((cfg.get("tiers") or {}).get(tier, {}))
