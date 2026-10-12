"""What each ``tab_ready`` told the client — the tab emission contract (pipeline-1min 2.1).

Every pipeline ``tab_ready`` goes out through :meth:`TabLedger.event`, which
stamps ``position`` and ``final`` and remembers what was sent:

- ``final: true`` — the tab's last emission this run: nothing later changes
  its content or its position. Every tab of the final response gets exactly
  one, before ``complete``.
- ``final: false`` — a later phase may still change the tab, so it comes
  again (same id): the quiz re-assembly can touch every tab (cross-tab links,
  a quick_quiz attachment, the overview's tab count) and a late synthesis
  patches the overview. ``provisional: true`` marks a placeholder built before
  extraction (the plan-done Moments tab) — never final.
- ``position`` — the tab's index in the response's tab order: exact on a final
  emission, an estimate before it.

:meth:`TabLedger.closing_events` is the final re-emit: after the last late
patch, every tab of the final response not yet received final goes out final
at its final position, in order. A tab streamed earlier that the final
response no longer holds was withdrawn; ``complete.tabIds`` lists the final
order, so a client drops whatever it holds beyond it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from src.services.pipeline.pipeline_helpers import sse_event

logger = logging.getLogger(__name__)

_OVERVIEW = "overview"


def _fingerprint(tab: dict) -> str:
    """The tab payload as the client received it (key order ignored)."""
    return json.dumps(tab, sort_keys=True, default=str)


def tab_id(tab: dict) -> str:
    return str(tab.get("id", ""))


@dataclass(frozen=True)
class SentTab:
    """The last ``tab_ready`` of one tab id."""

    position: int
    fingerprint: str
    final: bool


@dataclass
class TabLedger:
    """Per-run record of the tabs streamed, keyed by tab id."""

    sent: dict[str, SentTab] = field(default_factory=dict)
    # Late phases still to land that change already-assembled tabs.
    quiz_pending: bool = False
    synthesis_pending: bool = False

    def expect_late_changes(self, *, quiz: bool, synthesis: bool) -> None:
        """Declare what the late group will change before the first tab goes out."""
        self.quiz_pending = quiz
        self.synthesis_pending = synthesis

    def _settled(self, tab: dict) -> bool:
        if self.quiz_pending:
            return False
        return not (self.synthesis_pending and tab.get("component") == _OVERVIEW)

    def event(self, tab: dict, position: int, *, provisional: bool = False) -> str:
        """The ``tab_ready`` chunk for ``tab`` at ``position``; final when nothing can change it."""
        final = not provisional and self._settled(tab)
        payload = {**tab, "position": position, "final": final}
        if provisional:
            payload["provisional"] = True
        self.sent[tab_id(tab)] = SentTab(position, _fingerprint(tab), final)
        return sse_event("tab_ready", payload)

    def _received_final(self, tab: dict, position: int) -> bool:
        sent = self.sent.get(tab_id(tab))
        if sent is None or not sent.final:
            return False
        if sent == SentTab(position, _fingerprint(tab), True):
            return True
        logger.error("tab_ready contract: tab %r changed after its final emission", tab_id(tab))
        return False

    def closing_events(self, tabs: list[dict]) -> list[str]:
        """The final re-emit: every tab of ``tabs`` not yet received final, in order, final."""
        self.expect_late_changes(quiz=False, synthesis=False)
        return [
            self.event(tab, position)
            for position, tab in enumerate(tabs)
            if not self._received_final(tab, position)
        ]

    def withdrawn(self, tabs: list[dict]) -> list[str]:
        """Ids streamed this run that the final response ``tabs`` no longer holds."""
        kept = {tab_id(tab) for tab in tabs}
        return [streamed for streamed in self.sent if streamed not in kept]
