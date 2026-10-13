"""Offline views of the model A/B round 2 (pipeline-1min 4.0d, D30): raw tables, reviewer dumps.

Nothing is sent and nothing outside the cache dir is written. Both views read
round 2's answers (``<cache-dir>/answers.jsonl``) with the request block the
live run saved (``<cache-dir>/dry-run.json``) and round 1's answers to the same
requests (``gates/g4-model-ab.json``), scored together (``_model_ab_r2_rows``).

* ``tables`` — the raw per-arm tables (``raw-tables.md`` + ``raw-rows.json``).
* ``review`` — per cassette ``review/<vid>.json`` + ``.md`` for the reviewer
  agents (the transcript comes from a fresh offline capture of the cassettes).
* ``report`` — once the reviewers' ``review/<vid>.classified.json`` exist: the
  round-2 block (``_model_ab_r2_report``) goes into ``gates/g4-model-ab.json``
  and the whole report is re-rendered by ``model_refresh_ab.py --rescore`` —
  round 1 re-scored (true median, picks kept), Round 2 appended after it.

Usage (from the repo root)::

    services/summarizer/.venv/bin/python scripts/model_ab_r2_offline.py tables
    services/summarizer/.venv/bin/python scripts/model_ab_r2_offline.py review
    services/summarizer/.venv/bin/python scripts/model_ab_r2_offline.py report
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SUMMARIZER_DIR = _REPO_ROOT / "services" / "summarizer"
for _path in (_SUMMARIZER_DIR, Path(__file__).resolve().parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import model_refresh_ab  # noqa: E402
from _model_ab_capture import CASSETTE_VIDEOS, capture_cassettes  # noqa: E402
from _model_ab_r2_report import build_round2  # noqa: E402
from _model_ab_r2_review import dump_sizes, review_dump, write_review  # noqa: E402
from _model_ab_r2_rows import Row, combined_rows, memory_pairs, round1_results  # noqa: E402
from _model_ab_r2_tables import render_raw, spend_of, summarize_arms  # noqa: E402
from _model_ab_runner import spent_usd  # noqa: E402
from model_refresh_ab_r2 import DEFAULT_CACHE_DIR, ROUND1_JSON  # noqa: E402


def _rows(args: argparse.Namespace) -> list[Row]:
    info = json.loads((args.cache_dir / "dry-run.json").read_text(encoding="utf-8"))["requests"]
    round1 = round1_results(args.round1_json, frozenset(info))
    return combined_rows(info, round1, args.cache_dir / "answers.jsonl")


def tables(args: argparse.Namespace) -> int:
    """Write and print the raw per-arm tables."""
    rows = _rows(args)
    spend = spend_of(rows, spent_usd(args.cache_dir / "answers.jsonl"))
    markdown = render_raw(rows, spend)
    payload = {
        "spend": spend,
        "summary": summarize_arms(rows),
        "memory_pairs": memory_pairs(rows),
        "rows": rows,
    }
    (args.cache_dir / "raw-tables.md").write_text(markdown, encoding="utf-8")
    (args.cache_dir / "raw-rows.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(markdown)
    return 0


def review(args: argparse.Namespace) -> int:
    """Write each cassette's reviewer dump and print its size."""
    rows = _rows(args)
    requests = [r for r in capture_cassettes() if r.call_type == "extraction"]
    for video_id in CASSETTE_VIDEOS:
        dump = review_dump(video_id, requests, rows)
        as_json, as_md = write_review(args.cache_dir / "review", dump)
        sizes = dump_sizes(dump) | {
            "json_bytes": as_json.stat().st_size,
            "md_bytes": as_md.stat().st_size,
        }
        print(f"{video_id}: {as_json} + {as_md.name} {json.dumps(sizes)}")
    return 0


def report(args: argparse.Namespace) -> int:
    """Put the round-2 block into the gate JSON, then re-render the report through --rescore."""
    block = build_round2(args.cache_dir, args.round1_json, args.benchmark, args.ceiling_note)
    payload = json.loads(args.round1_json.read_text(encoding="utf-8"))
    payload["round2"] = block
    args.round1_json.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    out_md = args.round1_json.with_suffix(".md")
    return model_refresh_ab.main(
        ["--rescore", "--out-json", str(args.round1_json), "--out-md", str(out_md)]
    )


_VIEWS = {"tables": tables, "review": review, "report": report}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("view", choices=tuple(_VIEWS))
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--round1-json", type=Path, default=ROUND1_JSON)
    parser.add_argument(
        "--benchmark", type=Path, default=ROUND1_JSON.parent / "g3-prod-benchmark.json"
    )
    parser.add_argument(
        "--ceiling-note",
        default="",
        help='how the ceiling was set, e.g. "raised by Kfir from $1.00"',
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(name)s: %(message)s")
    return _VIEWS[args.view](args)


if __name__ == "__main__":
    sys.exit(main())
