"""Prompt loading (Langfuse registry first, disk fallback) + the extraction key-frames block.

The extraction template itself is rendered by ``extraction_prompt``.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from ...config import prompts_from_disk
from ..transcript.render import format_marker
from .prompt_registry import (
    fetch_registered_prompt,
    record_registered_prompt,
    registry_matches_disk,
)

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"

# Subdirectory → Langfuse name-segment. Mirrors ``scripts/register_prompts.py``
# so the registry name we look up matches the name the uploader registered.
_SUBDIR_LABEL: dict[str, str] = {
    "schemas": "schema",
    "enrich": "enrich",
    "examples": "example",
    "detection": "detection",
}


def _langfuse_name_for(path: Path) -> str | None:
    """Map a prompts-dir path to its registered Langfuse name.

    Returns ``None`` when the path lies outside ``PROMPTS_DIR`` — those files
    are unregistered, so a registry lookup would always miss anyway.
    """
    try:
        rel = path.relative_to(PROMPTS_DIR)
    except ValueError:
        return None
    parts = rel.parts
    stem = path.stem
    if len(parts) == 1:
        return f"summarizer:{stem}"
    sub = _SUBDIR_LABEL.get(parts[0], parts[0] or "misc")
    return f"summarizer:{sub}:{stem}"


@lru_cache(maxsize=32)
def _read_file_cached(path_str: str) -> str:
    """Disk-only cached loader. Indirection point for tests."""
    return Path(path_str).read_text()


def _read_prompt_file(path_str: str) -> str:
    """Read a prompt ``.txt`` — fresh in disk mode, process-cached otherwise.

    Disk mode exists so a dev's .txt edit takes effect on the next run; the
    worker never reloads and uvicorn's reloader watches ``*.py`` only, so a
    cached read would pin the first version until a restart.
    """
    if prompts_from_disk():
        return Path(path_str).read_text()
    return _read_file_cached(path_str)


def load_prompt_text(path: Path) -> str:
    """Public registry-first loader for any prompt file under ``PROMPTS_DIR``.

    Use this from every pipeline phase that needs a prompt template — it
    is the single place that decides between Langfuse and disk, and it
    records the prompt version on the active trace so the resulting
    generation span carries the link in its metadata.

    Falls back to the in-process file cache when the path is outside
    ``PROMPTS_DIR`` (unregistered) or when the registry has no entry.
    Defensive: paths that resolve outside ``PROMPTS_DIR`` (symlink
    traversal) are rejected with a logged warning and an empty result,
    matching the prior path-safety contract enforced by
    ``enrichment._load_prompt``.
    """
    resolved = path.resolve()
    if not _is_under_prompts_dir(resolved):
        logger.warning("Prompt path outside PROMPTS_DIR rejected: %s", path)
        return ""
    langfuse_name = _langfuse_name_for(path)
    if langfuse_name is None:
        return _read_prompt_file(str(path))
    return load_prompt_with_fallback(langfuse_name=langfuse_name, fallback_path=path)


def _is_under_prompts_dir(resolved_path: Path) -> bool:
    """``True`` when ``resolved_path`` (already-resolved) lives under PROMPTS_DIR."""
    try:
        resolved_path.relative_to(PROMPTS_DIR.resolve())
    except ValueError:
        return False
    return True


def load_prompt_with_fallback(*, langfuse_name: str, fallback_path: Path) -> str:
    """Fetch a prompt from Langfuse, falling back to a local ``.txt`` file.

    The Langfuse fetch is best-effort:
      * When ``PROMPT_SOURCE=disk`` (dev-only), the registry is never asked
        and the file is re-read on every call (edits apply without a restart).
      * When Langfuse is disabled (no keys), the local file is used.
      * When the prompt isn't registered yet, the local file is used.
      * Any SDK exception is swallowed by ``fetch_prompt_with_obj``.
      * When the registry version's ``{placeholder}`` set differs from the
        local file's, the local file is used (warned once per name) — the code
        renders the file it shipped with, so deploying and registering a
        reworked prompt are safe in either order. Wording-only registry edits
        keep the same slots and still win.

    Side effect: when the registry text is used, the full Prompt object is
    recorded via :func:`record_active_prompt` so subsequent LLM generations
    get both the ``promptVersions`` metadata field AND the native
    ``trace.generation(prompt=...)`` cross-reference in the Langfuse UI.
    Recording is explicit — callers can also call ``fetch_prompt_with_obj``
    + ``record_active_prompt`` themselves if they want different semantics.
    """
    if prompts_from_disk():
        return _read_prompt_file(str(fallback_path))
    fetched = fetch_registered_prompt(langfuse_name)
    if fetched is None:
        return _read_file_cached(str(fallback_path))
    text, prompt_obj = fetched
    disk_text = _shipped_text_or_none(fallback_path)
    if disk_text is not None and not registry_matches_disk(langfuse_name, text, disk_text):
        return disk_text
    record_registered_prompt(langfuse_name, prompt_obj)
    return text


def _shipped_text_or_none(path: Path) -> str | None:
    """The process-cached local file; ``None`` when unreadable (registry text then wins)."""
    try:
        return _read_file_cached(str(path))
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug("No readable local prompt at %s for the placeholder check: %s", path, exc)
        return None


# Keep the frame block bounded — a long, low-signal list crowds the transcript
# and inflates token cost. 12 captioned frames is enough to ground visual
# claims and decide whether a filmstrip/diagram is warranted.
_FRAME_CONTEXT_MAX = 12
_FRAME_CAPTION_MAX = 90
_FRAME_MATCH_TOLERANCE = 5.0  # seconds


def _resolve_frame_caption(
    frame: dict,
    timestamp: float,
    descriptions: list[dict],
) -> tuple[str, str]:
    """Pick the best caption + scene_type for a frame.

    Vision description (matched by timestamp within tolerance) wins; OCR text is
    the fallback. Returns ``("", "")`` when neither is available.
    """
    best: dict | None = None
    best_dist = _FRAME_MATCH_TOLERANCE
    for desc in descriptions:
        dist = abs(desc.get("timestamp_sec", 0) - timestamp)
        if dist <= best_dist:
            best_dist = dist
            best = desc
    if best:
        caption = str(best.get("content") or "").strip()
        if caption:
            return caption, str(best.get("scene_type") or "").strip()
    return str(frame.get("ocr_text") or "").strip(), ""


def format_gallery_frames_for_extraction(
    gallery_frames: list[dict],
    frame_descriptions: list[dict] | None = None,
) -> str:
    """Render up to 12 captioned gallery frames as prompt lines.

    Each line is ``"M:SS — caption [scene_type]"``. Frames without any caption
    (no vision description, no OCR) are skipped — a bare timestamp adds no
    signal. Returns ``""`` when nothing usable is available.
    """
    if not gallery_frames:
        return ""
    descriptions = frame_descriptions or []
    lines: list[str] = []
    for frame in sorted(gallery_frames, key=lambda f: f.get("timestamp", 0)):
        if len(lines) >= _FRAME_CONTEXT_MAX:
            break
        timestamp = frame.get("timestamp", 0) or 0
        caption, scene = _resolve_frame_caption(frame, timestamp, descriptions)
        if not caption:
            continue
        caption = caption[:_FRAME_CAPTION_MAX]
        time_str = format_marker(timestamp)[1:-1]
        suffix = f" [{scene}]" if scene else ""
        lines.append(f"{time_str} — {caption}{suffix}")
    return "\n".join(lines)
