#!/usr/bin/env python3
"""Sync local prompt templates to the Langfuse prompt registry.

Walks the summarizer's `prompts/` tree plus the assistant's
`prompt_templates.py` and uploads each prompt under a stable name::

    summarizer:base_extraction
    summarizer:schema:food
    summarizer:enrich:enrich_study
    summarizer:detection:language_detect
    assistant:rag_system

Idempotency strategy
--------------------
Langfuse versions prompts on every ``create_prompt`` call. We avoid creating
no-op versions by comparing the local content to the server version that
carries the target label. A 404 means "not there yet" (upload); any other
lookup failure reports ``unknown`` in a dry run and still uploads on commit.
The exit code is 1 when any prompt is ``unknown`` or failed to upload.

Run modes
---------
``--dry-run`` prints what would change and uploads nothing. With Langfuse
keys in the environment it reads the labelled versions to tell
``unchanged`` from ``would-upload``; without keys every prompt is listed as
``would-upload``. ``--commit`` uploads. Default is ``--dry-run`` so the
script is safe to copy/paste from docs.

``--label NAME`` (default ``production``) is the label uploads receive and
the idempotency check compares against. A non-production label stages new
prompt text in a project without moving what the runtime serves (the
runtime fetches ``production``); re-run with the default label to promote.

Local run::

    python3 scripts/register_prompts.py --dry-run
    LANGFUSE_PUBLIC_KEY=pk LANGFUSE_SECRET_KEY=sk \\
        python3 scripts/register_prompts.py --commit [--label staging]
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

# Make the summarizer source tree importable when invoked from anywhere.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SUMMARIZER_SRC = _REPO_ROOT / "services" / "summarizer"
_ASSISTANT_SRC = _REPO_ROOT / "services" / "assistant"
sys.path.insert(0, str(_SUMMARIZER_SRC))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("register_prompts")


# Service name → root directory containing .txt files. Sub-folders become
# the second segment of the prompt name (e.g. ``schemas/food.txt`` →
# ``summarizer:schema:food``). Unknown folders use ``misc``.
PROMPT_ROOTS: dict[str, Path] = {
    "summarizer": _SUMMARIZER_SRC / "src" / "prompts",
}

_SUBDIR_LABEL: dict[str, str] = {
    "schemas": "schema",
    "enrich": "enrich",
    "examples": "example",
    "detection": "detection",
}

# Defensive guardrails to keep accidental files (renamed .env, secrets,
# dumps) from being shipped to Langfuse. Any rejected file is logged and
# skipped — we do not partially upload.
_MAX_PROMPT_BYTES = 200_000
_SECRET_PROBE_RE = re.compile(
    r"\b("
    r"AKIA[A-Z0-9]{16}|ASIA[A-Z0-9]{16}|"
    r"sk-ant-[A-Za-z0-9_\-]{20,}|"
    r"sk-(?!ant-)[A-Za-z0-9_\-]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|"
    r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}|"
    r"LANGFUSE_SECRET_KEY|"
    r"ANTHROPIC_API_KEY|"
    r"OPENAI_API_KEY"
    r")\b",
    re.IGNORECASE,
)

# The runtime fetches prompts by this label (Langfuse's default for
# ``get_prompt``), so only uploads carrying it change what is served.
DEFAULT_LABEL = "production"
# Langfuse's label charset. ``latest`` is assigned by Langfuse to every new
# version, so it cannot serve as a sync target.
_LABEL_RE = re.compile(r"^[a-z0-9_.-]+$")
_RESERVED_LABELS = frozenset({"latest"})


class PromptRegistry(Protocol):
    """The two Langfuse client calls the sync needs (narrow, so tests can fake it)."""

    def get_prompt(self, name: str, *, label: str | None = None) -> object: ...

    def create_prompt(self, *, name: str, prompt: str, labels: list[str]) -> object: ...


def _parse_label(value: str) -> str:
    """argparse ``type`` for ``--label``: reject names Langfuse would refuse or manages itself."""
    if not _LABEL_RE.match(value):
        raise argparse.ArgumentTypeError(
            f"invalid label {value!r}: use lowercase letters, digits, '_', '-' or '.'"
        )
    if value in _RESERVED_LABELS:
        raise argparse.ArgumentTypeError(f"label {value!r} is managed by Langfuse")
    return value


@dataclass(frozen=True)
class PromptRecord:
    """One prompt that needs syncing."""

    name: str
    content: str
    source_path: Path


def _is_safe_to_upload(content: str, path: Path) -> tuple[bool, str]:
    """Return ``(ok, reason)`` for a candidate prompt file.

    Rejects files that exceed the size cap (probable dump file), are
    empty (whitespace-only), or contain strings that look like leaked
    secrets / env-var names. The check is conservative — false positives
    are fine; false negatives ship real secrets to Langfuse.
    """
    if len(content) == 0 or content.strip() == "":
        return False, "empty file"
    if len(content) > _MAX_PROMPT_BYTES:
        return False, f"exceeds {_MAX_PROMPT_BYTES}-byte cap (got {len(content)})"
    match = _SECRET_PROBE_RE.search(content)
    if match:
        return False, f"matched secret pattern near {match.group(1)[:8]}…"
    return True, "ok"


def _label_for(parent: str) -> str:
    return _SUBDIR_LABEL.get(parent, parent or "misc")


def _name_for(service: str, root: Path, file_path: Path) -> str:
    rel = file_path.relative_to(root)
    parts = rel.parts
    stem = file_path.stem
    if len(parts) == 1:
        return f"{service}:{stem}"
    sub = _label_for(parts[0])
    return f"{service}:{sub}:{stem}"


def discover_prompts() -> list[PromptRecord]:
    """Return every prompt file the script knows how to register.

    Files that fail :func:`_is_safe_to_upload` are skipped with a warning
    rather than aborting the run — that way an obviously-bad file doesn't
    block routine syncs of the rest of the registry.
    """
    records: list[PromptRecord] = []
    for service, root in PROMPT_ROOTS.items():
        if not root.exists():
            logger.warning("Prompt root missing: %s", root)
            continue
        for path in sorted(root.rglob("*.txt")):
            content = path.read_text(encoding="utf-8")
            ok, reason = _is_safe_to_upload(content, path)
            if not ok:
                logger.warning("Skipping %s — %s", path, reason)
                continue
            name = _name_for(service, root, path)
            records.append(PromptRecord(name=name, content=content, source_path=path))

    # Assistant prompts live in code, not files. Read them out by name.
    assistant_prompts = _load_assistant_prompts()
    for name, content in assistant_prompts.items():
        ok, reason = _is_safe_to_upload(content, _ASSISTANT_SRC)
        if not ok:
            logger.warning("Skipping assistant prompt %s — %s", name, reason)
            continue
        records.append(
            PromptRecord(
                name=f"assistant:{name}",
                content=content,
                source_path=_ASSISTANT_SRC / "src" / "utils" / "prompt_templates.py",
            )
        )
    return records


def _load_assistant_prompts() -> dict[str, str]:
    """Pull constant string prompts out of the assistant's prompt_templates.

    The assistant module isn't importable from outside its own service tree,
    so we open the file and pluck the top-level ``X = "..."`` strings via AST
    parsing. This avoids dragging the whole assistant dependency graph into
    the registration script.
    """
    import ast

    src = _ASSISTANT_SRC / "src" / "utils" / "prompt_templates.py"
    if not src.exists():
        return {}
    try:
        tree = ast.parse(src.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        logger.warning("Failed to parse assistant prompt_templates.py: %s", exc)
        return {}

    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            continue
        # Convention: only export SCREAMING_SNAKE_CASE constants.
        if target.id != target.id.upper():
            continue
        out[target.id.lower()] = node.value.value
    return out


SyncState = Literal["unchanged", "changed", "unknown"]

# Sync results that mean the run did not do (or could not check) its job.
_FAILED_RESULTS = ("unknown", "error")


def _is_not_found(exc: Exception) -> bool:
    """True for the registry's 404 — the prompt or label doesn't exist yet."""
    return getattr(exc, "status_code", None) == 404


def _sync_state(
    client: PromptRegistry, name: str, content: str, *, label: str = DEFAULT_LABEL
) -> SyncState:
    """Compare local content with the server version carrying ``label``.

    Scoped to the label so a content-equal version that lacks it reports as
    ``changed`` — the runtime fetches prompts by label, so an unlabelled match
    is useless. A 404 (prompt or label not there yet) is ``changed`` too: an
    upload creates it. Any other lookup failure (bad keys / 401, network, 5xx)
    is ``unknown``: the script can't tell, so a dry run must not report it as a
    planned upload.
    """
    try:
        existing = client.get_prompt(name, label=label)
    except Exception as exc:  # noqa: BLE001 — classified: 404 vs anything else
        if _is_not_found(exc):
            return "changed"
        logger.warning("Lookup failed for %s: %s", name, exc)
        return "unknown"
    existing_text = getattr(existing, "prompt", None)
    if isinstance(existing_text, str) and existing_text.strip() == content.strip():
        return "unchanged"
    return "changed"


def upload_prompt(
    client: PromptRegistry | None,
    record: PromptRecord,
    *,
    dry_run: bool,
    label: str = DEFAULT_LABEL,
) -> str:
    """Sync one prompt; return one of ``unchanged|would-upload|unknown|updated|error``.

    A dry run never uploads: with a client it reads the labelled version to
    tell ``unchanged`` from ``would-upload`` (``unknown`` when the lookup
    failed); without one (no keys) it cannot compare and reports
    ``would-upload``. A commit uploads whatever isn't known to be unchanged.
    """
    if client is None:
        return "would-upload"
    state = _sync_state(client, record.name, record.content, label=label)
    if state == "unchanged":
        return "unchanged"
    if dry_run:
        return "unknown" if state == "unknown" else "would-upload"
    try:
        client.create_prompt(name=record.name, prompt=record.content, labels=[label])
        return "updated"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Upload failed for %s: %s", record.name, exc)
        return "error"


def _build_client(*, dry_run: bool) -> PromptRegistry | None:
    """Initialize a Langfuse client, or return None when there is nothing to talk to.

    Returns ``None`` when ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` are
    blank — this is the "observability not configured" path and is normal for
    fresh dev setups. ``main()`` short-circuits a commit on ``None`` and exits
    0, so when this script runs as a one-shot init container it won't block
    downstream services for users who haven't signed up for Langfuse. A dry
    run also tolerates a missing SDK (host-side previews with a bare
    ``python3``) and then compares nothing.
    """
    public = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret = os.environ.get("LANGFUSE_SECRET_KEY")
    host = os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    if not public or not secret:
        outcome = "dry run compares nothing" if dry_run else "skipping prompt sync"
        logger.info("Langfuse not configured (LANGFUSE_PUBLIC_KEY/SECRET_KEY blank) — %s", outcome)
        return None
    try:
        from langfuse import Langfuse  # type: ignore
    except ImportError as exc:
        if not dry_run:
            sys.exit(f"Langfuse SDK is not installed: {exc}")
        logger.warning("Langfuse SDK is not installed (%s) — dry run compares nothing", exc)
        return None
    return Langfuse(public_key=public, secret_key=secret, host=host)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--commit",
        action="store_true",
        help="Actually upload to Langfuse.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change and upload nothing (the default).",
    )
    parser.add_argument(
        "--filter",
        default="",
        help="Only sync prompts whose name contains this substring.",
    )
    parser.add_argument(
        "--label",
        type=_parse_label,
        default=DEFAULT_LABEL,
        help=(
            f"Label uploads receive and the idempotency check compares against "
            f"(default: {DEFAULT_LABEL}). Any other label stages prompts without "
            f"changing what the runtime serves."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dry_run = not args.commit

    records = discover_prompts()
    if args.filter:
        records = [r for r in records if args.filter in r.name]
    if not records:
        logger.warning("No prompts discovered — nothing to do")
        return 0

    logger.info("Discovered %d prompts (dry_run=%s, label=%s)", len(records), dry_run, args.label)
    client = _build_client(dry_run=dry_run)
    if not dry_run and client is None:
        # _build_client logged the reason (e.g. Langfuse disabled). Exit 0 so
        # the init container doesn't block downstream services in compose.
        return 0

    summary: dict[str, int] = {}
    for record in records:
        result = upload_prompt(client, record, dry_run=dry_run, label=args.label)
        summary[result] = summary.get(result, 0) + 1
        logger.info("  [%-12s] %s (%d bytes)", result, record.name, len(record.content))

    logger.info("Summary: %s", summary)
    # Non-zero when any prompt failed to sync or couldn't be checked, so a
    # caller (activate_langfuse.sh, CI) never mistakes it for a clean run.
    return 1 if any(summary.get(result) for result in _FAILED_RESULTS) else 0


if __name__ == "__main__":
    raise SystemExit(main())
