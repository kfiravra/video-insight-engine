"""The quiz's format gate (output-quality 4.6e; D9 revised by Kfir 2026-10-23).

Gate 4 scored every quiz 2.0: the reviewers objected to a quiz on opinion talks and
podcasts at all ("bends an opinion podcast into recall questions", G4-2). The quiz now
ships only on instructional formats — a lecture (a class, a course session, an explainer
that builds up concepts) and a tutorial / how-to the viewer follows — never on a talk,
an interview, a podcast or the news.

The formats are ``quizPolicy.formats`` in domains.json, ids of the tier probe's
vocabulary (``prompts/tier_probe.txt``, ``probe_types.VALID_FORMATS``); the run's format
is the probe's (``ctx.content_format``). ``quizEnrichment.quizDomains`` and
``is_learnable`` still apply on top. An unknown format — no probe answer, or a category
override — rules nothing out, as a missing evidence key does; the probe itself maps a
label it does not know to ``commentary``, so every probe answer is judged.

Readers: ``phases/enrichment`` makes no quiz call on another format, and the reconcile
quiz step drops a planned quiz tab with :data:`QUIZ_FORMAT_REASON` (``droppedTabs``
``reconcile_quiz_format``).
"""

from __future__ import annotations

from ...shared_config.domain_config import get_config

QUIZ_FORMAT_REASON = "quiz_format"


def quiz_formats() -> frozenset[str]:
    """The probe formats a quiz may ship on (``quizPolicy.formats``)."""
    return frozenset(get_config()["quizPolicy"]["formats"])


def quiz_format_allowed(content_format: str | None) -> bool:
    """True when a run of ``content_format`` may carry a quiz; an unknown format (None)
    rules nothing out (module docstring)."""
    return content_format is None or content_format in quiz_formats()
