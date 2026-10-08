"""Assembly orchestrator — assembles final VIEResponse from pipeline outputs.

Resolves data sources, runs assemblers, validates domain requirements,
injects frame thumbnails, and produces the final {meta, tabs[]} shape.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any

from src.shared_config.domain_config import (
    assembler_item_caps,
    build_fallback_tabs,
    domain_requirements,
    effective_requirements,
    quiz_policy,
    ruled_out_requirements,
    sibling_datasources,
)
from src.utils.data_helpers import is_empty_data

from .assemblers_learning import assemble_display_section
from .assemblers_overhaul import _normalize_filmstrip_frame
from .attachments import attach_secondaries
from .cross_tab import resolve_cross_tab_links
from .density import enforce_density
from .normalizers import _chapters_to_moments
from .promotion import demote_component, promote_component
from .registry import ASSEMBLER_REGISTRY, infer_component

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────
# Data Source Resolution
# ─────────────────────────────────────────────────────


def resolve_data_source(
    data_source: str,
    extraction: dict | None,
    enrichment: dict | None = None,
) -> Any:
    """Resolve a dot-notation dataSource to actual data.

    Examples:
        "travel.itinerary"   -> extraction["travel"]["itinerary"]
        "enrichment.quiz"    -> enrichment["quiz"]
        "fitness"            -> extraction["fitness"]
    """
    if not data_source:
        return None

    parts = data_source.split(".")

    if parts[0] == "enrichment":
        if not enrichment:
            return None
        obj: Any = enrichment
        for part in parts[1:]:
            if part == "*":
                return obj
            if isinstance(obj, dict) and part in obj:
                obj = obj[part]
            else:
                return None
        return obj

    obj = extraction
    for part in parts:
        if part == "*":
            return obj
        if isinstance(obj, dict) and part in obj:
            obj = obj[part]
        else:
            return None
    return obj


# ─────────────────────────────────────────────────────
# Frame Utilities
# ─────────────────────────────────────────────────────


# With only ~25 frames per video, duplication is caused by frame REUSE, not
# match distance — keep the window wide enough for long videos (~72s frame
# spacing at 30min) and enforce exclusivity instead.
_FRAME_MATCH_MAX_DISTANCE = 30.0

# A frame may serve two items only when they reference (nearly) the same
# moment — e.g. the same pull in spot_explorer and moment_track.
_FRAME_REUSE_EPS = 2.0

# How many next-nearest candidates the injector tries past filler refusals
# before an item goes without a thumbnail.
_MAX_FRAME_ATTEMPTS = 4


def _frame_identity(frame: dict) -> str | None:
    """Durable identity for reuse tracking; None when nothing to collide on."""
    return frame.get("s3_key") or frame.get("s3_url") or None


def _item_timestamp(item: dict) -> float | None:
    """Resolve an item's numeric timestamp in seconds, or None.

    Explicit None-checks per key — the old `a or b or c` chain made a
    legitimate `seconds: 0` fall through to the display string ("0:00"),
    so second-zero moments were structurally never frame-matched.
    """
    for key in ("timestamp", "startTime", "seconds", "time"):
        value = item.get(key)
        if value is None or isinstance(value, str):
            continue
        try:
            return float(value)
        except (ValueError, TypeError):
            continue
    return None


def find_nearest_frame(
    timestamp_seconds: float,
    frames: list[dict],
    max_distance: float = _FRAME_MATCH_MAX_DISTANCE,
    used: dict[str, float] | None = None,
    tab_used: set[str] | None = None,
    reuse_eps: float = _FRAME_REUSE_EPS,
    exclude: set[str] | None = None,
) -> dict | None:
    """Find the closest eligible frame to a timestamp.

    A frame already attached elsewhere (tracked by `used`/`tab_used`) is
    skipped unless the requesting timestamp is within `reuse_eps` of its
    prior use — same-moment reuse across tabs is intended, everything else
    is visual duplication. Within one tab (`tab_used`) reuse is never OK.
    `exclude` skips specific frames outright — the injector uses it to retry
    the next-nearest candidate after a filler (talking-head) refusal.
    """
    for frame in sorted(frames, key=lambda f: abs(f.get("timestamp", 0) - timestamp_seconds)):
        if abs(frame.get("timestamp", 0) - timestamp_seconds) > max_distance:
            return None
        key = _frame_identity(frame)
        if key is not None:
            if exclude is not None and key in exclude:
                continue
            if tab_used is not None and key in tab_used:
                continue
            if used is not None and key in used and abs(used[key] - timestamp_seconds) > reuse_eps:
                continue
        return frame
    return None


# Scene types that should never feed a frame thumbnail — these are filler
# (presenter face, video opener) and adding them as "evidence" pretends the
# frame supports the claim when it doesn't. Talking_head frames pass through
# only when the vision LLM marks them as educationally valuable.
_NON_EVIDENCE_SCENE_TYPES: frozenset[str] = frozenset(
    {
        "talking_head",
        "intro",
        "outro",
        "transition",
        "black",
        "logo",
    }
)


def _attach_frame_metadata(
    item: dict,
    source_frame: dict,
    frame_descriptions: list[dict] | None,
) -> bool:
    """Enrich an item with frame thumb + (when available) vision metadata.

    Vision descriptions carry the actual semantic payload — scene_type,
    educational_value, and a one-line `content` caption — but they're cheap
    to look up and the audit showed they're computed and thrown away. Here we
    fuse them onto the item so MomentTrack/StepPlayer/CodeExplorer/Comparison
    can render evidence captions alongside the thumbnail.

    Returns True when the thumbnail was attached; False when the frame was
    declined as filler (so it stays available for other items).
    """
    desc: dict | None = None
    scene_type = ""
    educational = ""
    if frame_descriptions:
        desc = find_description_for_frame(source_frame, frame_descriptions)
        if desc:
            scene_type = str(desc.get("scene_type") or "").strip().lower()
            educational = str(desc.get("educational_value") or "").strip()
            visual_subject = str(desc.get("visual_subject") or "").strip().lower()
            # Skip filler scenes — and frames vision says are presenter-
            # dominated — unless the LLM explicitly flagged educational
            # signal. Protects against pasting random presenter crops next
            # to substantive content rows. "content"/"mixed" always pass.
            is_filler = scene_type in _NON_EVIDENCE_SCENE_TYPES or visual_subject == "presenter"
            if is_filler and not educational:
                return False

    item["thumbnailUrl"] = source_frame.get("s3_url", "")
    if source_frame.get("s3_key"):
        item["s3Key"] = source_frame["s3_key"]
    if source_frame.get("ocr_text") and not item.get("frameOcr"):
        item["frameOcr"] = source_frame["ocr_text"]

    if not desc:
        return True

    caption = str(desc.get("content") or "").strip()
    if caption:
        item["frameCaption"] = caption
    if scene_type:
        item["frameSceneType"] = scene_type
    if educational:
        item["frameEvidence"] = educational
    text_visible = str(desc.get("text_visible") or "").strip()
    if text_visible and not item.get("frameOcr"):
        item["frameOcr"] = text_visible
    return True


def inject_frame_thumbnails(
    items: list[dict],
    frames: list[dict],
    all_frames: list[dict] | None = None,
    frame_descriptions: list[dict] | None = None,
    used: dict[str, float] | None = None,
    tab_used: set[str] | None = None,
    max_distance: float = _FRAME_MATCH_MAX_DISTANCE,
) -> list[dict]:
    """Add thumbnailUrl + optional vision metadata to items with timestamps.

    s3Key is the durable S3 object key; the API regenerates a fresh presigned
    URL from it on every read. thumbnailUrl is the at-generation-time URL —
    used by the SSE stream for immediate display. When frame_descriptions is
    provided, also attaches frameCaption / frameSceneType / frameEvidence /
    frameOcr so downstream components can render evidence inline.

    `used` (response-wide) and `tab_used` (per tab) track frames already
    attached so different moments never share a thumbnail — an item with no
    eligible frame gets none rather than a wrong duplicate.
    """
    if not frames:
        return items

    s3_frames = [f for f in frames if f.get("s3_url")]
    match_pool = all_frames if all_frames else s3_frames

    for item in items:
        ts_float = _item_timestamp(item)
        if ts_float is None:
            continue
        # Retry past filler refusals: a talking-head frame declined by
        # _attach_frame_metadata should not cost the item its thumbnail when
        # the next-nearest frame in range is real content. Refused frames are
        # NOT marked used — a different item may still legitimately claim one
        # (e.g. when vision flagged it educationally valuable for its moment).
        refused: set[str] = set()
        for _ in range(_MAX_FRAME_ATTEMPTS):
            nearest = find_nearest_frame(
                ts_float,
                match_pool,
                max_distance=max_distance,
                used=used,
                tab_used=tab_used,
                exclude=refused,
            )
            source: dict | None = None
            if nearest and nearest.get("s3_url"):
                source = nearest
            elif nearest and s3_frames:
                source = find_nearest_frame(
                    ts_float,
                    s3_frames,
                    max_distance=max_distance,
                    used=used,
                    tab_used=tab_used,
                    exclude=refused,
                )
            if source is None:
                break
            if _attach_frame_metadata(item, source, frame_descriptions):
                key = _frame_identity(source)
                if key is not None:
                    if used is not None:
                        used.setdefault(key, ts_float)
                    if tab_used is not None:
                        tab_used.add(key)
                break
            key = _frame_identity(source)
            if key is None:
                break
            refused.add(key)
    return items


# Relaxed second-pass window for the moment grid — tighter than the strict
# pass's 30s so backfilled thumbs stay relevant to their moment.
_BACKFILL_MAX_DISTANCE = 15.0

# Grace beyond the reported duration before a timestamp counts as impossible —
# metadata durations round and live streams trim, so a few seconds of overhang
# is legitimate.
_TIMESTAMP_OVER_DURATION_TOLERANCE = 10.0


def _drop_impossible_timestamps(tabs: list[dict], video_duration: float | int | None) -> int:
    """Drop moment_track items whose timestamp lies beyond the video's end.

    Extraction occasionally hallucinates timestamps past the video duration
    (observed live: a 7:50 video carrying moments at 8:00-12:00). Such items
    can neither seek nor ever receive a real frame — dropping them beats
    shipping broken affordances. Returns the number of dropped items.
    """
    try:
        limit = float(video_duration) if video_duration else 0.0
    except (TypeError, ValueError):
        limit = 0.0
    if limit <= 0:
        return 0
    limit += _TIMESTAMP_OVER_DURATION_TOLERANCE

    dropped = 0
    for tab in tabs:
        if tab.get("component") != "moment_track":
            continue
        props = tab.get("props")
        if not isinstance(props, dict) or not isinstance(props.get("items"), list):
            continue
        items = props["items"]
        kept = [
            it
            for it in items
            if not (isinstance(it, dict) and (_item_timestamp(it) or 0.0) > limit)
        ]
        dropped += len(items) - len(kept)
        if len(kept) != len(items):
            props["items"] = kept
    if dropped:
        logger.warning(
            "Assembly: dropped %d moment(s) with timestamps beyond video duration (%.0fs)",
            dropped,
            limit - _TIMESTAMP_OVER_DURATION_TOLERANCE,
        )
    return dropped


def _drop_emptied_moment_tabs(tabs: list[dict], dropped_sink: list[dict]) -> None:
    """Remove moment_track tabs whose item list the timestamp hygiene emptied.

    _validate_assembled_props ran before the drop pass, so without this an
    all-hallucinated moment tab would ship as an empty surface (persisted and
    cached until the next PIPELINE_VERSION bump).
    """
    for idx in reversed(range(len(tabs))):
        tab = tabs[idx]
        if tab.get("component") != "moment_track":
            continue
        props = tab.get("props")
        if isinstance(props, dict) and props.get("items"):
            continue
        popped = tabs.pop(idx)
        logger.warning(
            "TAB DROPPED: id=%r, component='moment_track' — every moment was beyond duration",
            popped.get("id"),
        )
        dropped_sink.append(
            {
                "id": popped.get("id", ""),
                "component": "moment_track",
                "dataSource": "",
                "reason": "all_timestamps_impossible",
            }
        )


def _backfill_moment_thumbnails(
    tabs: list[dict],
    frames: list[dict],
    all_frames: list[dict] | None,
    frame_descriptions: list[dict] | None,
) -> None:
    """Second-pass thumbnail fill for moment_track items left frameless.

    The strict pass enforces response-wide exclusivity, which can leave
    25-50% of moment cards as empty plates in the grid view. This pass
    relaxes ONLY the cross-tab ledger (a moment card sharing a frame with a
    tier item is fine for gallery coverage) while still forbidding duplicate
    frames within the tab, and uses a tighter ±15s window. moment_track only
    by design — positional components keep strict semantics.
    """
    if not frames:
        return
    for tab in tabs:
        if tab.get("component") != "moment_track":
            continue
        props = tab.get("props")
        if not isinstance(props, dict):
            continue
        items = props.get("items")
        if not isinstance(items, list):
            continue
        frameless = [it for it in items if isinstance(it, dict) and not it.get("thumbnailUrl")]
        if not frameless:
            continue
        # Mirror _frame_identity (s3_key or s3_url): a frame attached without
        # an s3_key carries only its URL on the item, and rebuilding the
        # ledger from s3Key alone would let the relaxed pass re-attach that
        # same frame to a frameless sibling — the exact within-tab duplicate
        # this pass forbids.
        tab_used = {
            key
            for it in items
            if isinstance(it, dict)
            for key in (it.get("s3Key") or it.get("thumbnailUrl"),)
            if isinstance(key, str) and key
        }
        filled_before = len(items) - len(frameless)
        inject_frame_thumbnails(
            frameless,
            frames,
            all_frames=all_frames,
            frame_descriptions=frame_descriptions,
            used=None,
            tab_used=tab_used,
            max_distance=_BACKFILL_MAX_DISTANCE,
        )
        gained = sum(1 for it in frameless if it.get("thumbnailUrl"))
        if gained:
            logger.info(
                "Assembly: moment thumbnail backfill +%d (had %d/%d)",
                gained,
                filled_before,
                len(items),
            )


def find_description_for_frame(
    frame: dict,
    frame_descriptions: list[dict],
    tolerance: float = 5.0,
) -> dict | None:
    """Find a vision description matching a frame by timestamp."""
    if not frame_descriptions:
        return None
    ts = frame.get("timestamp", 0)
    best = None
    best_distance = float("inf")
    for desc in frame_descriptions:
        distance = abs(desc.get("timestamp_sec", 0) - ts)
        if distance < best_distance:
            best_distance = distance
            best = desc
    return best if best_distance <= tolerance else None


# ─────────────────────────────────────────────────────
# Domain Requirement Validation
# ─────────────────────────────────────────────────────

# Single-sourced from domains.json `domainRequirements` (project-score-9 4.5d).
# Raw config mirror kept for config-parity tests; runtime policy goes through
# effective_requirements() which also merges (domain, format) playbooks.
_DOMAIN_REQUIREMENTS: dict[str, dict] = domain_requirements()


# A required component is satisfied by any of its promotion targets — e.g.
# `comparison` is promoted to `comparison_radar` when it has enough axes, which
# still fulfils the review domain's need for a comparison tab.
_REQUIREMENT_EQUIVALENTS: dict[str, frozenset[str]] = {
    "comparison": frozenset({"comparison", "comparison_radar"}),
    "step_player": frozenset({"step_player", "step_flow_canvas"}),
}

# droppedTabs reason for a required component the plan's evidence ruled out
# (pipeline-1min 1d.7) — recorded for the audit trail, never a planned tab.
REQUIREMENT_EVIDENCE_FALSE = "requirement_evidence_false"


def _backfill_required_component(
    tabs: list[dict],
    primary_tag: str,
    accepted: frozenset[str],
    extraction: dict | None,
    enrichment: dict | None,
    synthesis: dict | None,
    video_meta: dict | None,
) -> bool:
    """Append a tab for a missing required component from the registry defaults.

    The planner can drop a domain's signature component (e.g. tech's
    ``code_playground``) by betting every tab on a field this video left empty.
    Rather than only warning, try each defaultTab in `primary_tag` whose component
    is accepted, resolving its dataSource with the same fallbacks the main loop
    uses. On the first one that has real data and assembles + validates, append it.
    """
    existing_ids = {t.get("id") for t in tabs}
    for cand in build_fallback_tabs(primary_tag):
        component = cand.get("component", "") if isinstance(cand, dict) else ""
        if component not in accepted or cand.get("id", "") in existing_ids:
            continue
        data_source = cand.get("dataSource", "")
        data = resolve_data_source(data_source, extraction, enrichment)
        if is_empty_data(data):
            data = _cross_domain_fallback(
                data_source, extraction, enrichment
            ) or _in_domain_sibling_fallback(data_source, component, extraction, enrichment)
        if is_empty_data(data):
            continue
        tab_with_hints = {
            **cand,
            "_primary_tag": primary_tag,
            "_synthesis": synthesis,
            "_video_meta": video_meta,
        }
        assembler = ASSEMBLER_REGISTRY.get(component, assemble_display_section)
        try:
            props = assembler(tab_with_hints, data, extraction, enrichment)
        except Exception as e:
            logger.warning(
                "Backfill assembler raised for %r (%s): %s", cand.get("id"), component, e
            )
            continue
        if props is None or not _validate_assembled_props(component, props):
            continue
        tabs.append(
            {
                "id": cand.get("id", ""),
                "label": cand.get("label", cand.get("id", "")),
                "emoji": cand.get("emoji", ""),
                "component": component,
                "props": props,
                "goal": cand.get("goal", ""),
                "crossTabLinks": [],
            }
        )
        logger.info(
            "Backfilled required component '%s' for domain '%s' from %s",
            component,
            primary_tag,
            data_source,
        )
        return True
    return False


def demotion_blocked(
    content_tags: Iterable[str], evidence: Mapping[str, bool] | None
) -> frozenset[str]:
    """Components no demotion may land on: the ruled-out ones and their equivalents.

    Gated over EVERY content tag, not only the primary: a travel-primary food
    vlog with ``has_ingredients: false`` must not demote a spot_explorer onto a
    checklist because travel itself has no checklist gate.
    """
    ruled_out = {c for tag in content_tags for c in ruled_out_requirements(tag, evidence)}
    return frozenset().union(*(_REQUIREMENT_EQUIVALENTS.get(c, frozenset({c})) for c in ruled_out))


def count_dropped_tabs(dropped: list[dict]) -> int:
    """Planned tabs that died. An evidence-skipped requirement is recorded in
    ``droppedTabs`` for the audit trail but was never a tab, so it is not counted."""
    return sum(1 for entry in dropped if entry.get("reason") != REQUIREMENT_EVIDENCE_FALSE)


def _record_evidence_skip(
    primary_tag: str, component: str, key: str, dropped_sink: list[dict] | None
) -> None:
    logger.info(
        "Required '%s' for domain '%s' not backfilled: %s=false in the plan's evidence",
        component,
        primary_tag,
        key,
    )
    if dropped_sink is not None:
        dropped_sink.append(
            {
                "id": "",
                "component": component,
                "dataSource": "",
                "reason": REQUIREMENT_EVIDENCE_FALSE,
                "detail": f"{key}=false",
            }
        )


def _pop_forbidden(
    tabs: list[dict],
    forbidden: frozenset[str],
    primary_tag: str,
    content_format: str | None,
    dropped_sink: list[dict] | None,
) -> None:
    for idx in reversed(range(len(tabs))):
        component = tabs[idx].get("component", "")
        if component not in forbidden:
            continue
        popped = tabs.pop(idx)
        logger.warning(
            "TAB DROPPED: id=%r component=%r — forbidden for domain=%s format=%s",
            popped.get("id"),
            component,
            primary_tag,
            content_format,
        )
        if dropped_sink is not None:
            dropped_sink.append(
                {
                    "id": popped.get("id", ""),
                    "component": component,
                    "dataSource": "",
                    "reason": "domain_forbidden",
                }
            )


def _cap_component_counts(
    tabs: list[dict], limits: dict[str, int], primary_tag: str, dropped_sink: list[dict] | None
) -> None:
    for comp, limit in limits.items():
        indices = [i for i, t in enumerate(tabs) if t.get("component") == comp]
        if len(indices) <= limit:
            continue
        logger.warning(
            "'%s' appears %dx (max %d for domain '%s') — keeping first %d only",
            comp,
            len(indices),
            limit,
            primary_tag,
            limit,
        )
        for idx in reversed(indices[limit:]):
            popped = tabs.pop(idx)
            if dropped_sink is not None:
                dropped_sink.append(
                    {
                        "id": popped.get("id", ""),
                        "component": comp,
                        "dataSource": "",
                        "reason": "domain_max_cap",
                    }
                )


def _validate_domain_requirements(
    tabs: list[dict],
    primary_tag: str,
    extraction: dict | None = None,
    enrichment: dict | None = None,
    synthesis: dict | None = None,
    video_meta: dict | None = None,
    dropped_sink: list[dict] | None = None,
    content_format: str | None = None,
    evidence: Mapping[str, bool] | None = None,
) -> None:
    """Validate assembled tabs against domain requirements in-place.

    When a required component is missing, attempt to backfill it from the registry
    defaults using real extraction data before falling back to a warning — unless
    the plan's ``evidence`` rules it out (recorded as ``requirement_evidence_false``).
    Forbidden components (domain policy + playbook union) are popped — this is
    the backstop behind the plan-time enforcement, covering cached plans,
    backfill paths, and promotion outputs.
    """
    reqs = effective_requirements(primary_tag, content_format)
    _pop_forbidden(
        tabs, reqs.get("forbidden") or frozenset(), primary_tag, content_format, dropped_sink
    )

    ruled_out = ruled_out_requirements(primary_tag, evidence)
    for req in reqs.get("required", []):
        accepted = _REQUIREMENT_EQUIVALENTS.get(req, frozenset({req}))
        if any(t.get("component", "") in accepted for t in tabs):
            continue
        if req in ruled_out:
            _record_evidence_skip(primary_tag, req, ruled_out[req], dropped_sink)
        elif not _backfill_required_component(
            tabs, primary_tag, accepted, extraction, enrichment, synthesis, video_meta
        ):
            logger.warning(
                "Domain '%s' requires '%s' but it's missing from assembled tabs",
                primary_tag,
                req,
            )

    _cap_component_counts(tabs, reqs.get("max", {}), primary_tag, dropped_sink)
    _check_tab_labels(tabs)


def _check_tab_labels(tabs: list[dict]) -> None:
    """Warn on empty goals/labels; rename placeholder "Untitled Chapter" labels."""
    for tab in tabs:
        if not tab.get("goal"):
            logger.warning("Tab '%s' has empty goal", tab.get("id", "?"))
        if not tab.get("label"):
            logger.warning("Tab '%s' has empty label", tab.get("id", "?"))
        label = tab.get("label", "")
        if "<Untitled" in label or "Untitled Chapter" in label:
            tab["label"] = "Introduction"


# ─────────────────────────────────────────────────────
# Post-Processing
# ─────────────────────────────────────────────────────

_UNTITLED_RE = re.compile(r"<?\s*Untitled\s+Chapter\s+(\d+)\s*>?", re.IGNORECASE)

# Maps component → required list key. If the list is empty after assembly, drop the tab.
# Live (v2 registry) components only — legacy v1 names were dropped alongside
# their ASSEMBLER_REGISTRY entries (cached rows fall through to display_section).
_COMPONENT_REQUIRED_LISTS: dict[str, str] = {
    # Kept across the overhaul
    "moment_track": "items",
    "spot_explorer": "spots",
    "info_grid": "items",
    "checklist": "items",
    "step_player": "steps",
    "flash_deck": "cards",
    "budget": "breakdown",
    "comparison": "comparisons",
    # Video-to-action overhaul: new components
    "code_playground": "snippets",
    "workout_room": "exercises",
    "quiz_arena": "questions",
    "lyrics_karaoke": "sections",
    "video_filmstrip": "frames",
    "concept_canvas": "concepts",
    "connect_canvas": "pairs",
    "step_flow_canvas": "steps",
    "comparison_radar": "comparisons",
    "packing_mission": "items",
    # interactive-overhaul-v2 P5b — news signature component
    "claims_tracker": "claims",
    # interactive-overhaul-v2 P5c/d — gaming + sport signature components
    "tier_list": "items",
    "formation_diagram": "positions",
    # Secondary-tier (attachment-only) — interactive-overhaul-v2 P2.
    # tip_callout/summary_header carry scalar text (no list), so they are not
    # listed here; they're validated by their assemblers returning None on empty.
    "stat_banner": "stats",
    "diagram_card": "nodes",
    "frame_strip": "frames",
    "quick_quiz": "questions",
}


def _validate_assembled_props(component: str, props: dict) -> bool:
    """Validate assembled props — return False if the tab should be dropped.

    Only enforces the required list (must be non-empty). Per-component density
    minimums live inside the individual assemblers (e.g. spot_explorer's
    `_MIN_SPOTS`, comparison's real-pair check) so we can keep their thresholds
    co-located with their normalization logic.
    """
    required_key = _COMPONENT_REQUIRED_LISTS.get(component)
    if required_key is None:
        return True
    data_list = props.get(required_key)
    if not isinstance(data_list, list) or len(data_list) == 0:
        logger.warning(
            "Validation: component=%r has empty required list %r — dropping tab",
            component,
            required_key,
        )
        return False
    return True


# Component → name of its user-facing list prop. Stays code-side (it names
# assembler prop keys, not tunables); every component in domains.json
# `assemblerItemCaps` MUST appear here or its cap silently never enforces
# (pinned by tests/test_assembly_caps_config.py). v1-legacy names (quiz,
# scenario, exercise_tracker, code_explorer, gallery, lyrics_player) removed
# 2026-07-12 — they are no longer registry components.
_COUNT_KEYS: dict[str, str] = {
    "spot_explorer": "spots",
    "checklist": "items",
    "step_player": "steps",
    "flash_deck": "cards",
    "info_grid": "items",
    "moment_track": "items",
    "comparison": "comparisons",
    "claims_tracker": "claims",
    "tier_list": "items",
    "formation_diagram": "positions",
}

# Per-component user-facing item caps. Long videos otherwise produce
# unscannable lists (29-row "comparisons", 55-item timelines). Single-sourced
# from domains.json `assemblerItemCaps` (project-score-9 4.5d) — deliberately
# INDEPENDENT of the advisory `densityGates` the planner sees; this is the
# code backstop that holds regardless of prompt drift.
_TAB_ITEM_CAPS: dict[str, int] = assembler_item_caps()


# moment_track is a timeline: its cap scales with duration and the kept items
# are sampled evenly across the video, not head-sliced (which truncated long
# videos to their first ~40 min). ~1 moment per 7 min, clamped to [20, 60].
_MOMENT_CAP_MIN = 20
_MOMENT_CAP_MAX = 60
_MOMENT_MINUTES_PER_ITEM = 7


def _evenly_sample(items: list, cap: int) -> list:
    """Return ``cap`` items at evenly spaced indices, keeping first and last.

    Used for timelines so a capped list still spans the whole video instead of
    only its head. Rounding collisions may yield slightly fewer than ``cap``.
    """
    n = len(items)
    if cap <= 0:
        return []
    if n <= cap:
        return items
    if cap == 1:
        return [items[0]]
    step = (n - 1) / (cap - 1)
    indices = sorted({round(i * step) for i in range(cap)})
    return [items[i] for i in indices]


def _cap_tab_items(component: str, props: dict, video_duration: float | None = None) -> None:
    """Truncate the user-facing list on a tab if it exceeds the cap."""
    cap = _TAB_ITEM_CAPS.get(component)
    if cap is None:
        return
    list_key = _COUNT_KEYS.get(component)
    if not list_key:
        return
    items = props.get(list_key)
    if not isinstance(items, list):
        return

    # Timeline: scale the cap with duration and sample evenly (span the video).
    if component == "moment_track":
        if video_duration and video_duration > 0:
            cap = min(
                _MOMENT_CAP_MAX,
                max(
                    _MOMENT_CAP_MIN,
                    round((video_duration / 60) / _MOMENT_MINUTES_PER_ITEM),
                ),
            )
        if len(items) > cap:
            logger.info(
                "Assembly: capping %s.%s from %d → %d (even-sampled)",
                component,
                list_key,
                len(items),
                cap,
            )
            props[list_key] = _evenly_sample(items, cap)
        return

    if len(items) > cap:
        # spot_explorer groups spots by day via sections[].spotIndices. A plain
        # head-truncation would silently amputate the later days (a 10-day
        # itinerary rendering only days 1-4) AND leave sections pointing at
        # indices past the cap (empty days in the UI). Even-sample across the
        # whole list and remap the section indices instead.
        if list_key == "spots" and isinstance(props.get("sections"), list):
            logger.info(
                "Assembly: capping %s.%s from %d → %d (even-sampled, sections remapped)",
                component,
                list_key,
                len(items),
                cap,
            )
            _cap_spots_with_sections(props, items, cap)
            return
        logger.info(
            "Assembly: capping %s.%s from %d → %d",
            component,
            list_key,
            len(items),
            cap,
        )
        props[list_key] = items[:cap]


def _cap_spots_with_sections(props: dict, spots: list, cap: int) -> None:
    """Cap spots while guaranteeing every day/section keeps at least one spot.

    Global even-sampling can empty a small section entirely (a "Day 4"
    sub-tab silently vanishing from an itinerary), so quotas are allocated
    per section — largest remainder with a floor of 1 — and sampled evenly
    WITHIN each section. Sections' spotIndices are remapped to the rebuilt
    spots list; only when the section count itself exceeds the cap are
    trailing sections dropped.
    """
    valid: list[dict] = []
    for section in props["sections"]:
        if not isinstance(section, dict):
            continue
        indices = [
            i for i in section.get("spotIndices", []) if isinstance(i, int) and 0 <= i < len(spots)
        ]
        if indices:
            valid.append({**section, "spotIndices": indices})

    if not valid:
        props["spots"] = _evenly_sample(spots, cap)
        props["sections"] = []
        return

    if len(valid) >= cap:
        valid = valid[:cap]
        quotas = [1] * len(valid)
    else:
        total = sum(len(s["spotIndices"]) for s in valid)
        budget = min(cap, total)
        quotas = [max(1, (budget * len(s["spotIndices"])) // total) for s in valid]
        overshoot = sum(quotas) - budget
        while overshoot > 0:
            largest = max(range(len(quotas)), key=lambda j: quotas[j])
            if quotas[largest] <= 1:
                break
            quotas[largest] -= 1
            overshoot -= 1
        shortfall = budget - sum(quotas)
        if shortfall > 0:
            by_remainder = sorted(
                range(len(valid)),
                key=lambda j: -((budget * len(valid[j]["spotIndices"])) % total),
            )
            for j in by_remainder:
                if shortfall == 0:
                    break
                if quotas[j] < len(valid[j]["spotIndices"]):
                    quotas[j] += 1
                    shortfall -= 1

    kept_old: set[int] = set()
    for section, quota in zip(valid, quotas):
        kept_old.update(_evenly_sample(section["spotIndices"], quota))

    # Spots not referenced by any section fill whatever room remains.
    if len(kept_old) < cap:
        referenced = {i for s in valid for i in s["spotIndices"]}
        unreferenced = [i for i in range(len(spots)) if i not in referenced]
        kept_old.update(_evenly_sample(unreferenced, cap - len(kept_old)))

    kept_sorted = sorted(kept_old)[:cap]
    old_to_new = {old: new for new, old in enumerate(kept_sorted)}
    props["spots"] = [spots[i] for i in kept_sorted]

    rebuilt: list[dict] = []
    for section in valid:
        mapped = [old_to_new[i] for i in section["spotIndices"] if i in old_to_new]
        if mapped:
            rebuilt.append({**section, "spotIndices": mapped})
    dropped = len(props["sections"]) - len(rebuilt)
    if dropped:
        logger.info("Assembly: %d spot section(s) dropped by cap", dropped)
    props["sections"] = rebuilt


def _post_process_tabs(tabs: list[dict], video_duration: float | None = None) -> None:
    """Apply post-processing rules to assembled tabs in-place."""
    for i, tab in enumerate(tabs):
        label = tab.get("label", "")
        props = tab.get("props", {})
        emoji = tab.get("emoji", "")

        component = tab.get("component", "")
        if isinstance(props, dict) and component:
            _cap_tab_items(component, props, video_duration)

        if emoji and label.startswith(emoji):
            label = label[len(emoji) :].lstrip()
            tab["label"] = label

        match = _UNTITLED_RE.search(label)
        if match:
            label = "Introduction" if i == 0 else f"Part {match.group(1)}"
            tab["label"] = label

        if isinstance(props, dict):
            for key in ("steps", "spots", "entries", "items"):
                items_list = props.get(key)
                if isinstance(items_list, list):
                    for idx_in_list, item in enumerate(items_list):
                        if isinstance(item, dict):
                            for field in ("instruction", "label", "title", "name"):
                                val = item.get(field)
                                if isinstance(val, str):
                                    m = _UNTITLED_RE.search(val)
                                    if m:
                                        item[field] = (
                                            "Introduction"
                                            if idx_in_list == 0
                                            else f"Part {m.group(1)}"
                                        )


def _link_tabs(tabs: list[dict], primary_tag: str) -> None:
    """Set every tab's ``crossTabLinks``; each target is linked from one tab only."""
    all_tab_ids = {t["id"] for t in tabs}
    globally_linked: set[str] = set()
    for tab in tabs:
        links = resolve_cross_tab_links(
            tab_id=tab["id"],
            all_tab_ids=all_tab_ids,
            component=tab.get("component"),
            all_tabs=tabs,
            primary_tag=primary_tag,
        )
        deduped = []
        for link in links:
            if link["targetTab"] not in globally_linked:
                deduped.append(link)
                globally_linked.add(link["targetTab"])
        tab["crossTabLinks"] = deduped


# ─────────────────────────────────────────────────────
# Empty Data Detection + Cross-Domain Fallback
# ─────────────────────────────────────────────────────

# Mapping of equivalent fields across domains for fallback resolution.
# When primary domain field is empty, try these alternatives in other domains.
_FIELD_EQUIVALENTS: dict[str, list[str]] = {
    "concepts": ["keyMoments", "keyPoints", "takeaways"],
    "keyPoints": ["takeaways", "keyMoments", "quotes", "savingTips"],
    "timestamps": ["keyMoments"],
    "takeaways": ["keyMoments", "takeaways", "keyPoints", "savingTips"],
    "keyMoments": ["keyPoints", "concepts", "takeaways"],
    "tips": ["savingTips", "transportationTips", "accommodationTips", "takeaways"],
    "packingList": ["savingTips", "tips", "takeaways"],
    "itinerary": ["keyMoments", "spots", "costs"],
    "restaurants": ["spots", "keyMoments"],
    "phrases": ["keyPoints", "vocabulary"],
    "vocabulary": ["concepts", "phrases"],
    "drills": ["steps", "exercises"],
    "keyFacts": ["keyPoints", "concepts", "takeaways"],
    "hotels": ["accommodations", "spots", "itinerary"],
    "specs": ["keyFacts", "keyPoints", "items"],
    "experiments": ["steps"],
    "rules": ["concepts", "keyPoints", "tips"],
}


def _cross_domain_fallback(
    data_source: str,
    extraction: dict | None,
    enrichment: dict | None = None,
) -> Any:
    """Try other extraction domains when primary domain field is empty.

    Given "learning.concepts" with empty data, scans narrative, tech, etc.
    for the same field name or equivalent fields.
    """
    if not extraction:
        return None
    parts = data_source.split(".")
    if len(parts) != 2:
        return None

    primary_domain, field = parts

    # Try exact field name in other domains first
    for domain, domain_data in extraction.items():
        if domain == primary_domain or not isinstance(domain_data, dict):
            continue
        candidate = domain_data.get(field)
        if not is_empty_data(candidate):
            logger.info(
                "Cross-domain fallback: %s.%s (empty) → %s.%s (%d items)",
                primary_domain,
                field,
                domain,
                field,
                len(candidate) if isinstance(candidate, (list, dict)) else 1,
            )
            return candidate

    # Try equivalent field names in other domains
    equivalents = _FIELD_EQUIVALENTS.get(field, [])
    for domain, domain_data in extraction.items():
        if domain == primary_domain or not isinstance(domain_data, dict):
            continue
        for equiv_field in equivalents:
            candidate = domain_data.get(equiv_field)
            if not is_empty_data(candidate):
                logger.info(
                    "Cross-domain fallback: %s.%s (empty) → %s.%s (%d items)",
                    primary_domain,
                    field,
                    domain,
                    equiv_field,
                    len(candidate) if isinstance(candidate, (list, dict)) else 1,
                )
                return candidate

    return None


# Components whose assemblers normalize arbitrary dict/str lists into key/value
# items — safe targets for the generic longest-list fallback below.
_GENERIC_LIST_FALLBACK_COMPONENTS = frozenset({"info_grid", "checklist"})


def _in_domain_sibling_fallback(
    data_source: str,
    component: str,
    extraction: dict | None,
    enrichment: dict | None = None,
) -> Any:
    """Recover an empty planned field from a populated SIBLING field in the SAME domain.

    The planner picks a tab's dataSource before extraction runs, so it may bet on a
    schema field this video left empty (e.g. ``tech.patterns`` when only
    ``tech.snippets`` got populated). Both back the same component per domains.json,
    so the tab can render from the sibling instead of being dropped. This is the
    same-domain counterpart to ``_cross_domain_fallback`` (which only scans OTHER
    domains for the same field name).
    """
    if not extraction or "." not in data_source:
        return None

    domain = data_source.split(".", 1)[0]

    # Registry-driven: siblings backing the same component, in priority order.
    for candidate in sibling_datasources(domain, data_source):
        value = resolve_data_source(candidate, extraction, enrichment)
        if not is_empty_data(value):
            logger.info(
                "In-domain sibling fallback: %s (empty) → %s (%d items)",
                data_source,
                candidate,
                len(value) if isinstance(value, (list, dict)) else 1,
            )
            return value

    # Last resort: the planner invented a field not registered as a defaultTab
    # (e.g. ``tech.topics``). Try the component's required list key against other
    # populated list fields in the same domain.
    required_key = _COMPONENT_REQUIRED_LISTS.get(component)
    domain_data = extraction.get(domain)
    if required_key and isinstance(domain_data, dict):
        candidate = domain_data.get(required_key)
        if not is_empty_data(candidate):
            logger.info(
                "In-domain sibling fallback: %s (empty) → %s.%s (%d items)",
                data_source,
                domain,
                required_key,
                len(candidate) if isinstance(candidate, (list, dict)) else 1,
            )
            return candidate

    # Generic last resort, gated to key/value-style components whose assembler
    # normalizes arbitrary list shapes (worst case it returns None and the tab
    # drops exactly as it would have): use the longest populated list field in
    # the planned domain. Positional components (spot_explorer etc.) are
    # deliberately excluded — a random list would corrupt their semantics.
    if component in _GENERIC_LIST_FALLBACK_COMPONENTS and isinstance(domain_data, dict):
        best_field: str | None = None
        best_value: list | None = None
        for field_name, value in domain_data.items():
            if not isinstance(value, list) or is_empty_data(value):
                continue
            if not all(isinstance(v, (dict, str)) for v in value):
                continue
            # Timeline-shaped lists (highlights, moments, chapters — items that
            # carry a timestamp) are a jump index, not reference facts; keyed
            # off "step"/"claim"/"topic" they would masquerade as a grid.
            if any(isinstance(v, dict) and _item_timestamp(v) is not None for v in value):
                continue
            if best_value is None or len(value) > len(best_value):
                best_field = field_name
                best_value = value
        if best_value is not None:
            logger.info(
                "Generic in-domain fallback: %s (empty) → %s.%s (%d items)",
                data_source,
                domain,
                best_field,
                len(best_value),
            )
            return best_value

    return None


# ─────────────────────────────────────────────────────
# Overview Ordering Guarantee
# ─────────────────────────────────────────────────────


def _ensure_overview_first(
    tabs: list[dict],
    synthesis: dict | None,
    video_meta: dict | None,
    extraction: dict | None,
    enrichment: dict | None,
    primary_tag: str,
) -> None:
    """Guarantee an overview tab exists and sits at index 0.

    Finds any tab flagged as overview (id or component) and moves it to the
    front; if none exists, synthesizes one from synthesis/video_meta/extraction
    via the overview assembler and prepends it. Mutates `tabs` in place.
    """
    from .assemblers_learning import assemble_overview

    overview_idx = next(
        (
            i
            for i, t in enumerate(tabs)
            if t.get("id") == "overview" or t.get("component") == "overview"
        ),
        -1,
    )

    if overview_idx >= 0:
        if overview_idx != 0:
            tabs.insert(0, tabs.pop(overview_idx))
        # Normalize so the frontend always routes to OverviewInteractive.
        tabs[0]["component"] = "overview"
        return

    tab_stub = {
        "id": "overview",
        "label": "Overview",
        "emoji": "📋",
        "_primary_tag": primary_tag,
        "_synthesis": synthesis,
        "_video_meta": video_meta,
    }
    props = assemble_overview(tab_stub, None, extraction or {}, enrichment)
    if props is None:
        return
    tabs.insert(
        0,
        {
            "id": "overview",
            "label": "Overview",
            "emoji": "📋",
            "component": "overview",
            "props": props,
            "goal": "Quick summary of what this video covers",
            "crossTabLinks": [],
        },
    )


_QUIZ_COMPONENT = "quiz_arena"


def _move_quiz_tabs_last(tabs: list[dict]) -> None:
    """Move quiz_arena tabs to the end, in order, when ``quizPolicy.position`` is "last".

    The plan already puts its quiz last, but assembly appends after the planned
    tabs (backfilled requirements, the filmstrip, minimum-3 fallbacks), so
    the order is enforced once every tab is in. Stable; mutates ``tabs``.
    """
    if quiz_policy()["position"] != "last":
        return
    quiz_tabs = [t for t in tabs if t.get("component") == _QUIZ_COMPONENT]
    if quiz_tabs:
        tabs[:] = [t for t in tabs if t.get("component") != _QUIZ_COMPONENT] + quiz_tabs


def _annotate_overview_item_count(tabs: list[dict]) -> None:
    """Inject the content-tab count into the overview's data + stats pills.

    Must run AFTER all tab additions (cross-tab link resolution, gallery
    auto-append, fallback candidates) so `len(tabs) - 1` reflects what the
    user will actually see. Surfaces as both `data.itemCount` and an "Items"
    stat chip on the hero.
    """
    if not tabs or tabs[0].get("component") != "overview":
        return
    item_count = max(0, len(tabs) - 1)
    if item_count == 0:
        return
    props = tabs[0].get("props")
    if not isinstance(props, dict):
        return
    data = props.setdefault("data", {})
    if not isinstance(data, dict):
        return
    data["itemCount"] = item_count

    existing_stats = data.get("stats")
    stats: list[Any] = existing_stats if isinstance(existing_stats, list) else []
    if not any(isinstance(s, dict) and s.get("label") == "Items" for s in stats):
        stats.append({"label": "Items", "value": str(item_count), "emoji": "📚"})
        data["stats"] = stats


# ─────────────────────────────────────────────────────
# Fallback Tab Candidates
# ─────────────────────────────────────────────────────


def build_fallback_candidates(
    synthesis: dict | None,
    video_meta: dict | None,
    existing_components: set[str],
    existing_ids: set[str],
) -> list[dict]:
    """Build fallback tab candidates from synthesis and video metadata.

    Priority: overview > info_grid > moment_track.
    Skips candidates that duplicate existing components or IDs.
    """
    candidates: list[dict] = []
    synthesis = synthesis or {}
    video_meta = video_meta or {}

    # Fallback 1: overview from synthesis
    if "overview" not in existing_components and "overview" not in existing_ids:
        master_summary = synthesis.get("masterSummary", "")
        key_takeaways = synthesis.get("keyTakeaways", [])
        if master_summary or key_takeaways:
            candidates.append(
                {
                    "id": "overview",
                    "label": "Overview",
                    "emoji": "📋",
                    "component": "overview",
                    "props": {
                        "summary": master_summary,
                        "keyTakeaways": key_takeaways,
                        "tldr": synthesis.get("tldr", ""),
                    },
                    "goal": "Quick summary of what this video covers",
                    "crossTabLinks": [],
                }
            )

    # Fallback 2: info_grid from synthesis keyTakeaways
    if "info_grid" not in existing_components and "key_info" not in existing_ids:
        key_takeaways = synthesis.get("keyTakeaways", [])
        if len(key_takeaways) >= 2:
            items = [{"key": f"Takeaway {i + 1}", "value": t} for i, t in enumerate(key_takeaways)]
            candidates.append(
                {
                    "id": "key_info",
                    "label": "Key Info",
                    "emoji": "📊",
                    "component": "info_grid",
                    "props": {"items": items},
                    "goal": "Key takeaways from this video at a glance",
                    "crossTabLinks": [],
                }
            )

    # Fallback 3: moment_track from video chapters
    if "moment_track" not in existing_components and "key_moments" not in existing_ids:
        chapters = video_meta.get("chapters", [])
        if isinstance(chapters, list) and len(chapters) >= 2:
            chapter_dicts = [ch for ch in chapters if isinstance(ch, dict)]
            if len(chapter_dicts) >= 2:
                video_duration = video_meta.get("duration")
                try:
                    video_duration = int(video_duration) if video_duration is not None else None
                except (ValueError, TypeError):
                    video_duration = None
                items = _chapters_to_moments(chapter_dicts, video_duration)
                if len(items) >= 2:
                    candidates.append(
                        {
                            "id": "key_moments",
                            "label": "Key Moments",
                            "emoji": "🎬",
                            "component": "moment_track",
                            "props": {"items": items},
                            "goal": "Jump to the chapters of this video",
                            "crossTabLinks": [],
                        }
                    )

    return candidates


# ─────────────────────────────────────────────────────
# Main Assembly Orchestrator
# ─────────────────────────────────────────────────────


def assemble_response(
    triage: dict,
    extraction: dict | None,
    enrichment: dict | None,
    synthesis: dict | None,
    video_meta: dict | None = None,
    description_analysis: dict | None = None,
    frames: list[dict] | None = None,
    gallery_frames: list[dict] | None = None,
    all_frames: list[dict] | None = None,
    frame_descriptions: list[dict] | None = None,
) -> dict:
    """Assemble the final VIEResponse v2 from pipeline outputs.

    Returns:
        dict: { meta, tabs: TabEntry[] }.
    """
    video_meta = video_meta or {}

    # Inject normalized filmstrip frames into extraction so a tab planned as
    # `{ "component": "video_filmstrip", "dataSource": "frames" }` resolves
    # cleanly. Reuses the assembler's own normalizer for consistency. Frame
    # data is captured by the frames phase but otherwise bypasses extraction —
    # this is the bridge that lets the LLM plan a filmstrip tab. Work on a
    # shallow copy so the synthetic `frames` key never leaks into the caller's
    # extraction record (which is persisted/cached separately).
    if extraction is not None:
        extraction = dict(extraction)
    if extraction is not None and "frames" not in extraction:
        source = gallery_frames if gallery_frames else (frames or [])
        if frame_descriptions:
            enriched_source = []
            for f in source:
                if not isinstance(f, dict):
                    continue
                desc = find_description_for_frame(f, frame_descriptions)
                merged = dict(f)
                if desc:
                    merged.setdefault("caption", desc.get("content"))
                    merged.setdefault("ocr", desc.get("text_visible"))
                    merged.setdefault("sceneType", desc.get("scene_type"))
                enriched_source.append(merged)
            source = enriched_source
        normalized = [
            n for n in (_normalize_filmstrip_frame(item) for item in source) if n is not None
        ]
        if normalized:
            extraction["frames"] = normalized

    meta: dict[str, Any] = {
        "contentTags": triage.get("contentTags", ["learning"]),
        "modifiers": triage.get("modifiers", []),
        "primaryTag": triage.get("primaryTag", "learning"),
        "userGoal": triage.get("userGoal", ""),
    }

    if synthesis:
        meta["tldr"] = synthesis.get("tldr", "")
        meta["seoDescription"] = synthesis.get("seoDescription", "")
        meta["masterSummary"] = synthesis.get("masterSummary", "")
        meta["keyTakeaways"] = synthesis.get("keyTakeaways", [])

    if description_analysis:
        meta["descriptionAnalysis"] = description_analysis

    primary_tag = meta["primaryTag"]
    raw_tabs = triage.get("tabs", [])
    # The plan's Appendix-C booleans (D15): a required component they rule out
    # is neither backfilled nor reached by demotion.
    evidence = triage.get("evidence") if isinstance(triage.get("evidence"), dict) else None
    blocked_rungs = demotion_blocked({primary_tag, *meta["contentTags"]}, evidence)

    assembled_tabs: list[dict] = []
    # Response-wide reuse ledger: s3 key -> timestamp of first attachment.
    frame_used: dict[str, float] = {}
    # Honest drop accounting: every planned tab that dies records why.
    dropped_tabs: list[dict] = []

    for raw_tab in raw_tabs:
        if not isinstance(raw_tab, dict):
            continue

        tab_id = raw_tab.get("id", "")
        data_source = raw_tab.get("dataSource", "")
        component = raw_tab.get("component") or infer_component(tab_id)

        # Guarantee id="overview" tabs always render the OverviewInteractive —
        # triage sometimes mis-tags them as display_section, which would skip
        # the interactive renderer on the frontend.
        if tab_id == "overview":
            component = "overview"

        data = resolve_data_source(data_source, extraction, enrichment)
        data_resolved = data is not None

        # Treat empty collections as missing — lets fallback logic try other domains
        if is_empty_data(data):
            data = None
            data_resolved = False

        # Cross-domain fallback: when primary domain field is empty, try other domains
        if data is None and "." in data_source:
            data = _cross_domain_fallback(data_source, extraction, enrichment)
            if data is not None:
                data_resolved = True

        # In-domain sibling fallback: when the planner bet on a field this video left
        # empty, swap in a populated sibling backing the same component (registry-driven).
        if data is None and "." in data_source:
            data = _in_domain_sibling_fallback(data_source, component, extraction, enrichment)
            if data is not None:
                data_resolved = True

        if data is None and extraction:
            if tab_id in ("exercises", "timer"):
                data = extraction.get("fitness")
            elif tab_id == "pros_cons":
                review = extraction.get("review", {})
                if isinstance(review, dict):
                    data = {
                        "pros": review.get("pros", []),
                        "cons": review.get("cons", []),
                        "comparisons": review.get("comparisons", []),
                    }
            elif component == "moment_track":
                data = (extraction.get("learning") or {}).get("timestamps")

        # YouTube chapters fall through to fill *empty* moment_track data only.
        # Prior behavior (pre-MomentTrack) was the opposite: chapters always
        # overrode LLM timestamps. We flipped this with the unified MomentTrack
        # because LLM-extracted moments now carry mood / description / highlight
        # spans — strictly richer than the {start_time, title} pairs YouTube
        # exposes. When extraction returns nothing, chapters are still the best
        # navigation aid we have, so we keep the fallback.
        if component == "moment_track" and (
            data is None or (isinstance(data, list) and len(data) == 0)
        ):
            yt_chapters = (video_meta or {}).get("chapters", [])
            if isinstance(yt_chapters, list) and len(yt_chapters) > 0:
                chapter_dicts = [ch for ch in yt_chapters if isinstance(ch, dict)]
                if chapter_dicts:
                    video_duration = video_meta.get("duration") if video_meta else None
                    try:
                        video_duration = int(video_duration) if video_duration is not None else None
                    except (ValueError, TypeError):
                        video_duration = None
                    data = _chapters_to_moments(chapter_dicts, video_duration)
                    data_resolved = True

        # Components that build from synthesis/meta, not extraction data
        _SELF_SUFFICIENT = {"overview", "budget"}
        if not data_resolved and data is None and component not in _SELF_SUFFICIENT:
            available = list(extraction.keys()) if extraction else []
            logger.warning(
                "No data for tab id=%r, dataSource=%r — not in extraction/enrichment. "
                "Available extraction keys: %s",
                tab_id,
                data_source,
                available,
            )

        assembler = ASSEMBLER_REGISTRY.get(component, assemble_display_section)
        tab_with_hints = {
            **raw_tab,
            "_primary_tag": primary_tag,
            "_synthesis": synthesis,
            "_video_meta": video_meta,
        }

        drop_reason: str | None = None
        drop_detail: str | None = None
        try:
            props = assembler(tab_with_hints, data, extraction, enrichment)
        except Exception as e:
            logger.warning(
                "TAB DROPPED: id=%r, component=%r, dataSource=%r — assembler raised %s: %s",
                tab_id,
                component,
                data_source,
                type(e).__name__,
                e,
            )
            props = None
            drop_reason = "assembler_raised"
            drop_detail = f"{type(e).__name__}: {e}"

        # Post-extraction promotion (the planner couldn't see the item counts /
        # connection graph that gate the richer components) then density caps.
        # Both run before validation so a promoted/trimmed tab is re-checked.
        if props is not None:
            component, props = promote_component(
                component,
                props,
                data,
                extraction,
                primary_tag,
            )
            props = enforce_density(component, props)

        if props is not None and not _validate_assembled_props(component, props):
            logger.warning(
                "TAB DROPPED: id=%r, component=%r — failed validation",
                tab_id,
                component,
            )
            props = None
            drop_reason = "validation_empty_list"

        # Degrade, never drop: a rich assembler failing on NON-EMPTY data
        # (shape mismatch, min-item gate, exception) walks the demote ladder
        # to a simpler component instead of losing the tab. Runs before frame
        # injection so degraded props get thumbnails like any other tab.
        # Policy drops (domain_forbidden/max_cap) are handled later and are
        # never demoted.
        degraded_from: str | None = None
        if props is None and not is_empty_data(data):
            demoted = demote_component(
                component, tab_with_hints, data, extraction, enrichment, blocked=blocked_rungs
            )
            if demoted is not None:
                degraded_from = component
                component, props = demoted
                drop_reason = None
                drop_detail = None

        if props is not None and frames:
            tab_used: set[str] = set()
            for key in (
                "items",
                "spots",
                "steps",
                "images",
                "snippets",
                "comparisons",
                "exercises",
            ):
                if key in props and isinstance(props[key], list):
                    inject_frame_thumbnails(
                        props[key],
                        frames,
                        all_frames=all_frames,
                        frame_descriptions=frame_descriptions,
                        used=frame_used,
                        tab_used=tab_used,
                    )

        if props is None:
            data_summary = (
                (
                    f"list[{len(data)}]"
                    if isinstance(data, list)
                    else f"dict(keys={list(data.keys())})"
                    if isinstance(data, dict)
                    else repr(type(data).__name__)
                )
                if data is not None
                else "None"
            )
            if drop_reason is None:
                drop_reason = "assembler_returned_none"
                drop_detail = f"data was {data_summary}"
                if data_resolved or data is not None:
                    logger.warning(
                        "TAB DROPPED: id=%r, component=%r, dataSource=%r — "
                        "assembler returned None (data was %s)",
                        tab_id,
                        component,
                        data_source,
                        data_summary,
                    )
            dropped_tabs.append(
                {
                    "id": tab_id,
                    "component": component,
                    "dataSource": data_source,
                    "reason": drop_reason,
                    **({"detail": drop_detail} if drop_detail else {}),
                }
            )
            continue

        assembled_tabs.append(
            {
                "id": tab_id,
                "label": raw_tab.get("label", tab_id),
                "emoji": raw_tab.get("emoji", ""),
                "component": component,
                "props": props,
                "goal": raw_tab.get("goal", ""),
                "crossTabLinks": [],
                **({"degradedFrom": degraded_from} if degraded_from else {}),
            }
        )

    # Hallucinated timestamps (beyond the video's end) can never seek or
    # match a frame — drop them before the backfill and post-assembly
    # extraction passes spend effort on them.
    if _drop_impossible_timestamps(assembled_tabs, (video_meta or {}).get("duration")):
        _drop_emptied_moment_tabs(assembled_tabs, dropped_tabs)

    # Relaxed second-pass thumbnail fill for the moment grid — runs after the
    # strict per-tab injection so exclusivity had first pick.
    _backfill_moment_thumbnails(assembled_tabs, frames or [], all_frames, frame_descriptions)

    # Guarantee overview is present and first — must run before cross-tab link
    # resolution so the overview participates in link rules (overview → X).
    _ensure_overview_first(
        assembled_tabs,
        synthesis,
        video_meta,
        extraction,
        enrichment,
        primary_tag,
    )

    # Validate domain requirements (may backfill a missing required component from
    # real extraction data). Runs before cross-tab resolution so a backfilled tab
    # participates in link rules.
    _validate_domain_requirements(
        assembled_tabs,
        primary_tag,
        extraction,
        enrichment,
        synthesis,
        video_meta,
        dropped_sink=dropped_tabs,
        content_format=triage.get("contentFormat"),
        evidence=evidence,
    )

    # Conditional filmstrip auto-append. The standalone gallery component was
    # retired in the video-to-action overhaul; this surfaces the same frames as
    # a `video_filmstrip` scrubber instead.
    _gallery_source = gallery_frames if gallery_frames else frames
    if _gallery_source and len(_gallery_source) > 0:
        filmstrip_frames: list[dict] = []
        non_generic_count = 0
        for f in sorted(_gallery_source, key=lambda x: x.get("timestamp", 0)):
            ts = f.get("timestamp", 0)
            mins = int(ts) // 60
            secs = int(ts) % 60
            caption = f"Moment at {mins}:{secs:02d}"
            if frame_descriptions:
                desc = find_description_for_frame(f, frame_descriptions)
                if desc and desc.get("content"):
                    caption = desc["content"]
            if caption.startswith("Moment at") and f.get("ocr_text"):
                caption = f.get("ocr_text", caption)

            normalized = _normalize_filmstrip_frame(
                {
                    "thumbnailUrl": f.get("s3_url", ""),
                    "caption": caption,
                    "timestamp": ts,
                    **({"ocr": f["ocr_text"]} if f.get("ocr_text") else {}),
                }
            )
            if normalized is not None:
                if f.get("s3_key"):
                    normalized["s3Key"] = f["s3_key"]
                filmstrip_frames.append(normalized)
                # Count non-generic captions over the SURVIVING frames only, so
                # the ≥70% gate below divides by the same population it counts.
                if not caption.startswith("Moment at"):
                    non_generic_count += 1

        # The filmstrip is the lowest-signal tab on the rail — auto-append only
        # when captions are largely non-generic (≥70%) and we have enough frames
        # to feel like a real strip, not a 3-photo afterthought. Also skip
        # content types where it never adds value: podcast/narrative/music are
        # mostly talking-head footage; lectures with one slide drown in noise.
        has_enough_frames = len(filmstrip_frames) >= 10
        has_good_captions = non_generic_count >= len(filmstrip_frames) * 0.7
        _NO_GALLERY_DOMAINS = {"narrative", "music"}
        _domain_blocked = primary_tag in _NO_GALLERY_DOMAINS
        # A planner-designed filmstrip tab already shows these frames — a
        # second auto-appended gallery is pure duplication.
        _has_filmstrip_tab = any(t.get("component") == "video_filmstrip" for t in assembled_tabs)
        # A moment_track with a rich thumbnail gallery IS the visual-moments
        # surface now (grid view) — an extra filmstrip duplicates it. Keep the
        # filmstrip only when it shows substantially unique material (moment
        # thumbs cover < 50% of the filmstrip-eligible frames).
        _moment_thumb_count = max(
            (
                sum(
                    1
                    for it in t.get("props", {}).get("items", [])
                    if isinstance(it, dict) and it.get("thumbnailUrl")
                )
                for t in assembled_tabs
                if t.get("component") == "moment_track"
            ),
            default=0,
        )
        _rich_moment_gallery = _moment_thumb_count >= 8 and _moment_thumb_count >= 0.5 * max(
            len(filmstrip_frames), 1
        )
        if (
            filmstrip_frames
            and has_enough_frames
            and has_good_captions
            and not _domain_blocked
            and not _has_filmstrip_tab
            and not _rich_moment_gallery
        ):
            assembled_tabs.append(
                {
                    "id": "frames-gallery",
                    "label": "Visual Moments",
                    "emoji": "\U0001f5bc\ufe0f",
                    "component": "video_filmstrip",
                    "props": {"frames": filmstrip_frames},
                    "goal": "Browse key visual moments from the video",
                    "crossTabLinks": [],
                }
            )

    _post_process_tabs(assembled_tabs, (video_meta or {}).get("duration"))
    # After post-processing: a link's text is its target's FINAL label (emoji
    # prefix stripped, "Untitled Chapter N" renamed), never the raw plan label.
    _link_tabs(assembled_tabs, primary_tag)

    # Secondary-tier attachments (interactive-overhaul-v2 P2): enrich sparse
    # tabs / break up dense ones. Authoritative + data-driven; runs after tabs
    # are finalized so item counts reflect the user-visible list. The overview
    # tab is its own hero — never decorate it.
    attach_frames = extraction.get("frames") if isinstance(extraction, dict) else None
    # frame_strip/quick_quiz/tip_callout are built from response-global data —
    # budget ONE of each per response so tabs don't repeat identical payloads.
    # A filmstrip tab (planned or auto-appended) already shows the gallery, so
    # it pre-consumes the frame_strip budget.
    _once_per_response = frozenset({"frame_strip", "quick_quiz", "tip_callout"})
    used_kinds: set[str] = (
        {"frame_strip"}
        if any(t.get("component") == "video_filmstrip" for t in assembled_tabs)
        else set()
    )
    for tab in assembled_tabs:
        if tab.get("component") == "overview":
            continue
        secondaries = attach_secondaries(
            tab,
            extraction,
            enrichment,
            attach_frames,
            primary_tag,
            excluded_kinds=used_kinds,
        )
        if secondaries:
            tab["attachments"] = secondaries
            used_kinds |= {
                a["component"] for a in secondaries if a.get("component") in _once_per_response
            }

    # Minimum 3-tab guarantee: add fallback tabs if needed
    if len(assembled_tabs) < 3:
        existing_components = {t.get("component", "") for t in assembled_tabs}
        existing_ids = {t["id"] for t in assembled_tabs}
        fallbacks = build_fallback_candidates(
            synthesis,
            video_meta,
            existing_components,
            existing_ids,
        )
        for fb in fallbacks:
            if len(assembled_tabs) >= 3:
                break
            component = fb.get("component", "")
            if not _validate_assembled_props(component, fb.get("props", {})):
                continue
            assembled_tabs.append(fb)
            logger.info(
                "[assembly] Fallback tab added: id=%r, component=%r",
                fb["id"],
                component,
            )

        if len(assembled_tabs) < 3:
            logger.warning(
                "[assembly] Could not reach 3 tabs even with fallbacks (got %d)",
                len(assembled_tabs),
            )

    _move_quiz_tabs_last(assembled_tabs)

    # Annotate the overview with the final tab count — must run after all
    # additions (cross-tab links, gallery auto-append, fallback candidates)
    # so the "Items" stat reflects what the user actually sees.
    _annotate_overview_item_count(assembled_tabs)

    # Assembly summary
    planned_ids = [t.get("id", "?") for t in raw_tabs if isinstance(t, dict)]
    assembled_ids = {t["id"] for t in assembled_tabs}
    dropped_ids = [tid for tid in planned_ids if tid not in assembled_ids]
    if dropped_ids:
        logger.warning(
            "[assembly] Assembled %d/%d tabs, dropped: %s",
            len(assembled_tabs),
            len(planned_ids),
            dropped_ids,
        )
    else:
        logger.info(
            "[assembly] Assembled %d/%d tabs (none dropped)", len(assembled_tabs), len(planned_ids)
        )

    return {"meta": meta, "tabs": assembled_tabs, "dropped": dropped_tabs}
