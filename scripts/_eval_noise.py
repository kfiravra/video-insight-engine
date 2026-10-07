"""Golden-eval noise file: the run-to-run spread of each primary metric.

Built by ``run_eval.py --noise-runs N`` from N full passes over the same live
set (every pass with ``bypassCache``, so each one is a fresh pipeline run):

* ``noise[m]``    — max − min of the N run means of metric ``m`` (|Δ| for
                    N = 2). ``scripts/gate.py`` treats a later drop larger
                    than this as a regression.
* ``baseline[m]`` — mean of the N run means.
* ``videos``      — per-video metric means over the passes, so the gate can
                    compare a later run on the same videos (paired).
* ``layoutStability`` — mean Jaccard of the tab-component multisets between
                    pass 1 and every later pass (reported, not gated).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from _eval_metrics import PRIMARY_METRICS, mean_or_none

NOISE_SCHEMA_VERSION = 1
DEFAULT_NOISE_PATH = (
    Path(__file__).resolve().parent.parent / "dev" / "golden-dataset" / "noise.json"
)


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def _spread(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if len(present) < 2:
        return None
    return round(max(present) - min(present), 4)


def _videos_by_id(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {v["id"]: v for v in summary.get("videos") or []}


def _per_video_means(summaries: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    passes = [_videos_by_id(s) for s in summaries]
    ids = sorted({vid for p in passes for vid in p})
    return {
        vid: {
            m: mean_or_none((p[vid].get("metrics") or {}).get(m) for p in passes if vid in p)
            for m in PRIMARY_METRICS
        }
        for vid in ids
    }


def layout_jaccard(a: list[str], b: list[str]) -> float:
    """Jaccard similarity of two tab-component multisets (1.0 when both empty)."""
    left, right = Counter(a), Counter(b)
    union = sum((left | right).values())
    return sum((left & right).values()) / union if union else 1.0


def _layout_stability(summaries: list[dict[str, Any]]) -> float | None:
    first, scores = _videos_by_id(summaries[0]), []
    for later in summaries[1:]:
        for vid, video in _videos_by_id(later).items():
            base = (first.get(vid) or {}).get("components")
            if base and video.get("components"):
                scores.append(layout_jaccard(base, video["components"]))
    return mean_or_none(scores)


def build_noise(summaries: list[dict[str, Any]], report_paths: list[str]) -> dict[str, Any]:
    """Build the noise file from N >= 2 eval run summaries (``eval-*.json``)."""
    if len(summaries) < 2:
        raise ValueError("noise needs at least two run summaries")
    run_means = {m: [(s.get("metrics") or {}).get(m) for s in summaries] for m in PRIMARY_METRICS}
    return {
        "schemaVersion": NOISE_SCHEMA_VERSION,
        "createdAt": datetime.now(UTC).isoformat(),
        "apiUrl": summaries[0].get("apiUrl"),
        "runs": len(summaries),
        "reports": report_paths,
        "directions": dict(PRIMARY_METRICS),
        "runMeans": run_means,
        "baseline": {m: mean_or_none(v) for m, v in run_means.items()},
        "noise": {m: _spread(v) for m, v in run_means.items()},
        "layoutStability": _layout_stability(summaries),
        "videos": _per_video_means(summaries),
    }


def write_noise(noise: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(noise, indent=2) + "\n", encoding="utf-8")
    return path
