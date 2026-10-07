"""Primary golden-eval metrics beyond the quality score.

Duplicate-item rate (lower is better)
-------------------------------------
Measured over the assembled tabs the API returns (``GET /api/videos/:id``).

* **Item** — one element of any list found anywhere inside a tab's ``props``
  (lists nested in ``props.data`` included), when the element is a string or
  an object. Its **text** is the string itself, or the object's string
  values joined, minus media/layout keys (``NON_CONTENT_KEYS``: urls, S3
  keys, emoji, times, frame captions). ``video_filmstrip`` tabs are skipped
  entirely: their captions are frame descriptions, repeated by design next to
  the moments that share the frame.
* **Countable** — an item whose normalised text (lower-case, punctuation
  stripped, unicode word tokens) has at least ``MIN_TOKENS`` tokens. Shorter
  items ("Salt", "Plank") legitimately repeat and are ignored.
* **Near-duplicate** — two countable items whose token sets have Jaccard
  similarity >= ``DUPLICATE_JACCARD``.
* An item is a **duplicate** when it near-duplicates an earlier item of the
  *same list* (within-list), or an item of an *earlier tab* (cross-tab, in
  tab order). The ``overview`` tab takes part in the within-list check only:
  its takeaways summarise the other tabs on purpose. Each item counts once.
* **Rate** per video = duplicates / countable items (``None`` when the video
  has no countable item). The run metric is the mean of per-video rates.

Faithfulness (higher is better)
-------------------------------
The pipeline's own LLM judge (``faithfulness.py``) scores a deterministic
sample of extracted claims against the transcript and logs the fraction
grounded as the ``faithfulness`` score on the run's Langfuse trace
(``pipeline:<videoSummaryId>``). It is not persisted in Mongo nor returned by
the API, so the Langfuse trace is the only source. The sample rate is
per-claim (prod 0.2 → round(n*0.2) claims, min 1, capped at 6), so every
run with extracted claims gets a score. The run metric is the mean over the
videos that have one; coverage is reported next to it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from typing import Any

# Primary metrics and the direction that counts as better. Single-sourced
# here for the run summary, the noise file and scripts/gate.py.
PRIMARY_METRICS: dict[str, str] = {
    "quality": "higher",
    "faithfulness": "higher",
    "duplicateRate": "lower",
}

DUPLICATE_JACCARD = 0.8
MIN_TOKENS = 3
NON_CONTENT_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "url",
        "href",
        "thumbnailUrl",
        "s3Key",
        "emoji",
        "icon",
        "color",
        "mood",
        "time",
        "timestamp",
        "seconds",
        "endSeconds",
        "caption",
        "frameCaption",
        "frameEvidence",
        "frameOcr",
        "frameSceneType",
    }
)
SKIPPED_COMPONENTS: frozenset[str] = frozenset({"video_filmstrip"})
CROSS_TAB_EXEMPT_COMPONENTS: frozenset[str] = frozenset({"overview"})

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


# ─── Duplicate-item rate ───────────────────────────────────────────────
def _item_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return " ".join(
            v for k, v in item.items() if k not in NON_CONTENT_KEYS and isinstance(v, str)
        )
    return ""


def _tokens(text: str) -> frozenset[str]:
    return frozenset(_TOKEN_RE.findall(text.lower()))


def _iter_lists(node: Any) -> Iterator[list[Any]]:
    """Yield every list (at any depth) whose elements include strings or objects."""
    if isinstance(node, dict):
        for value in node.values():
            yield from _iter_lists(value)
    elif isinstance(node, list):
        if any(isinstance(x, (str, dict)) for x in node):
            yield node
        for element in node:
            if isinstance(element, dict):
                yield from _iter_lists(element)


def _is_near_duplicate(a: frozenset[str], b: frozenset[str]) -> bool:
    union = len(a | b)
    return union > 0 and len(a & b) / union >= DUPLICATE_JACCARD


def _matches_any(tokens: frozenset[str], pool: Iterable[frozenset[str]]) -> bool:
    return any(_is_near_duplicate(tokens, other) for other in pool)


def _tab_lists(tab: dict[str, Any]) -> list[list[frozenset[str]]]:
    """Countable token sets per list for one tab."""
    lists: list[list[frozenset[str]]] = []
    for items in _iter_lists(tab.get("props") or {}):
        token_sets = [_tokens(_item_text(item)) for item in items]
        lists.append([t for t in token_sets if len(t) >= MIN_TOKENS])
    return lists


def duplicate_item_rate(tabs: list[dict[str, Any]]) -> float | None:
    """Fraction of countable items that near-duplicate an earlier item (see module doc)."""
    countable = 0
    duplicates = 0
    earlier_tabs: list[frozenset[str]] = []
    for tab in tabs:
        component = str(tab.get("component", "")).lower()
        if component in SKIPPED_COMPONENTS:
            continue
        cross_tab = component not in CROSS_TAB_EXEMPT_COMPONENTS
        this_tab: list[frozenset[str]] = []
        for items in _tab_lists(tab):
            seen_in_list: list[frozenset[str]] = []
            for tokens in items:
                countable += 1
                if _matches_any(tokens, seen_in_list) or (
                    cross_tab and _matches_any(tokens, earlier_tabs)
                ):
                    duplicates += 1
                seen_in_list.append(tokens)
            this_tab.extend(items)
        if cross_tab:
            earlier_tabs.extend(this_tab)
    if countable == 0:
        return None
    return round(duplicates / countable, 4)


# ─── Langfuse trace signals ────────────────────────────────────────────
def extract_faithfulness(trace: dict[str, Any]) -> float | None:
    """Return the trace's latest ``faithfulness`` score value, if any."""
    scores = [
        s
        for s in trace.get("scores") or []
        if isinstance(s, dict)
        and s.get("name") == "faithfulness"
        and isinstance(s.get("value"), (int, float))
    ]
    if not scores:
        return None
    latest = max(scores, key=lambda s: str(s.get("timestamp") or ""))
    return float(latest["value"])


def _parse_json_object(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    match = _JSON_OBJECT_RE.search(raw)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


# The early domain/format call: the tier probe since pipeline-1min 1b.1; runs
# recorded before it (the phase-0 baseline, refreshed with --refresh-langfuse)
# carry the same ``{"format": …}`` answer on the retired ``classifier``.
_FORMAT_OBSERVATIONS = ("tier_probe", "classifier")


def _observation_format(observation: dict[str, Any]) -> str | None:
    data = _parse_json_object(observation.get("output"))
    fmt = (data or {}).get("format")
    return fmt.strip().lower() if isinstance(fmt, str) and fmt.strip() else None


def extract_probe_format(trace: dict[str, Any]) -> str | None:
    """Return the ``format`` the run's ``tier_probe`` (else ``classifier``) generation produced."""
    observations = [o for o in trace.get("observations") or [] if isinstance(o, dict)]
    for name in _FORMAT_OBSERVATIONS:
        for observation in observations:
            if observation.get("name") != name:
                continue
            fmt = _observation_format(observation)
            if fmt is not None:
                return fmt
    return None


# ─── Aggregation ───────────────────────────────────────────────────────
def mean_or_none(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 4)
