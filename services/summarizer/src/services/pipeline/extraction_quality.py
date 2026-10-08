"""Extraction quality metric: the share of planned dataSources the extraction populated."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.utils.data_helpers import is_empty_data


@dataclass
class ExtractionQuality:
    """Result of extraction quality check."""

    score: float  # 0.0 to 1.0
    populated: int
    total: int
    empty_fields: list[str] = field(default_factory=list)


def _resolve_dot_path(data: dict, path: str) -> Any:
    """Resolve a dot-notation path against extraction data.

    Supports wildcard ``*`` as a terminal — returns the current object at that point
    (same semantics as assembly's ``resolve_data_source``).
    """
    parts = path.split(".")
    obj: Any = data
    for part in parts:
        if part == "*":
            return obj
        if isinstance(obj, dict) and part in obj:
            obj = obj[part]
        else:
            return None
    return obj


def check_extraction_quality(
    plan_tabs: list[dict],
    extraction_data: dict,
) -> ExtractionQuality:
    """Check how many plan tabs have populated extraction data.

    Skips tabs whose dataSource starts with meta, synthesis, or enrichment
    since those aren't extraction outputs.

    Args:
        plan_tabs: List of tab dicts from plan/triage, each with "dataSource".
        extraction_data: The extraction output dict.

    Returns:
        ExtractionQuality with score, counts, and list of empty field paths.
    """
    if not plan_tabs or not extraction_data:
        return ExtractionQuality(score=0.0, populated=0, total=0, empty_fields=[])

    skip_prefixes = ("meta", "synthesis", "enrichment")

    total = 0
    populated = 0
    empty_fields: list[str] = []

    for tab in plan_tabs:
        ds = tab.get("dataSource", "")
        if not ds:
            continue
        # Skip non-extraction sources
        if any(ds.startswith(prefix) for prefix in skip_prefixes):
            continue

        total += 1
        resolved = _resolve_dot_path(extraction_data, ds)

        if is_empty_data(resolved):
            empty_fields.append(ds)
        else:
            populated += 1

    score = populated / total if total > 0 else 1.0
    return ExtractionQuality(
        score=score,
        populated=populated,
        total=total,
        empty_fields=empty_fields,
    )
