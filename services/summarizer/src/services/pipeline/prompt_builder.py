"""Schema injection system — assembles extraction prompts from base template + domain schemas."""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

from ...config import prompts_from_disk
from ...utils.language_utils import ENGLISH_OUTPUT_DIRECTIVE
from .pipeline_helpers import sanitize_for_prompt

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"
SCHEMAS_DIR = PROMPTS_DIR / "schemas"
EXAMPLES_DIR = PROMPTS_DIR / "examples"

# Allowed schema names — alphanumeric + underscore only (no path traversal)
_SAFE_NAME_RE = re.compile(r"^[a-z0-9_]+$")

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


def _load_text(path_str: str) -> str:
    """Load a prompt file, preferring the Langfuse-registered version.

    For paths under ``PROMPTS_DIR`` we delegate to
    :func:`load_prompt_with_fallback` so the registry version (if any)
    wins. Paths outside the prompts dir bypass the registry entirely.
    """
    return load_prompt_text(Path(path_str))


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
        return _read_file_cached(str(path))
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
      * When ``PROMPT_SOURCE=disk`` (dev-only), the registry is never asked.
      * When Langfuse is disabled (no keys), the local file is used.
      * When the prompt isn't registered yet, the local file is used.
      * Any SDK exception is swallowed by ``fetch_prompt_with_obj``.

    Side effect: a successful Langfuse fetch records the full Prompt
    object via :func:`record_active_prompt` so subsequent LLM generations
    get both the ``promptVersions`` metadata field AND the native
    ``trace.generation(prompt=...)`` cross-reference in the Langfuse UI.
    Recording is explicit — callers can also call ``fetch_prompt_with_obj``
    + ``record_active_prompt`` themselves if they want different semantics.
    """
    if prompts_from_disk():
        return _read_file_cached(str(fallback_path))
    text = _try_fetch_from_registry(langfuse_name)
    if text is not None:
        return text
    return _read_file_cached(str(fallback_path))


def _try_fetch_from_registry(langfuse_name: str) -> str | None:
    """Attempt a registry fetch; record the Prompt on success. ``None`` on any failure."""
    try:
        from src.services.observability import (
            fetch_prompt_with_obj,
            record_active_prompt,
        )
    except Exception:  # noqa: BLE001 — observability import must never crash
        return None
    try:
        obj = fetch_prompt_with_obj(langfuse_name)
    except Exception:  # noqa: BLE001
        return None
    if obj is None:
        return None
    text = getattr(obj, "prompt", None)
    if not isinstance(text, str):
        return None
    try:
        record_active_prompt(langfuse_name, obj)
    except Exception:  # noqa: BLE001 — recording is best-effort
        pass
    return text


def build_tab_goals(triage_tabs: list[dict]) -> str:
    """Format triage tabs into a goal-oriented description for extraction prompt.

    Args:
        triage_tabs: Tab dicts from triage result, each with id, label, component, goal.

    Returns:
        Formatted string describing each tab's purpose for the LLM.
    """
    if not triage_tabs:
        return "Not specified — use your best judgment for tab coverage"
    lines = []
    for tab in triage_tabs:
        label = tab.get("label", tab.get("id", "unknown"))
        component = tab.get("component", "overview")
        goal = tab.get("goal", "")
        lines.append(f'Tab: "{label}" ({component}) — {goal}')
    return "\n".join(lines)


def _load_schema(name: str) -> str:
    """Load a domain or modifier schema file.

    Args:
        name: Schema name (e.g., "travel", "food", "narrative").
              Must be alphanumeric + underscore only (no path traversal).

    Returns:
        Schema text content, or empty string if file not found or name invalid.
    """
    if not _SAFE_NAME_RE.match(name):
        logger.warning("Rejected unsafe schema name: %r", name)
        return ""
    schema_path = SCHEMAS_DIR / f"{name}.txt"
    if not schema_path.exists():
        logger.warning("Schema file not found: %s", schema_path)
        return ""
    return _load_text(str(schema_path))


def _load_domain_example(tag: str) -> str:
    """Load a domain example file, falling back to learning.txt if not found.

    Args:
        tag: Domain tag (e.g., "food", "tech"). Must pass _SAFE_NAME_RE.

    Returns:
        Example text content, or learning example as fallback.
    """
    if not _SAFE_NAME_RE.match(tag):
        logger.warning("Rejected unsafe example tag: %r", tag)
        tag = "learning"
    example_path = EXAMPLES_DIR / f"{tag}.txt"
    if not example_path.exists():
        logger.info("No example for domain %r, falling back to learning", tag)
        example_path = EXAMPLES_DIR / "learning.txt"
    if not example_path.exists():
        return "No example available — follow the schema above precisely, filling every field."
    return _load_text(str(example_path))


_EMPHASIS = {
    "food": "PRIORITY: Every ingredient with exact measurement. Steps in order. Temps with units.",
    "tech": "PRIORITY: Every code snippet exactly. Variable names preserved. Commands reproducible.",
    "travel": "PRIORITY: Every location map-searchable. Costs with currency. Transport specific.",
    "fitness": "PRIORITY: Every exercise with sets/reps/rest. Form cues verbatim. Modifications included.",
    "review": "PRIORITY: All pros AND cons. Specs with numbers. Verdict unmodified.",
    "learning": "PRIORITY: All concepts with definitions. Examples preserved. Progression maintained.",
    "music": "PRIORITY: Lyrics exact or omitted. Credits complete. Genre specific.",
    "project": "PRIORITY: All materials with specs. Steps in order. Safety verbatim.",
}


def get_detail_level(duration_seconds: int) -> str:
    """Determine extraction detail level based on video duration."""
    duration_minutes = duration_seconds / 60
    if duration_minutes < 10:
        return "concise"
    elif duration_minutes < 45:
        return "standard"
    else:
        return "detailed"


def get_content_emphasis(primary_tag: str) -> str:
    """Get content emphasis instruction for the primary domain."""
    return _EMPHASIS.get(
        primary_tag, "Extract the most useful information with precision and completeness."
    )


def _build_base_template(
    content_tags: list[str],
    modifiers: list[str],
    quality_rules: str,
    title: str = "",
    duration_minutes: int = 0,
    user_goal: str = "",
    tab_goals: str = "",
    detail_level: str = "standard",
    content_emphasis: str = "",
    video_context: str = "",
    frame_context: str = "",
) -> str:
    """Build extraction prompt with domain schemas injected, {transcript} placeholder intact.

    Loads the base_extraction.txt template, then injects domain schemas for
    each content tag and modifier. Uses .replace() (not .format()) to avoid
    format-string injection from user-controlled content.

    Args:
        content_tags: List of domain tags (e.g., ["travel", "food"]).
        modifiers: List of modifier tags (e.g., ["narrative", "finance"]).
        quality_rules: Combined quality rules string.
        title: Video title.
        duration_minutes: Video duration in minutes.
        user_goal: What the viewer wants from this video.
        tab_goals: Tab goals description from triage.

    Returns:
        Prompt template with {transcript} placeholder still intact.
    """
    base_path = PROMPTS_DIR / "base_extraction.txt"
    if not base_path.exists():
        raise FileNotFoundError(f"Base extraction template not found: {base_path}")

    template = _load_text(str(base_path))

    # Build combined domain schemas
    schema_parts: list[str] = []

    for tag in content_tags:
        schema = _load_schema(tag)
        if schema:
            schema_parts.append(f"--- {tag.upper()} DOMAIN ---\n{schema}")

    for modifier in modifiers:
        schema = _load_schema(modifier)
        if schema:
            schema_parts.append(f"--- {modifier.upper()} MODIFIER ---\n{schema}")

    domain_schemas = (
        "\n\n".join(schema_parts) if schema_parts else "Use general-purpose extraction."
    )

    # Determine primary tag for example injection
    primary_tag = content_tags[0] if content_tags else "learning"
    domain_example = _load_domain_example(primary_tag)

    # Inject everything EXCEPT {transcript} — caller decides whether to fill it
    return (
        ENGLISH_OUTPUT_DIRECTIVE
        + "\n\n"
        + (
            template.replace("{domain_schemas}", domain_schemas)
            .replace("{quality_rules}", quality_rules)
            .replace("{title}", sanitize_for_prompt(title))
            .replace("{duration_minutes}", str(duration_minutes))
            .replace(
                "{user_goal}", user_goal or "Extract the most useful information from this video"
            )
            .replace(
                "{tab_goals}",
                tab_goals or "Not specified — use your best judgment for tab coverage",
            )
            .replace("{detail_level}", detail_level)
            .replace(
                "{content_emphasis}", content_emphasis or "Extract with precision and completeness."
            )
            .replace("{video_context}", video_context or "Not available")
            .replace("{frame_context}", frame_context or "No keyframe captions available.")
            .replace("{primary_tag}", primary_tag)
            .replace("{domain_example}", domain_example)
        )
    )


def build_extraction_prompt(
    content_tags: list[str],
    modifiers: list[str],
    transcript: str,
    quality_rules: str,
    title: str = "",
    duration_minutes: int = 0,
    user_goal: str = "",
    tab_goals: str = "",
    detail_level: str = "standard",
    content_emphasis: str = "",
    video_context: str = "",
    frame_context: str = "",
) -> str:
    """Assemble a complete extraction prompt from base template + domain schemas.

    Args:
        content_tags: List of domain tags (e.g., ["travel", "food"]).
        modifiers: List of modifier tags (e.g., ["narrative", "finance"]).
        transcript: Full transcript text.
        quality_rules: Combined quality rules string.
        title: Video title.
        duration_minutes: Video duration in minutes.
        user_goal: What the viewer wants from this video.
        tab_goals: Tab goals description from triage.
        detail_level: "concise", "standard", or "detailed" based on duration.
        content_emphasis: Domain-specific priority instructions.

    Returns:
        Assembled prompt string ready for LLM.
    """
    template = _build_base_template(
        content_tags,
        modifiers,
        quality_rules,
        title,
        duration_minutes,
        user_goal,
        tab_goals,
        detail_level,
        content_emphasis,
        video_context,
        frame_context,
    )
    # build_extraction_prompt is the single-shot convenience wrapper —
    # no batch context applies, so clear the placeholder explicitly.
    return template.replace("{batch_context}", "").replace("{transcript}", transcript)


def build_extraction_template(
    content_tags: list[str],
    modifiers: list[str],
    quality_rules: str,
    title: str = "",
    duration_minutes: int = 0,
    user_goal: str = "",
    tab_goals: str = "",
    detail_level: str = "standard",
    content_emphasis: str = "",
    video_context: str = "",
    frame_context: str = "",
) -> str:
    """Build extraction prompt template with {transcript} placeholder for extractor to fill.

    Similar to build_extraction_prompt() but does NOT replace {transcript}.
    The extractor injects transcript per-segment for adaptive splitting.

    Args:
        content_tags: List of domain tags (e.g., ["travel", "food"]).
        modifiers: List of modifier tags (e.g., ["narrative", "finance"]).
        quality_rules: Combined quality rules string.
        title: Video title.
        duration_minutes: Video duration in minutes.
        user_goal: What the viewer wants from this video.
        tab_goals: Tab goals description from triage.
        detail_level: "concise", "standard", or "detailed" based on duration.
        content_emphasis: Domain-specific priority instructions.

    Returns:
        Prompt template string with {transcript} placeholder intact.
    """
    return _build_base_template(
        content_tags,
        modifiers,
        quality_rules,
        title,
        duration_minutes,
        user_goal,
        tab_goals,
        detail_level,
        content_emphasis,
        video_context,
        frame_context,
    )


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
        time_str = f"{int(timestamp) // 60}:{int(timestamp) % 60:02d}"
        suffix = f" [{scene}]" if scene else ""
        lines.append(f"{time_str} — {caption}{suffix}")
    return "\n".join(lines)
