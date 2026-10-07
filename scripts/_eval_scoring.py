"""Legacy golden quality score (``overall``) and the dry-run stub.

``score_entry`` scores one assembled response against an entry's
expectations. It is the ``quality`` primary metric:

  - tabCount    within ±1 of ``expectedTabs``          weight 0.15
  - components  every ``requiredComponents`` appears   weight 0.35
  - keyContent  share of ``keyContent`` terms present  weight 0.25
  - emptyTabs   no tab with an empty list prop         weight 0.10
  - forbidden   no ``forbiddenComponents`` present     weight 0.15
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

_EMPTY_LIST_KEYS = ("items", "data", "rows", "questions", "cards")


@dataclass
class EvalResult:
    id: str
    domain: str
    tab_count: int
    expected_tab_count: int
    tab_count_score: float
    component_coverage: float
    content_coverage: float
    empty_tab_count: int
    overall: float
    # 1.0 when no forbiddenComponents appeared; 0.0 on ANY hit. A forbidden
    # component (e.g. quiz_arena on an unboxing) is a hard product bug, so it
    # carries its own weight in `overall` rather than diluting coverage.
    forbidden_ok: float = 1.0
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _flatten_text(tabs: list[dict[str, Any]]) -> str:
    """Concat all text-ish strings in the assembled tabs into one searchable blob."""
    out: list[str] = []
    for tab in tabs:
        out.append(str(tab.get("label", "")))
        props = tab.get("props", {})
        if isinstance(props, dict):
            out.append(json.dumps(props, default=str))
    return " ".join(out).lower()


def _coverage(expected: list[str], present: set[str] | str) -> float:
    """Share of ``expected`` found in ``present`` (1.0 when nothing is expected)."""
    if not expected:
        return 1.0
    return sum(1 for e in expected if e.lower() in present) / len(expected)


def _empty_tab_count(tabs: list[dict[str, Any]]) -> int:
    # Loose on purpose: only the common list keys, so intentionally short
    # overview tabs don't false-positive.
    empty = 0
    for tab in tabs:
        props = tab.get("props", {}) or {}
        if not isinstance(props, dict):
            continue
        if any(isinstance(props.get(k), list) and not props[k] for k in _EMPTY_LIST_KEYS):
            empty += 1
    return empty


def score_entry(expected: dict[str, Any], actual: dict[str, Any]) -> EvalResult:
    """Score one assembled response against its golden expectations.

    Tolerant of partial data — every sub-score caps at ``1.0`` even when the
    expected list is empty, so missing fields don't depress the overall.
    """
    tabs = actual.get("tabs", []) or []
    expected_count = len(expected.get("expectedTabs") or [])
    tab_count_score = max(0.0, 1.0 - abs(len(tabs) - expected_count) * 0.25)
    present = {str(t.get("component", "")).lower() for t in tabs}
    component_coverage = _coverage(expected.get("requiredComponents") or [], present)
    content_coverage = _coverage(expected.get("keyContent") or [], _flatten_text(tabs))
    empty = _empty_tab_count(tabs)
    forbidden_hits = [c for c in expected.get("forbiddenComponents") or [] if c.lower() in present]
    forbidden_ok = 0.0 if forbidden_hits else 1.0

    overall = round(
        (tab_count_score * 0.15)
        + (component_coverage * 0.35)
        + (content_coverage * 0.25)
        + (max(0.0, 1.0 - 0.1 * empty) * 0.10)
        + (forbidden_ok * 0.15),
        3,
    )
    return EvalResult(
        id=expected.get("id", "unknown"),
        domain=expected.get("domain", "unknown"),
        tab_count=len(tabs),
        expected_tab_count=expected_count,
        tab_count_score=round(tab_count_score, 3),
        component_coverage=round(component_coverage, 3),
        content_coverage=round(content_coverage, 3),
        empty_tab_count=empty,
        overall=overall,
        forbidden_ok=forbidden_ok,
        notes=f"forbidden: {', '.join(forbidden_hits)}" if forbidden_hits else "",
    )


def failed_result(expected: dict[str, Any], note: str) -> EvalResult:
    """All-zero result for a video whose pipeline run failed."""
    return EvalResult(
        id=expected.get("id", "unknown"),
        domain=expected.get("domain", "unknown"),
        tab_count=0,
        expected_tab_count=len(expected.get("expectedTabs") or []),
        tab_count_score=0.0,
        component_coverage=0.0,
        content_coverage=0.0,
        empty_tab_count=0,
        overall=0.0,
        notes=note[:200],
    )


def stub_actual(expected: dict[str, Any]) -> dict[str, Any]:
    """Stub assembled response for ``--dry-run``.

    Returns exactly what the expectations want, so a dry-run smoke-tests the
    scoring and reporting path without any network call.
    """
    items = [{"text": term} for term in expected.get("keyContent", [])]
    components = expected.get("requiredComponents", []) + ["overview"] * 10
    tabs = [
        {
            "id": tab_id,
            "label": tab_id.replace("_", " ").title(),
            "component": component,
            "props": {"items": items},
        }
        for tab_id, component in zip(expected.get("expectedTabs", []), components, strict=False)
    ]
    return {"meta": {"language": expected.get("language", "en")}, "tabs": tabs}
