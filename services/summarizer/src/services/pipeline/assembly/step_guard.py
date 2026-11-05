"""A step list with no step text never ships (step-player-empty hotfix, 2026-11-05).

nz4F6hM7S9E (dev, 2026-11-05): the plan put a "Build It Locally" step_player on
``project.steps``; plan-parse reconcile re-pointed it to ``tech.snippets`` (project was
not tagged, and the registry lists step_player among that path's components), and the
step adapter read none of a snippet's keys, so six steps shipped with ``instruction:
""``, only their timestamps set. The adapter now reads a snippet's ``explanation``
(``normalizers.STEP_TEXT_KEYS``) and skips a step with no text. When no item of the
list has any, :func:`empty_steps_drop` names the drop, and core records it as
``empty_steps`` (logged, listed in ``droppedTabs``) instead of walking the demote
ladder, whose terminal display_section would ship the raw items.
"""

from __future__ import annotations

import logging
from typing import Any

from .normalizers import step_text

logger = logging.getLogger(__name__)

EMPTY_STEPS = "empty_steps"
# step_flow_canvas assembles through assemble_step_player: the same contract.
STEP_COMPONENTS = frozenset({"step_player", "step_flow_canvas"})


def _has_text(item: Any) -> bool:
    if isinstance(item, str):
        return bool(item.strip())
    return isinstance(item, dict) and bool(step_text(item))


def empty_steps_drop(
    tab_id: str, component: str, data_source: str, data: Any
) -> tuple[str, str] | None:
    """``(EMPTY_STEPS, detail)`` for a step tab whose list holds items but no step text.

    None for any other component, for data that is not a non-empty list, and for a list
    with at least one step that has text (the assembler ships those).
    """
    if component not in STEP_COMPONENTS or not isinstance(data, list) or not data:
        return None
    if any(_has_text(item) for item in data):
        return None
    logger.warning(
        "TAB DROPPED: id=%r, component=%r, dataSource=%r — %d steps, none with text (%s)",
        tab_id,
        component,
        data_source,
        len(data),
        EMPTY_STEPS,
    )
    return EMPTY_STEPS, f"{len(data)} steps without text"
