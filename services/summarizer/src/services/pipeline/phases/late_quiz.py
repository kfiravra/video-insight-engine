"""The quiz joins the response after the other tabs went out (pipeline-1min 1d.1).

The quiz never blocks tabs: its call runs alongside synthesis and the moment
fill (``phases/assembly``), and assembly first emits every tab of a response
assembled WITHOUT it. Once the quiz lands, the response is assembled again
with it — the quiz_arena tab lands last (``quizPolicy.position``), a sparse
host may take a quick_quiz strip instead of a tip, links and the overview's
item count follow — and every tab that is new, moved or changed is re-sent at
its position, the way synthesis re-sends the overview.

What the late phases did to the first assembly in place is carried over: the
moment fill's frames (moment_track props). Synthesis's meta + overview patch is
re-applied by the caller.
"""

from __future__ import annotations

import json

_MOMENT_COMPONENT = "moment_track"


def _fingerprint(tab: dict) -> str:
    """The tab payload as the client received it (key order ignored)."""
    return json.dumps(tab, sort_keys=True, default=str)


def carry_moment_props(before: list[dict], after: list[dict]) -> None:
    """Keep the frames the moment fill put on ``before``'s moment tabs.

    Moment props never depend on the quiz, so ``after``'s are the unfilled copy
    of the same list; attachments live outside ``props`` and stay ``after``'s.
    """
    filled = {
        t.get("id"): t.get("props") for t in before if t.get("component") == _MOMENT_COMPONENT
    }
    for tab in after:
        if tab.get("component") == _MOMENT_COMPONENT and tab.get("id") in filled:
            tab["props"] = filled[tab.get("id")]


def positions_to_resend(before: list[dict], after: list[dict]) -> list[int]:
    """Positions in ``after`` whose tab is new, moved or changed since ``before``."""
    sent = {t.get("id"): (i, _fingerprint(t)) for i, t in enumerate(before)}
    return [i for i, tab in enumerate(after) if sent.get(tab.get("id")) != (i, _fingerprint(tab))]


def withdrawn_tab_ids(before: list[dict], after: list[dict]) -> list[str]:
    """Ids streamed from ``before`` that the final response no longer holds.

    Only a minimum-3 fallback the quiz tab made unnecessary; the client drops
    it when it refetches on ``done``.
    """
    kept = {t.get("id") for t in after}
    return [str(t.get("id")) for t in before if t.get("id") not in kept]
