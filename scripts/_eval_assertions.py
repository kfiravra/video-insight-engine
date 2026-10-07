"""Deterministic per-video assertions for the golden eval.

Evaluates the typed assertions from ``_eval_schema`` against one assembled
API response (``{meta, tabs, duration}``) plus the trace signals read from
Langfuse. Results are reported per video; ``scripts/gate.py`` fails on any
failed assertion that is not marked ``xfail``, and on any strict ``xfail``
that passed (XPASS — the marker must go). Each result carries the ``key`` of
the dataset assertion it came from, so markers are re-read by identity.

An assertion whose input is unavailable (no tier-probe format on the trace,
no duration on the response) is reported as *skipped* (``passed=None``) —
never as a pass, never as a gate failure.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from typing import Any

from _eval_schema import (
    Assertion,
    ExpectedDomain,
    ExpectedFormat,
    ForbiddenComponents,
    MinItems,
    MinItemsWithField,
    NoTimestampBeyondDuration,
    QuizAbsentOrLast,
    RequiredComponents,
)

# Numeric fields that hold a position in the video, in seconds.
TIMESTAMP_KEYS: frozenset[str] = frozenset(
    {"timestamp", "seconds", "endSeconds", "startSeconds", "startTime", "endTime"}
)
_CLOCK_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")

# (passed, detail) — passed is None when the input was unavailable.
_Verdict = tuple[bool | None, str]


@dataclass(frozen=True)
class AssertionResult:
    type: str
    passed: bool | None  # None = skipped (input unavailable)
    detail: str = ""
    xfail: str | None = None
    until: str | None = None  # the task expected to fix an xfail (e.g. "1b.2")
    strict: bool = True  # a strict xfail that passes (XPASS) fails the gate
    key: str | None = None  # the dataset assertion's ``key``; None in legacy reports

    @property
    def gating_failure(self) -> bool:
        return self.passed is False and self.xfail is None

    @property
    def strict_xpass(self) -> bool:
        """A strict marker on a check that now passes: the fix landed, drop the marker."""
        return self.passed is True and bool(self.xfail) and self.strict

    @property
    def label(self) -> str:
        """PASS / FAIL / SKIP, or XFAIL / XPASS for an ``xfail``-marked check."""
        if self.passed is None:
            return "SKIP"
        if self.xfail:
            return "XPASS" if self.passed else "XFAIL"
        return "PASS" if self.passed else "FAIL"

    @property
    def marker(self) -> str:
        """``xfail: <reason>; until <task>`` — empty for an unmarked check."""
        if not self.xfail:
            return ""
        until = f"; until {self.until}" if self.until else ""
        return f"xfail: {self.xfail}{until}" + ("" if self.strict else "; non-strict")


@dataclass(frozen=True)
class TraceSignals:
    trace_id: str | None = None
    faithfulness: float | None = None
    probe_format: str | None = None


# ─── Helpers ───────────────────────────────────────────────────────────
def _components(tabs: list[dict[str, Any]]) -> list[str]:
    return [str(t.get("component", "")).lower() for t in tabs]


def _tabs_with(tabs: list[dict[str, Any]], component: str) -> list[dict[str, Any]]:
    return [t for t in tabs if str(t.get("component", "")).lower() == component.lower()]


def _props_list(tab: dict[str, Any], field: str | None) -> list[Any]:
    props = tab.get("props") or {}
    if not isinstance(props, dict):
        return []
    if field is not None:
        value = props.get(field)
        return value if isinstance(value, list) else []
    lists = [v for v in props.values() if isinstance(v, list)]
    return max(lists, key=len) if lists else []


def _clock_seconds(value: str) -> float | None:
    match = _CLOCK_RE.match(value.strip())
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)


def _iter_timestamps(node: Any, path: str) -> Iterator[tuple[str, float]]:
    if isinstance(node, list):
        for i, element in enumerate(node):
            yield from _iter_timestamps(element, f"{path}[{i}]")
    elif isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}"
            if key in TIMESTAMP_KEYS and isinstance(value, (int, float)):
                yield here, float(value)
            elif key == "time" and isinstance(value, str):
                seconds = _clock_seconds(value)
                if seconds is not None:
                    yield here, seconds
            else:
                yield from _iter_timestamps(value, here)


# ─── Evaluators ────────────────────────────────────────────────────────
def _expected_domain(a: ExpectedDomain, actual: dict[str, Any], _: TraceSignals) -> _Verdict:
    primary = str((actual.get("meta") or {}).get("primaryTag") or "")
    ok = primary.lower() in {v.lower() for v in a.values}
    return ok, f"primaryTag={primary or '∅'} expected one of {a.values}"


def _expected_format(a: ExpectedFormat, _: dict[str, Any], signals: TraceSignals) -> _Verdict:
    fmt = signals.probe_format
    if fmt is None:
        return None, "probe format unavailable (no Langfuse trace/observation)"
    return fmt in {v.lower() for v in a.values}, f"format={fmt} expected one of {a.values}"


def _forbidden(a: ForbiddenComponents, actual: dict[str, Any], _: TraceSignals) -> _Verdict:
    present = set(_components(actual.get("tabs") or []))
    hits = [c for c in a.components if c.lower() in present]
    return not hits, f"forbidden present: {hits}" if hits else "none present"


def _required(a: RequiredComponents, actual: dict[str, Any], _: TraceSignals) -> _Verdict:
    present = set(_components(actual.get("tabs") or []))
    missing = []
    for entry in a.components:
        options = [entry] if isinstance(entry, str) else entry
        if not any(o.lower() in present for o in options):
            missing.append(entry)
    return not missing, f"missing: {missing}" if missing else "all present"


def _quiz_absent_or_last(a: QuizAbsentOrLast, actual: dict[str, Any], _: TraceSignals) -> _Verdict:
    components = _components(actual.get("tabs") or [])
    quiz = {c.lower() for c in a.components}
    positions = [i for i, c in enumerate(components) if c in quiz]
    if not positions:
        return True, "no quiz"
    ok = positions == [len(components) - 1]
    return ok, f"quiz at tab positions {positions} of {len(components)}"


def _min_items(a: MinItems, actual: dict[str, Any], _: TraceSignals) -> _Verdict:
    tabs = _tabs_with(actual.get("tabs") or [], a.component)
    if not tabs:
        return False, f"no {a.component} tab"
    counts = [len(_props_list(t, a.field)) for t in tabs]
    return min(counts) >= a.min_items, f"{a.component} items={counts} min={a.min_items}"


def _min_items_with_field(
    a: MinItemsWithField, actual: dict[str, Any], _: TraceSignals
) -> _Verdict:
    tabs = _tabs_with(actual.get("tabs") or [], a.component)
    if not tabs:
        return False, f"no {a.component} tab"
    items = [i for t in tabs for i in _props_list(t, None) if isinstance(i, dict)]
    with_field = sum(1 for i in items if any(i.get(f) for f in a.fields))
    ok = with_field >= a.min_items
    return ok, f"{with_field}/{len(items)} {a.component} items carry {a.fields}"


def _no_timestamp_beyond(
    a: NoTimestampBeyondDuration, actual: dict[str, Any], _: TraceSignals
) -> _Verdict:
    duration = actual.get("duration")
    if not isinstance(duration, (int, float)) or duration <= 0:
        return None, "video duration unavailable"
    limit = duration + a.tolerance_seconds
    beyond = [(p, v) for p, v in _iter_timestamps(actual.get("tabs") or [], "tabs") if v > limit]
    if not beyond:
        return True, f"all timestamps <= {duration}s"
    shown = ", ".join(f"{p}={v:g}" for p, v in beyond[:3])
    return False, f"{len(beyond)} beyond {duration}s: {shown}"


_EVALUATORS: dict[type, Callable[..., _Verdict]] = {
    ExpectedDomain: _expected_domain,
    ExpectedFormat: _expected_format,
    ForbiddenComponents: _forbidden,
    RequiredComponents: _required,
    QuizAbsentOrLast: _quiz_absent_or_last,
    MinItems: _min_items,
    MinItemsWithField: _min_items_with_field,
    NoTimestampBeyondDuration: _no_timestamp_beyond,
}


# ─── Entry points ──────────────────────────────────────────────────────
COMPLETED = "completed"


def completed_result(error: str | None) -> AssertionResult:
    """Implicit assertion on every live video: the pipeline run completed."""
    if error:
        return AssertionResult(type=COMPLETED, passed=False, detail=error[:200], key=COMPLETED)
    return AssertionResult(type=COMPLETED, passed=True, detail="run completed", key=COMPLETED)


def evaluate_assertions(
    assertions: list[Assertion], actual: dict[str, Any], signals: TraceSignals
) -> list[AssertionResult]:
    """Run each typed assertion against one assembled response."""
    results: list[AssertionResult] = []
    for assertion in assertions:
        passed, detail = _EVALUATORS[type(assertion)](assertion, actual, signals)
        results.append(
            AssertionResult(
                type=assertion.type,
                passed=passed,
                detail=detail,
                xfail=assertion.xfail_reason,
                until=assertion.xfail_until,
                strict=assertion.xfail_strict,
                key=assertion.key,
            )
        )
    return results


def _marked(result: AssertionResult, assertion: Assertion | None) -> AssertionResult:
    if assertion is None:  # the check left the dataset or changed: never keep its marker
        return replace(result, xfail=None, until=None, strict=True)
    return replace(
        result,
        xfail=assertion.xfail_reason,
        until=assertion.xfail_until,
        strict=assertion.xfail_strict,
    )


def apply_markers(
    results: list[AssertionResult], assertions: list[Assertion]
) -> list[AssertionResult]:
    """Re-read the ``xfail`` markers of stored results from the current dataset.

    Matched by ``key``: a result whose dataset assertion was edited or
    removed (or a legacy result without a key) loses its stored marker, so a
    stale marker can never hide a different failing check.
    """
    by_key = {a.key: a for a in assertions}
    return [
        r if r.type == COMPLETED else _marked(r, by_key.get(r.key) if r.key else None)
        for r in results
    ]


def assign_legacy_keys(
    results: list[AssertionResult], assertions: list[Assertion]
) -> list[AssertionResult]:
    """One-time migration of a report written before results carried a ``key``.

    Keys are assigned by position only when the stored types line up with
    ``assertions`` exactly; otherwise the keyless results get a key that
    matches nothing, so ``apply_markers`` drops their markers (strict).
    """
    if all(r.key for r in results):
        return results
    lead = 1 if results and results[0].type == COMPLETED else 0
    body = results[lead:]
    lined_up = [r.type for r in body] == [a.type for a in assertions]
    keyed = [
        r if r.key else replace(r, key=assertions[i].key if lined_up else f"unmatched:{r.type}:{i}")
        for i, r in enumerate(body)
    ]
    return [replace(r, key=r.key or COMPLETED) for r in results[:lead]] + keyed
