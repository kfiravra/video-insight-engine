"""The quiz joins the response after the other tabs went out (pipeline-1min 1d.1).

The quiz never blocks tabs: its call runs alongside synthesis and the moment
fill (``phases/assembly``), and assembly first emits every tab of a response
assembled WITHOUT it — non-final, since the quiz may still change them. Once
the quiz lands, the response is assembled again with it — the quiz_arena tab
lands last (``quizPolicy.position``), a sparse host may take a quick_quiz
strip instead of a tip, links and the overview's item count follow — and the
final re-emit (``TabLedger.closing_events``) sends every tab final at its
final position.

What the late phases did to the first assembly in place is carried over: the
moment fill's frames (moment_track props). Synthesis's meta + overview patch is
re-applied by the caller.
"""

from __future__ import annotations

_MOMENT_COMPONENT = "moment_track"


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
