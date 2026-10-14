"""What pipeline-1min phases 1–3 removed stays removed (the 4.1 sweep).

Each entry names the removed functions, settings or fields and the trees that
must not mention them again: a reintroduced reader reads a field no stage
writes any more, a reintroduced setting is dead config, and a reintroduced
call path is one the split extraction replaced. Historical notes in ``docs/``
and ``dev/`` are out of scope. The plan fields 1b.2 dropped have their own
guard (``test_plan_dropped_field_readers.py``); deleted settings are pinned in
``test_config.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

_SUMMARIZER_SRC = Path(__file__).resolve().parent.parent / "src"
_REPO_ROOT = _SUMMARIZER_SRC.parent.parent.parent

# Scope name → (paths under the repo root, file suffixes scanned there).
_SCOPES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "summarizer": (("services/summarizer/src",), (".py", ".txt", ".json")),
    # The modules that build and emit enrichment: elsewhere ``flashcards`` /
    # ``scenarios`` are still live tab ids (celebrations, cross-tab links).
    "enrichment": (
        (
            "services/summarizer/src/services/pipeline/enrichment.py",
            "services/summarizer/src/services/pipeline/phases/enrichment.py",
            "services/summarizer/src/models/sse_events.py",
        ),
        (".py",),
    ),
    "scripts": (("scripts", "services/summarizer/scripts"), (".py", ".sh")),
    "shared": (("packages/shared/src",), (".ts", ".json")),
    "types": (("packages/types/src",), (".ts",)),
    "api": (("api/src",), (".ts",)),
    "web": (("apps/web/src",), (".ts", ".tsx")),
    "compose": (("docker-compose.yml", "docker-compose.prod.yml"), (".yml",)),
    "env": ((".env.example", ".env.production.example"), (".example",)),
}


@dataclass(frozen=True)
class Removed:
    what: str
    pattern: str
    scopes: tuple[str, ...]


_REMOVED = (
    # 1c.2: frame annotations left clean_text; Phase 2.5 renders ctx.visual_annotations.
    Removed("inject_visual_context", r"\binject_visual_context\b", ("summarizer",)),
    # 1c.3 / 1c.5: extraction runs on LLM_EXTRACTION_MODEL; the fast-first path is gone.
    Removed(
        "fast-first extraction",
        r"\bEXTRACTION_USE_FAST_FIRST\b|\buse_fast_first\b",
        ("summarizer", "scripts", "compose", "env"),
    ),
    # 1b.1: the tier probe replaced the early classifier.
    Removed(
        "FRAME_TIER_EARLY_CLASSIFIER",
        r"\bFRAME_TIER_EARLY_CLASSIFIER\b",
        ("summarizer", "compose", "env"),
    ),
    # 1b: <video_memory> replaced the plan-reasoning video context.
    Removed("video DNA context", r"\bto_video_context_full\b|\bvideo_dna_text\b", ("summarizer",)),
    # The plan phase replaced triage; its backward-compat alias had no caller left.
    Removed("run_phase_triage alias", r"\brun_phase_triage\b", ("summarizer", "scripts")),
    # 1b.1: the classifier and its category confidence are retired.
    Removed(
        "category confidence",
        r"\bcategory_?[cC]onfidence\b",
        ("summarizer", "types", "api", "web"),
    ),
    # 1c.5: quality and coverage are metrics only; the synthesis-fed retry is gone.
    Removed(
        "synthesis-fed extraction retry",
        r"\b(?:_attempt_synthesis_fed_retry|RETRY_SCORE_THRESHOLD|retry_guidance"
        r"|force_primary_model|decide_extraction_retry|validate_extraction_counts)\b",
        ("summarizer",),
    ),
    # 1d.1: one enrich_quiz prompt replaced the per-domain enrichment map and its 11 prompts.
    Removed(
        "per-domain enrichment prompts",
        r"\b(?:ENRICHMENT_MAP|get_enrichment_map|getEnrichmentMap)\b"
        r"|\benrich_(?:fitness|food|language|music|project|recall|review|science|study|tech|travel)\b",
        ("summarizer", "scripts", "shared"),
    ),
    # 1d.1: enrichment is quiz-only — no flashcard, cheat-sheet or scenario output or reader.
    Removed(
        "flashcard / cheat-sheet / scenario enrichment output",
        r"\b(?:flashcards|scenarios|cheat_?[sS]heet)\b",
        ("enrichment",),
    ),
    Removed(
        "flashcard / cheat-sheet / scenario enrichment readers",
        r"\benrichment\.(?:flashcards|scenarios|cheatSheet)\b|\bCodeCheatSheetItem\b"
        r"|\bevent\.(?:flashcards|scenarios|cheatSheet)\b",
        ("shared", "types", "web"),
    ),
)


def _files(scope: str) -> list[Path]:
    paths, suffixes = _SCOPES[scope]
    found: list[Path] = []
    for rel in paths:
        base = _REPO_ROOT / rel
        if base.is_file():
            found.append(base)
        elif base.is_dir():
            found.extend(
                p
                for p in base.rglob("*")
                if p.suffix in suffixes and p.is_file() and "node_modules" not in p.parts
            )
    return sorted(found)


def _hits(pattern: re.Pattern[str], files: list[Path]) -> list[str]:
    return [
        f"{path.relative_to(_REPO_ROOT)}:{number}"
        for path in files
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]


_CASES = [(removed, scope) for removed in _REMOVED for scope in removed.scopes]


@pytest.mark.parametrize(
    ("removed", "scope"), _CASES, ids=[f"{r.what}-{scope}" for r, scope in _CASES]
)
def test_should_find_no_removed_symbol_when_scanning_the_tree(removed: Removed, scope: str) -> None:
    files = _files(scope)
    if not files:
        pytest.skip(f"{scope} is not in this checkout (container run)")

    assert _hits(re.compile(removed.pattern), files) == []
