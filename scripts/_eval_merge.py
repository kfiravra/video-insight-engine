"""Fold new runs into existing eval reports, and re-sync reports with videos.yaml.

* ``run_eval.py --ids <new> --noise-runs 2 --merge-into r1.json r2.json`` runs
  only the selected ids, then writes pass k's rows into the k-th existing
  report (a row with the same id is replaced) and rebuilds the noise file
  from the merged reports — how a replacement golden video joins a baseline
  without re-running the whole set.
* ``run_eval.py --resync r1.json r2.json`` ($0, no API calls) re-derives every
  row from the current ``videos.yaml``: quality is re-scored from the stored
  row (``_eval_scoring.rescore`` — an ``expectedTabs`` / ``requiredComponents``
  / ``domain`` edit is applied exactly; a ``keyContent`` edit needs a fresh
  run), and the ``xfail`` markers of the stored assertions are re-read by
  key. A report written before results carried a ``key`` is migrated once
  (``assign_legacy_keys``) — gate.py refuses such a report until it is.

A merge re-syncs too, so a merged baseline is scored against one dataset.
Both rewrite the report .json/.md/.csv in place, keeping the replaced state
as ``<file>.<ts>.bak`` (one per rewrite). Rows of ids no longer in the dataset are kept as they are
(the gate and the noise file ignore ids that are not live).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

from _eval_assertions import apply_markers, assign_legacy_keys
from _eval_noise import load_json
from _eval_report import VideoOutcome, backup_reports, outcomes_from_summary, rewrite_reports
from _eval_schema import parse_assertions
from _eval_scoring import rescore

logger = logging.getLogger("run_eval.merge")


def resync_outcome(outcome: VideoOutcome, record: dict[str, Any] | None) -> VideoOutcome:
    """``outcome`` re-scored and re-marked against its current dataset ``record``.

    An errored row keeps its zero quality (there is nothing to re-score);
    a row whose id left the dataset (``record`` None) is returned unchanged.
    """
    if record is None:
        return outcome
    domain = record.get("domain", "unknown")
    assertions = parse_assertions(record)
    quality = (
        outcome.quality
        if outcome.error
        else rescore(record, outcome.quality, outcome.components).as_dict()
    )
    return replace(
        outcome,
        domain=domain,
        quality={**quality, "domain": domain},
        assertions=apply_markers(assign_legacy_keys(outcome.assertions, assertions), assertions),
    )


def merge_outcomes(base: list[VideoOutcome], fresh: list[VideoOutcome]) -> list[VideoOutcome]:
    """``base`` with ``fresh`` rows added (same id → replaced), ordered by golden id."""
    by_id = {o.id: o for o in base}
    by_id.update({o.id: o for o in fresh})
    return sorted(by_id.values(), key=lambda o: o.id)


def _rewrite(
    json_path: Path, fresh: list[VideoOutcome], records: dict[str, dict[str, Any]]
) -> list[str]:
    """Merge ``fresh`` into the report at ``json_path`` and re-sync it; return changed ids."""
    summary = load_json(json_path)
    stored = outcomes_from_summary(summary)
    merged = [resync_outcome(o, records.get(o.id)) for o in merge_outcomes(stored, fresh)]
    before = {o.id: o for o in stored}
    changed = sorted(o.id for o in merged if before.get(o.id) != o)
    backup_reports(json_path)
    rewrite_reports(merged, summary, json_path)
    return changed


def merge_into_reports(
    passes: list[list[VideoOutcome]],
    targets: list[str],
    records: dict[str, dict[str, Any]],
) -> None:
    """Write pass k's outcomes into ``targets[k]`` (in place, ``<file>.<ts>.bak`` kept)."""
    if len(passes) != len(targets):
        raise ValueError(f"{len(passes)} pass(es) but {len(targets)} --merge-into report(s)")
    for outcomes, target in zip(passes, targets, strict=True):
        changed = _rewrite(Path(target), outcomes, records)
        logger.info(
            "Merged %s into %s (rows changed: %s)", [o.id for o in outcomes], target, changed
        )


def run_resync(report_paths: list[str], records: dict[str, dict[str, Any]]) -> int:
    """CLI entry (``--resync``): re-sync each report with the dataset; exit code."""
    for path in report_paths:
        changed = _rewrite(Path(path), [], records)
        logger.info("Re-synced %s with the dataset (rows changed: %s)", path, changed)
    return 0
