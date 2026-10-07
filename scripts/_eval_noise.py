"""Golden-eval noise file: the run-to-run spread of each primary metric.

Built by ``run_eval.py --noise-runs N`` from N full passes over the same live
set (every pass with ``bypassCache``, so each one is a fresh pipeline run), or
rebuilt for $0 from those passes' reports with ``--noise-from``:

* ``noise[m]``    — the per-video noise estimate ``scripts/gate.py`` gates
                    on: ``sd`` = pooled within-video run-to-run sd (one-way
                    ANOVA residual over the N passes), ``df`` = n·(N−1),
                    ``n`` videos, ``passes`` N, and ``sigmaMean`` = sd/√n,
                    the sd of ONE pass's mean over the n videos.
* ``runMeanSpread[m]`` — max − min of the N run means (reported, not gated:
                    one |Δ| is a 1-df σ estimate, far too noisy to gate on).
* ``baseline[m]`` — mean of the N run means.
* ``videos``      — per-video metric means over the passes, so the gate can
                    compare a later run on the same videos (paired).
* ``excludedVideos`` — ids left out of all of the above because their
                    pipeline errored (or was absent) in at least one pass: a
                    failed run is not a quality measurement, and counting it
                    as 0 would inflate the noise and drag the baseline down.
* ``notScored[m]`` — completed videos with no ``m`` value in at least one
                    pass (e.g. no Langfuse faithfulness): "not scored", never
                    a 0 — left out of ``m``'s noise, run means and per-video
                    baseline (``videos[id][m]`` is null), not of other metrics.
* ``retiredVideos`` — report rows whose id is no longer a live golden entry
                    (disabled or removed from ``videos.yaml``); ignored.
* ``layoutStability`` — mean Jaccard of the tab-component multisets between
                    pass 1 and every later pass (reported, not gated).
* ``apiLabel``    — non-secret label of the API the passes ran against (see
                    ``api_label``); the gate refuses a report from another API.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from _eval_metrics import PRIMARY_METRICS, mean_or_none

logger = logging.getLogger("run_eval.noise")

# v2: ``noise`` became the per-video estimate (v1 held the run-mean spread).
NOISE_SCHEMA_VERSION = 2
DEFAULT_NOISE_PATH = (
    Path(__file__).resolve().parent.parent / "dev" / "golden-dataset" / "noise.json"
)


# Hosts whose name is not a secret (dev stacks); anything else is labelled
# by a hash so the EVAL_API_URL secret never lands in an artifact.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "vie-api", "host.docker.internal"})


def api_label(api_url: str) -> str:
    """Non-secret, comparable label for an API base URL.

    ``http://localhost:3000`` → ``localhost:3000``; any other host →
    ``remote:<12 hex>`` (a hash of scheme + host + port — never credentials
    or path), so two reports can be checked for the same target without
    writing ``EVAL_API_URL`` (a CI secret) into committed or uploaded files.
    """
    parts = urlsplit(api_url.strip())
    host = (parts.hostname or "").lower()
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if host in _LOCAL_HOSTS:
        return f"{host}:{port}"
    digest = hashlib.sha256(f"{parts.scheme}://{host}:{port}".encode()).hexdigest()
    return f"remote:{digest[:12]}"


def summary_api_label(data: dict[str, Any]) -> str | None:
    """The API label of a summary/noise file; legacy files carry a raw ``apiUrl``."""
    if data.get("apiLabel"):
        return str(data["apiLabel"])
    return api_label(str(data["apiUrl"])) if data.get("apiUrl") else None


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


def excluded_video_ids(summaries: list[dict[str, Any]]) -> list[str]:
    """Ids that errored — or are missing — in at least one pass."""
    passes = [_videos_by_id(s) for s in summaries]
    ids = {vid for p in passes for vid in p}
    return sorted(vid for vid in ids if any(_failed_in(p, vid) for p in passes))


def _failed_in(videos: dict[str, dict[str, Any]], vid: str) -> bool:
    return vid not in videos or bool(videos[vid].get("error"))


def _scored_ids(passes: list[dict[str, dict[str, Any]]], ids: list[str]) -> dict[str, list[str]]:
    """Per metric, the ids that carry a value in every pass."""
    return {
        m: [
            vid
            for vid in ids
            if all((p[vid].get("metrics") or {}).get(m) is not None for p in passes)
        ]
        for m in PRIMARY_METRICS
    }


def _per_video_means(
    passes: list[dict[str, dict[str, Any]]], ids: list[str], scored: dict[str, list[str]]
) -> dict[str, dict[str, float | None]]:
    return {
        vid: {
            m: mean_or_none((p[vid].get("metrics") or {}).get(m) for p in passes)
            if vid in scored[m]
            else None
            for m in PRIMARY_METRICS
        }
        for vid in ids
    }


def _run_means(
    videos: dict[str, dict[str, Any]], scored: dict[str, list[str]]
) -> dict[str, float | None]:
    """One pass's metric means over the videos scored in every pass."""
    return {
        m: mean_or_none((videos[vid].get("metrics") or {}).get(m) for vid in scored[m])
        for m in PRIMARY_METRICS
    }


