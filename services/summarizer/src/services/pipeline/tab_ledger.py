"""What each ``tab_ready`` told the client — the tab emission contract (pipeline-1min 2.1).

Every pipeline ``tab_ready`` goes out through :meth:`TabLedger.event`, which
stamps ``position`` and ``final`` and remembers what was sent:

- ``final: true`` — the tab's last emission this run: nothing later changes
  its content or its position. Every tab of the final response gets exactly
  one, before ``complete`` — but a moment tab whose frames still wait for the
  720p file (``frames_pending``, output-quality 3.7): its final follows
  ``complete``, framed. Should a tab still change after it (a contract slip,
  logged as an error), a corrected final follows for the same id — the last
  final wins.
- ``final: false`` — a later phase may still change the tab, so it comes
  again (same id): the quiz re-assembly can touch every tab (cross-tab links,
  a quick_quiz attachment, the overview's tab count), a late synthesis
  patches the overview, the deferred moment fill frames the moment tabs.
  ``provisional: true`` marks a placeholder built before extraction (the
  plan-done Moments tab) — never final.
- ``replaces`` — on the first ``tab_ready`` of the tab that takes the
  provisional tab's place under ANOTHER id (a required backfill or a demoted
  tab instead of the one the plan-done copy named): the provisional id. The
  client gives that tab the provisional's slot; a tab with the provisional's
  own id replaces it by id and carries nothing.
- ``position`` — the tab's index in the response's tab order: exact on a final
  emission, an estimate before it.

:meth:`TabLedger.closing_events` is the final re-emit: after the last late
patch, every tab of the final response not yet received final goes out final
at its final position, in order (a tab still unsettled goes out non-final,
and only when it changed). A tab streamed earlier that the final
response no longer holds was withdrawn; ``complete.tabIds`` lists the final
order, so a client drops whatever it holds beyond it.

The split extraction (3.6) streams **previews** while its groups land
(``groups_pending``) and once more after the final assembly: tabs without
their links and attachments, at estimated positions — never final (A1). Once
every group landed the usual rules apply again (``groups_landed``); every tab
still gets exactly one final emission, and the closing re-emit sends the
overview (built from every tab and synthesis) last.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from src.services.pipeline.pipeline_helpers import sse_event

logger = logging.getLogger(__name__)

_OVERVIEW = "overview"
_MOMENTS = "moment_track"


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


@dataclass(frozen=True)
class OpenProvisional:
    """The provisional tab the client shows until a ``tab_ready`` takes its place."""

    tab_id: str
    component: str


@dataclass
class TabLedger:
    """Per-run record of the tabs streamed, keyed by tab id."""

    sent: dict[str, SentTab] = field(default_factory=dict)
    # Late phases still to land that change already-assembled tabs.
    quiz_pending: bool = False
    synthesis_pending: bool = False
    # A moment fill waiting for the 720p file is joined after ``complete`` (output-quality
    # 3.7, ``phases/late_moment_fill``): moment tabs stay non-final.
    frames_pending: bool = False
    # Split extraction (3.6): extraction groups still landing — nothing final.
    groups_pending: bool = False
    # Split extraction: the closing re-emit sends the overview last.
    overview_last: bool = False
    # The provisional tab still on the client, and the response's tab that
    # takes its place under another id (``track_response``).
    provisional: OpenProvisional | None = None
    superseding_id: str | None = None

    def expect_late_changes(self, *, quiz: bool, synthesis: bool, frames: bool = False) -> None:
        """Declare what the late group will change before the first tab goes out.

        ``frames``: the moment fill waits for the 720p file after ``complete`` —
        the moment tabs stay non-final until ``frames_landed``.
        """
        self.quiz_pending = quiz
        self.synthesis_pending = synthesis
        self.frames_pending = frames

    def frames_landed(self) -> None:
        """The deferred moment fill ran: the moment tabs go final at the next closing re-emit."""
        self.frames_pending = False

    def expect_groups(self) -> None:
        """Tabs will stream per landed extraction group; the overview closes the run."""
        self.groups_pending = True
        self.overview_last = True

    def groups_landed(self) -> None:
        """Every extraction group landed: finals follow the late group's rules again."""
        self.groups_pending = False

    def track_response(self, tabs: list[dict]) -> None:
        """Name the tab of ``tabs`` that takes the open provisional's place under another id.

        Nobody when ``tabs`` still holds the provisional id (that tab replaces
        it by id) or holds no tab of its component (``complete.tabIds``
        withdraws it). Called with every (re-)assembled response.
        """
        self.superseding_id = None
        open_tab = self.provisional
        if open_tab is None or any(tab_id(t) == open_tab.tab_id for t in tabs):
            return
        self.superseding_id = next(
            (tab_id(t) for t in tabs if t.get("component") == open_tab.component), None
        )

    def _settled(self, tab: dict) -> bool:
        if self.quiz_pending or self.groups_pending:
            return False
        if self.frames_pending and tab.get("component") == _MOMENTS:
            return False
        return not (self.synthesis_pending and tab.get("component") == _OVERVIEW)

    def _replaced_provisional(self, tab: dict) -> str | None:
        """The provisional id ``tab`` replaces under another id; closes the provisional."""
        open_tab = self.provisional
        if open_tab is None:
            return None
        if tab_id(tab) == open_tab.tab_id:
            self.provisional = None  # replaced by id
            return None
        if tab_id(tab) != self.superseding_id:
            return None
        self.provisional, self.superseding_id = None, None
        return open_tab.tab_id

    def event(
        self, tab: dict, position: int, *, provisional: bool = False, preview: bool = False
    ) -> str:
        """The ``tab_ready`` chunk for ``tab`` at ``position``; final when nothing can change it.

        ``preview``: a tab streamed without its links and attachments (the split
        path) — never final.
        """
        final = not (provisional or preview) and self._settled(tab)
        payload = {**tab, "position": position, "final": final}
        if provisional:
            payload["provisional"] = True
            self.provisional = OpenProvisional(tab_id(tab), str(tab.get("component", "")))
        elif (replaced := self._replaced_provisional(tab)) is not None:
            payload["replaces"] = replaced
        self.sent[tab_id(tab)] = SentTab(position, _fingerprint(tab), final)
        return sse_event("tab_ready", payload)

    def holds(self, tab: dict) -> bool:
        """Whether the last emission of ``tab``'s id carried this exact content."""
        sent = self.sent.get(tab_id(tab))
        return sent is not None and sent.fingerprint == _fingerprint(tab)

    def _received_final(self, tab: dict, position: int) -> bool:
        sent = self.sent.get(tab_id(tab))
        if sent is None or not sent.final:
            return False
        if sent == SentTab(position, _fingerprint(tab), True):
            return True
        logger.error("tab_ready contract: tab %r changed after its final emission", tab_id(tab))
        return False

    def _sent_unsettled(self, tab: dict, position: int) -> bool:
        """A tab that cannot go final yet whose last emission already carried it, here."""
        if self._settled(tab):
            return False
        return self.sent.get(tab_id(tab)) == SentTab(position, _fingerprint(tab), False)

    def closing_events(self, tabs: list[dict]) -> list[str]:
        """The final re-emit: every tab of ``tabs`` not yet received final, in order, final.

        After per-group streaming the overview goes out last: it is the one tab
        built from all the others and synthesis. While the moment frames are
        pending the moment tabs go out non-final, and only when they changed;
        once they landed the next call sends them final.
        """
        overview_last, self.overview_last = self.overview_last, False
        self.groups_landed()
        self.expect_late_changes(quiz=False, synthesis=False, frames=self.frames_pending)
        self.track_response(tabs)
        order = list(enumerate(tabs))
        if overview_last:
            order.sort(key=lambda pair: pair[1].get("component") == _OVERVIEW)
        return [
            self.event(tab, position)
            for position, tab in order
            if not self._received_final(tab, position) and not self._sent_unsettled(tab, position)
        ]

    def withdrawn(self, tabs: list[dict]) -> list[str]:
        """Ids streamed this run that the final response ``tabs`` no longer holds."""
        kept = {tab_id(tab) for tab in tabs}
        return [streamed for streamed in self.sent if streamed not in kept]
