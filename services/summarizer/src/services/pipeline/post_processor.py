"""Post-processing utilities for pipeline output."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ...utils.data_helpers import parse_timestamp_to_seconds

if TYPE_CHECKING:
    from ...models.pipeline_types import PlanResult

logger = logging.getLogger(__name__)

# Tab IDs that typically contain checklists or quizzes — eligible for celebrations
_CELEBRATION_TAB_IDS = frozenset(
    {
        "quizzes",
        "flashcards",
        "scenarios",
        "packing",
        "ingredients",
        "materials",
        "tools",
    }
)


def drop_empty_tabs(tabs: list[dict], data: dict) -> list[dict]:
    """Remove tabs whose dataSource has fewer than 2 items in the data.

    A tab's dataSource is a dot-separated path like "travel.itinerary".
    If the resolved value is a list with fewer than 2 items, the tab is dropped.
    Non-list values and missing paths are kept (they may be scalars or not yet populated).

    Args:
        tabs: List of tab definitions with "dataSource" keys.
        data: Extracted data dict to check against.

    Returns:
        Filtered list of tabs with non-empty data sources.
    """
    result: list[dict] = []
    for tab in tabs:
        data_source = tab.get("dataSource", "")
        if not data_source:
            result.append(tab)
            continue

        # Resolve dot-separated path
        parts = data_source.split(".")
        value = data
        resolved = True
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                resolved = False
                break

        # Keep tab if path didn't resolve (might be populated later)
        # or if value is not a list (scalar/object data)
        # or if list has 2+ items
        if not resolved or not isinstance(value, list) or len(value) >= 2:
            result.append(tab)
        else:
            logger.debug(
                "Dropping tab %s — dataSource %s has %d items",
                tab.get("id"),
                data_source,
                len(value),
            )

    return result


def assign_section_accents(sections: list[dict]) -> list[dict]:
    """Assign accentIndex 0-6 cycling to sections for visual variety.

    Each section gets an accentIndex that cycles through 0-6,
    which the frontend uses for accent colors.

    Args:
        sections: List of section definitions.

    Returns:
        Sections with accentIndex added (new list, originals not mutated).
    """
    result: list[dict] = []
    for idx, section in enumerate(sections):
        new_section = dict(section)
        new_section["accentIndex"] = idx % 7
        result.append(new_section)
    return result


def merge_narrative(data: dict, narrative_data: dict | None) -> dict:
    """Merge narrative modifier enrichment into main data.

    Adds narrative key moments, quotes, and takeaways to the data dict
    under the "narrative" key. If narrative_data is None, returns data unchanged.

    Args:
        data: Main extracted data dict.
        narrative_data: Narrative modifier data or None.

    Returns:
        Data dict with narrative data merged in.
    """
    if not narrative_data:
        return data

    result = dict(data)
    result["narrative"] = narrative_data
    return result


def resolve_celebrations(tabs: list[dict]) -> list[dict]:
    """Determine which tabs should trigger celebrations.

    Tabs with IDs matching checklist/quiz patterns get a "celebration" flag
    set to True. The frontend uses this to show confetti or completion animations.

    Args:
        tabs: List of tab definitions.

    Returns:
        Tabs with "celebration" boolean added (new list, originals not mutated).
    """
    result: list[dict] = []
    for tab in tabs:
        new_tab = dict(tab)
        tab_id = tab.get("id", "")
        new_tab["celebration"] = tab_id in _CELEBRATION_TAB_IDS
        result.append(new_tab)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Extraction Count Validation (retired — kept as a no-op until 1c.5)
# ─────────────────────────────────────────────────────────────────────────────

# Count field → where its items live in the extraction. The plan no longer
# counts items, so only ``FIELD_TO_DOMAINS`` is still read: the extraction
# retry gate filters hard-miss fields by the domains that own them.
_COUNT_EXTRACTORS: dict[str, list[str]] = {
    "spots": ["travel.itinerary.*.spots"],
    "ingredients": ["food.ingredients"],
    "exercises": ["fitness.exercises"],
    "steps": ["food.steps", "project.steps"],
    "songs": ["music.structure"],
    "tips": ["food.tips", "fitness.tips", "travel.itinerary.*.tips"],
    "products": ["review.comparisons"],
}


def _derive_field_to_domains(extractors: dict[str, list[str]]) -> dict[str, frozenset[str]]:
    """Derive {field → {domain, …}} from the path map.

    The first segment of each dot-path is the domain that owns the field.
    Used to skip retry attempts for fields that no active contentTag schema defines.
    """
    return {
        field: frozenset(path.split(".", 1)[0] for path in paths)
        for field, paths in extractors.items()
    }


# Maps each count field to the set of domains whose schemas actually define
# it. A `tech + learning` video should not retry for "steps" or "tips" —
# those fields exist only in food/project/fitness/travel.
FIELD_TO_DOMAINS: dict[str, frozenset[str]] = _derive_field_to_domains(_COUNT_EXTRACTORS)


def validate_extraction_counts(
    manifest: PlanResult | None,
    extraction_data: dict | None,
    content_tags: list[str] | None = None,
) -> dict[str, dict]:
    """Plan-count vs extraction check — always ``{}`` now.

    The plan stopped estimating flat item counts (pipeline-1min 1b.2: each
    tab's ``brief.expect`` replaced them), so there is nothing to compare and
    the count-triggered retry can never fire. The signature stays
    for the extraction quality gate, which 1c.5 removes together with this.

    Returns:
        An empty warnings dict.
    """
    return {}


# Ratio below which the extraction is flagged for under-coverage: the latest
# timestamped item is far short of the video's end (e.g. a 4.5h video whose
# timeline stops at 1:34 → ratio 0.34).
COVERAGE_GATE_RATIO = 0.85

# Ratio below which coverage is *critical* — the transcript itself is almost
# certainly truncated/incomplete (e.g. a 57-min video whose timestamps stop at
# 3:20 → ratio 0.058, the Gemini-fallback-truncation signature) rather than the
# extraction merely thinning out over a long tail. Logged at error level and
# flagged on meta so the FE/admin can surface "transcript incomplete".
COVERAGE_CRITICAL_RATIO = 0.5

# Lists whose items carry a video offset, mapped to the field holding it.
# Offsets may be int seconds or "M:SS"/"H:MM:SS" strings.
_TIMESTAMP_PATHS: dict[str, str] = {
    "learning.timestamps": "seconds",
    "narrative.keyMoments": "timestamp",
    "news.storyTimeline": "timestamp",
}


def _collect_offsets(data: dict, list_path: str, field: str) -> list[int]:
    """Collect parsed second-offsets from the items of a dot-path list."""
    current: Any = data
    for part in list_path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return []
    if not isinstance(current, list):
        return []

    offsets: list[int] = []
    for item in current:
        if not isinstance(item, dict):
            continue
        raw = item.get(field)
        if raw is None and field != "seconds":
            raw = item.get("seconds")  # LearningTimestamp carries both
        secs = parse_timestamp_to_seconds(raw)
        if secs is not None:
            offsets.append(secs)
    return offsets


def coverage_is_degraded(coverage: dict | None) -> bool:
    """True when a run's extraction coverage marks the output as degraded.

    Degraded = at least one extraction batch was dropped (a chunk of the video
    produced no output) OR coverage is critically low (transcript truncated).
    Consumed by the assembly phase to flag the persisted doc + meta, by the
    SSE terminal events, and (via the doc) by the admin run badge.
    """
    if not coverage:
        return False
    if coverage.get("critical"):
        return True
    dropped = coverage.get("batchesDropped")
    return isinstance(dropped, int) and dropped > 0


def compute_extraction_coverage(
    extraction_data: dict | None,
    duration_seconds: float,
) -> dict | None:
    """Compare the latest timestamped extraction offset against video duration.

    Returns ``{maxTimestamp, duration, ratio, tailMissingSeconds}`` or None
    when there is no duration or no timestamped content to measure. A ratio
    well below 1.0 means a tail of the video produced no timestamped output —
    the signature of the under-extraction bug (4.5h video stopping at 1:34).
    """
    if not extraction_data or duration_seconds <= 0:
        return None

    offsets: list[int] = []
    for list_path, field in _TIMESTAMP_PATHS.items():
        offsets.extend(_collect_offsets(extraction_data, list_path, field))
    if not offsets:
        return None

    max_ts = max(offsets)
    ratio = max_ts / duration_seconds
    return {
        "maxTimestamp": max_ts,
        "duration": int(duration_seconds),
        "ratio": round(ratio, 3),
        "tailMissingSeconds": max(0, int(duration_seconds - max_ts)),
    }
