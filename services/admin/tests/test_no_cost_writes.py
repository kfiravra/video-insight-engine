"""vie-admin no longer writes the dollar cost collections.

The credit grant moved to vie-api's minutes ledger; ``userCosts`` and
``userCostAdjustments`` are read-only history here. This scans the service's
source so a write path cannot come back unnoticed.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
COST_COLLECTIONS = r"(?:userCosts|userCostAdjustments)"
WRITE_METHODS = (
    r"(?:insert_one|insert_many|update_one|update_many|replace_one|delete_one|delete_many"
    r"|find_one_and_update|find_one_and_replace|find_one_and_delete|bulk_write|drop|rename)"
)
# db.userCosts.update_one(...) and db["userCosts"].update_one(...)
WRITE_PATTERN = re.compile(
    rf"(?:\.{COST_COLLECTIONS}|\[['\"]{COST_COLLECTIONS}['\"]\])\s*\.\s*{WRITE_METHODS}\b"
)
# the collection fetched into a variable, then written through it
ALIAS_PATTERN = re.compile(
    rf"=\s*\w+(?:\.{COST_COLLECTIONS}|\[['\"]{COST_COLLECTIONS}['\"]\])\s*$", re.M
)


def _sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_the_scan_sees_the_service_source() -> None:
    assert any(path.name == "users.py" for path in _sources())


def test_no_source_file_writes_the_cost_collections() -> None:
    offenders = [
        f"{path.relative_to(SRC)}:{match.group(0).strip()}"
        for path in _sources()
        for match in WRITE_PATTERN.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_no_source_file_aliases_a_cost_collection_for_writing() -> None:
    offenders = [
        str(path.relative_to(SRC))
        for path in _sources()
        if ALIAS_PATTERN.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_the_scan_pattern_would_catch_the_removed_write() -> None:
    removed = 'await db.userCosts.update_one({"userId": u}, {"$inc": {"creditAdjustmentUsd": -1}})'

    assert WRITE_PATTERN.search(removed) is not None


def test_the_scan_pattern_would_catch_a_bracket_style_write() -> None:
    assert WRITE_PATTERN.search('db["userCostAdjustments"].insert_one(doc)') is not None


def test_reads_are_still_allowed() -> None:
    assert WRITE_PATTERN.search("db.userCosts.aggregate(pipeline).to_list(10)") is None
    assert WRITE_PATTERN.search('db.userCostAdjustments.find({"userId": u})') is None
