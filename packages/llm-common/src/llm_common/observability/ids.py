"""Identifier helpers for Langfuse traces: the per-run trace id and the user-id policy.

Pure functions with no SDK import, so the api-side twin of
:func:`trace_id_for_run` and the tests can pin the same vectors.
"""

from __future__ import annotations

import hashlib

# ─── Policy values ──────────────────────────────────────────────────────
USER_ID_MODES = frozenset({"identity", "hash", "omit"})
DEFAULT_USER_ID_MODE = "identity"


# ─── Trace ids ──────────────────────────────────────────────────────────
def trace_id_for_run(run_id: str) -> str:
    """The Langfuse trace id of one pipeline run: ``sha256(run_id)`` as hex, first 32 chars.

    Same formula as ``Langfuse.create_trace_id(seed=run_id)`` (a test pins it), so
    a process that knows the run id can link to the trace without asking Langfuse.
    """
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32]


# ─── User ids ───────────────────────────────────────────────────────────
def normalize_user_id_mode(mode: str | None) -> str:
    """``mode`` lowercased; anything unknown falls back to ``identity``."""
    value = (mode or DEFAULT_USER_ID_MODE).strip().lower()
    return value if value in USER_ID_MODES else DEFAULT_USER_ID_MODE


def resolve_user_id(mode: str | None, salt: str | None, user_id: str | None) -> str | None:
    """Apply the ``LANGFUSE_USER_ID_MODE`` policy before a user id leaves the host.

    Modes:
      * ``identity`` (default): pass through, so support can find one user's runs.
      * ``hash``: SHA-256 of ``<salt>::<user_id>``, first 16 hex chars; correlates a
        user's traces without exposing the internal id to the trace store.
      * ``omit``: never send a user id.
    """
    if not user_id:
        return None
    resolved = normalize_user_id_mode(mode)
    if resolved == "omit":
        return None
    if resolved == "identity":
        return user_id
    digest = hashlib.sha256(f"{salt or ''}::{user_id}".encode()).hexdigest()
    return digest[:16]
