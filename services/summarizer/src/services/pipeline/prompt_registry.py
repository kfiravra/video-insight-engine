"""Registry side of the prompt loader — fetch, placeholder guard, trace link.

Templates render through chained ``str.replace("{name}", value)`` calls, so a
template's slots are its ``{lower_snake}`` tokens. ``{{``/``}}`` are the
templates' literal-brace convention (JSON examples) and never name a slot.

The placeholder guard exists for deploy order: the code ships with the ``.txt``
it renders, while the registry's ``production`` label moves on its own
schedule. A registry version written for older (or newer) code has a different
slot set, and the renderer would silently leave slots unfilled or drop inputs.

The guard compares slot sets only, so it catches slot drift and nothing else.
A wording-only change to a shipped ``.txt`` (schemas, examples, the toolkit,
any stage prompt) keeps the same slots: the registry's ``production`` version
— the OLD wording — keeps being served until the file is registered.
Registration is therefore a deploy step, never optional: ``vie-langfuse-init``
re-registers every prompt (``scripts/register_prompts.py --commit``) on
``docker compose up``; a deploy that skips it must run the script itself.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
_ESCAPED_BRACE_RE = re.compile(r"\{\{|\}\}")

# Prompt names already warned about — the loader runs on every stage call, so
# a drifted registry version would otherwise log on every pipeline run.
_DRIFT_WARNED: set[str] = set()


def fetch_registered_prompt(langfuse_name: str) -> tuple[str, object] | None:
    """Registry fetch → ``(text, Prompt object)``; ``None`` on a miss or any failure."""
    try:
        from src.services.observability import fetch_prompt_with_obj
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
    return text, obj


def record_registered_prompt(langfuse_name: str, prompt_obj: object) -> None:
    """Link the registry version to the active trace — call only when its text is used."""
    try:
        from src.services.observability import record_active_prompt

        record_active_prompt(langfuse_name, prompt_obj)
    except Exception as exc:  # noqa: BLE001 — recording is best-effort
        logger.debug("Prompt version recording failed for %s: %s", langfuse_name, exc)


@lru_cache(maxsize=64)
def declared_placeholders(text: str) -> frozenset[str]:
    """The ``{name}`` slots ``text`` declares; ``{{``/``}}`` escapes are literal braces.

    Cached because the same registry/disk texts are re-checked on every load.
    """
    return frozenset(_PLACEHOLDER_RE.findall(_ESCAPED_BRACE_RE.sub("", text)))


def registry_matches_disk(prompt_name: str, registry_text: str, disk_text: str) -> bool:
    """``True`` when the registry text declares the same slots as the shipped file.

    On a mismatch, warns once per ``prompt_name`` with the differing slot
    names (never prompt content) and returns ``False`` so the caller renders
    the shipped file instead.
    """
    if registry_text == disk_text:
        return True
    registry_slots = declared_placeholders(registry_text)
    disk_slots = declared_placeholders(disk_text)
    if registry_slots == disk_slots:
        return True
    _warn_drift_once(prompt_name, registry_slots, disk_slots)
    return False


def _warn_drift_once(
    prompt_name: str, registry_slots: frozenset[str], disk_slots: frozenset[str]
) -> None:
    if prompt_name in _DRIFT_WARNED:
        return
    _DRIFT_WARNED.add(prompt_name)
    missing = sorted(disk_slots - registry_slots)
    unexpected = sorted(registry_slots - disk_slots)
    logger.warning(
        "prompt_registry.placeholder_drift prompt=%s missing_in_registry=%s "
        "extra_in_registry=%s — using the deployed file",
        prompt_name,
        missing,
        unexpected,
        extra={
            "prompt_name": prompt_name,
            "missing_in_registry": missing,
            "extra_in_registry": unexpected,
        },
    )
