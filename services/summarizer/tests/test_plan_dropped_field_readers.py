"""No reader of the plan fields 1b.2 dropped is left (pipeline-1min 1b.2).

The plan no longer emits ``reasoning``, ``outboundLinks``, ``identity.audience``
or ``itemCounts``. A reader left behind would silently read nothing, so the
names must be gone from the summarizer source and prompts — and from the API,
web app and shared packages, which never read them (checked when present in
this checkout).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_SUMMARIZER_SRC = Path(__file__).resolve().parent.parent / "src"
_REPO_ROOT = _SUMMARIZER_SRC.parent.parent.parent

# Plan-only names: any hit is a reader (or writer) of a dropped field.
_DROPPED_NAMES = re.compile(r"\b(?:item_?[cC]ounts|ItemCounts|outbound_?[lL]inks)\b")
# ``audience`` was only ever read as an attribute or key of the plan identity.
_AUDIENCE_READ = re.compile(r"\.audience\b|[\"']audience[\"']")
# Modules that model, render or consume the plan output: they never name
# ``reasoning`` (the classifier's own ``reasoning`` is a different stage).
_PLAN_MODULES = (
    "models/pipeline_types.py",
    "services/pipeline/plan.py",
    "services/pipeline/plan_prompt.py",
    "services/pipeline/post_processor.py",
    "services/pipeline/assembly/core.py",
    "services/pipeline/assembly/cross_tab.py",
    "prompts/plan.txt",
)
_OTHER_SERVICES = ("api/src", "apps/web/src", "packages/types/src", "packages/shared/src")


def _hits(pattern: re.Pattern[str], files: list[Path]) -> list[str]:
    return [
        f"{path.relative_to(_REPO_ROOT)}:{number}"
        for path in files
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]


def _summarizer_files() -> list[Path]:
    return sorted(
        p for p in _SUMMARIZER_SRC.rglob("*") if p.suffix in (".py", ".txt") and p.is_file()
    )


def test_should_find_no_dropped_plan_field_when_scanning_summarizer_source() -> None:
    assert _hits(_DROPPED_NAMES, _summarizer_files()) == []


def test_should_find_no_audience_read_when_scanning_summarizer_source() -> None:
    python_files = [p for p in _summarizer_files() if p.suffix == ".py"]

    assert _hits(_AUDIENCE_READ, python_files) == []


@pytest.mark.parametrize("module", _PLAN_MODULES)
def test_should_not_name_reasoning_when_module_handles_the_plan(module: str) -> None:
    assert _hits(re.compile(r"\breasoning\b"), [_SUMMARIZER_SRC / module]) == []


@pytest.mark.parametrize("root", _OTHER_SERVICES)
def test_should_find_no_dropped_plan_field_when_scanning_other_services(root: str) -> None:
    base = _REPO_ROOT / root
    if not base.is_dir():
        pytest.skip(f"{root} is not in this checkout")
    files = [
        p
        for p in base.rglob("*")
        if p.suffix in (".ts", ".tsx", ".json") and p.is_file() and "node_modules" not in p.parts
    ]

    assert _hits(_DROPPED_NAMES, files) == []
