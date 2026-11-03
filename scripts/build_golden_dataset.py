#!/usr/bin/env python3
"""Upload ``dev/golden-dataset/videos.yaml`` to Langfuse as a Dataset.

Each YAML entry becomes one Langfuse dataset item with::

    input:           {url, domain, format, language}
    expectedOutput:  {expectedTabs, requiredComponents, forbiddenComponents, keyContent}
    metadata:        {id}

Idempotent: re-running rewrites existing items if their content drifted,
keyed on the entry's stable ``id``.

Usage::

    LANGFUSE_PUBLIC_KEY=pk LANGFUSE_SECRET_KEY=sk \\
        python3 scripts/build_golden_dataset.py --dataset-name vie-golden-v1
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_golden_dataset")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DATASET_PATH = _REPO_ROOT / "dev" / "golden-dataset" / "videos.yaml"


def load_dataset(path: Path = _DATASET_PATH) -> list[dict[str, Any]]:
    """Read videos.yaml into a flat list of records.

    Falls back to a lightweight YAML parser if PyYAML isn't installed —
    enough for the simple flat structure used in golden videos.
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        sys.exit(
            "PyYAML is required: pip install pyyaml. "
            "(Avoiding a custom parser keeps this script honest.)"
        )
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    videos = data.get("videos", []) if isinstance(data, dict) else []
    if not isinstance(videos, list):
        sys.exit(f"{path}: expected a top-level 'videos' list")
    return videos


def _build_client():
    try:
        from langfuse import Langfuse  # type: ignore
    except ImportError as exc:
        sys.exit(f"Langfuse SDK is not installed: {exc}")
    public = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret = os.environ.get("LANGFUSE_SECRET_KEY")
    base_url = os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    if not public or not secret:
        sys.exit("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set")
    # SDK v4: ``base_url`` (``host`` is deprecated).
    return Langfuse(public_key=public, secret_key=secret, base_url=base_url)


def upload(records: list[dict[str, Any]], dataset_name: str) -> dict[str, int]:
    """Create/update a Langfuse dataset with one item per record."""
    client = _build_client()
    try:
        client.create_dataset(name=dataset_name)
    except Exception as exc:  # noqa: BLE001 — typically "already exists"
        logger.info("Dataset create skipped (already exists?): %s", exc)

    summary = {"uploaded": 0, "error": 0}
    for rec in records:
        rid = rec.get("id")
        if not rid:
            logger.warning("Skipping entry missing 'id': %s", rec)
            continue
        item_input = {
            "url": rec.get("url"),
            "domain": rec.get("domain"),
            "format": rec.get("format"),
            "language": rec.get("language"),
        }
        expected = {
            "expectedTabs": rec.get("expectedTabs", []),
            "requiredComponents": rec.get("requiredComponents", []),
            "forbiddenComponents": rec.get("forbiddenComponents", []),
            "keyContent": rec.get("keyContent", []),
        }
        try:
            client.create_dataset_item(
                dataset_name=dataset_name,
                id=rid,
                input=item_input,
                expected_output=expected,
                metadata={"goldenId": rid},
            )
            summary["uploaded"] += 1
            logger.info("  uploaded: %s", rid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("  failed: %s — %s", rid, exc)
            summary["error"] += 1
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-name", default="vie-golden-v1")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and validate the YAML without uploading.",
    )
    args = parser.parse_args()

    records = load_dataset()
    logger.info("Loaded %d golden videos", len(records))

    if args.dry_run:
        for r in records:
            logger.info("  %s — %s/%s (%s)",
                        r.get("id"), r.get("domain"), r.get("format"), r.get("language"))
        return 0

    summary = upload(records, args.dataset_name)
    logger.info("Summary: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