def _pass_values(
    passes: list[dict[str, dict[str, Any]]], ids: list[str], metric: str
) -> list[list[float]]:
    """Per video, the metric's value in every pass (``ids`` are all scored)."""
    return [[float((p[vid].get("metrics") or {})[metric]) for p in passes] for vid in ids]


def per_video_noise(rows: list[list[float]]) -> dict[str, float | int] | None:
    """Pooled within-video sd of ``rows`` (one row per video, one value per pass).

    s² = Σᵢ Σₚ (xᵢₚ − x̄ᵢ)² / (n·(N−1)) — for N = 2 that is Σ dᵢ² / 2n over
    the per-video pass differences dᵢ. Not centred on mean(dᵢ): a shift that
    hits a whole pass (provider drift) is noise a fresh run sees too.
    """
    if not rows:
        return None
    n, passes = len(rows), len(rows[0])
    df = n * (passes - 1)
    ss = sum((v - sum(row) / passes) ** 2 for row in rows for v in row)
    sd = math.sqrt(ss / df)
    return {
        "sd": round(sd, 6),
        "df": df,
        "n": n,
        "passes": passes,
        "sigmaMean": round(sd / math.sqrt(n), 6),
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


def live_only(summary: dict[str, Any], live_ids: set[str] | None) -> dict[str, Any]:
    """``summary`` without the rows whose id is not live (``None`` = keep every row)."""
    if live_ids is None:
        return summary
    return {**summary, "videos": [v for v in summary.get("videos") or [] if v["id"] in live_ids]}


def _retired_ids(summaries: list[dict[str, Any]], live_ids: set[str] | None) -> list[str]:
    if live_ids is None:
        return []
    return sorted({vid for s in summaries for vid in _videos_by_id(s)} - live_ids)


def build_noise(
    summaries: list[dict[str, Any]], report_paths: list[str], live_ids: set[str] | None = None
) -> dict[str, Any]:
    """Build the noise file from N >= 2 eval run summaries (``eval-*.json``).

    ``live_ids`` (the live golden ids) drops rows of retired entries; ``None``
    keeps every row.
    """
    if len(summaries) < 2:
        raise ValueError("noise needs at least two run summaries")
    retired = _retired_ids(summaries, live_ids)
    summaries = [live_only(s, live_ids) for s in summaries]
    excluded = excluded_video_ids(summaries)
    passes = [_videos_by_id(s) for s in summaries]
    ids = sorted({vid for p in passes for vid in p} - set(excluded))
    scored = _scored_ids(passes, ids)
    per_pass = [_run_means(p, scored) for p in passes]
    run_means = {m: [p[m] for p in per_pass] for m in PRIMARY_METRICS}
    return {
        "schemaVersion": NOISE_SCHEMA_VERSION,
        "createdAt": datetime.now(UTC).isoformat(),
        "apiLabel": summary_api_label(summaries[0]),
        "runs": len(summaries),
        "reports": report_paths,
        "directions": dict(PRIMARY_METRICS),
        "runMeans": run_means,
        "baseline": {m: mean_or_none(v) for m, v in run_means.items()},
        "noise": {m: per_video_noise(_pass_values(passes, scored[m], m)) for m in PRIMARY_METRICS},
        "runMeanSpread": {m: _spread(v) for m, v in run_means.items()},
        "layoutStability": _layout_stability(summaries),
        "excludedVideos": excluded,
        "notScored": {m: sorted(set(ids) - set(scored[m])) for m in PRIMARY_METRICS},
        "retiredVideos": retired,
        "videos": _per_video_means(passes, ids, scored),
    }


def write_noise(noise: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(noise, indent=2) + "\n", encoding="utf-8")
    return path


def write_noise_from(
    report_paths: list[str], noise_out: str, live_ids: set[str] | None = None
) -> int:
    """Build + write the noise file from pass summaries (``eval-*-rN.json``); exit code.

    ``live_ids``: see ``build_noise``.
    """
    noise = build_noise([load_json(Path(p)) for p in report_paths], report_paths, live_ids)
    path = write_noise(noise, Path(noise_out))
    logger.info(
        "Noise file %s: noise=%s excluded=%s notScored=%s retired=%s",
        path,
        json.dumps(noise["noise"]),
        noise["excludedVideos"],
        json.dumps(noise["notScored"]),
        noise["retiredVideos"],
    )
    return 0
