"""ContextVars for propagating LLM tracking metadata through call stacks.

Every ``llm_usage`` row reads its attribution from these: the callback and
:func:`llm_common.callback.record_manual_usage` copy them onto the record.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

llm_feature_var: ContextVar[str] = ContextVar("llm_feature", default="unknown")
llm_request_id_var: ContextVar[str | None] = ContextVar("llm_request_id", default=None)
llm_video_id_var: ContextVar[str | None] = ContextVar("llm_video_id", default=None)
llm_user_id_var: ContextVar[str | None] = ContextVar("llm_user_id", default=None)
llm_video_summary_id_var: ContextVar[str | None] = ContextVar("llm_video_summary_id", default=None)

# D9: the ids that join a ledger row to its pipeline attempt (run, trace,
# attempt, version, bypass) or to its chat session (the web's raw session id;
# a pipeline started from chat carries the same id).
llm_run_id_var: ContextVar[str | None] = ContextVar("llm_run_id", default=None)
llm_trace_id_var: ContextVar[str | None] = ContextVar("llm_trace_id", default=None)
llm_session_id_var: ContextVar[str | None] = ContextVar("llm_session_id", default=None)
llm_attempt_var: ContextVar[int | None] = ContextVar("llm_attempt", default=None)
llm_pipeline_version_var: ContextVar[str | None] = ContextVar("llm_pipeline_version", default=None)
llm_bypass_var: ContextVar[bool | None] = ContextVar("llm_bypass", default=None)

# The model whose refusal the current call answers for: set around the one
# re-send on the previous model, so the ledger tags that row ``fallback_from``.
llm_refused_model_var: ContextVar[str | None] = ContextVar("llm_refused_model", default=None)

# The attribution ids by record field name (feature and refusal are per call, not per job).
USAGE_ID_VARS: dict[str, ContextVar[Any]] = {
    "request_id": llm_request_id_var,
    "video_id": llm_video_id_var,
    "user_id": llm_user_id_var,
    "video_summary_id": llm_video_summary_id_var,
    "run_id": llm_run_id_var,
    "trace_id": llm_trace_id_var,
    "session_id": llm_session_id_var,
    "attempt": llm_attempt_var,
    "pipeline_version": llm_pipeline_version_var,
    "bypass": llm_bypass_var,
}


def _var(name: str) -> ContextVar[Any]:
    try:
        return USAGE_ID_VARS[name]
    except KeyError:
        raise TypeError(f"unknown usage id: {name}") from None


def bind_usage_ids(**ids: Any) -> list[Token[Any]]:
    """Set the named attribution ids (``None`` included) and return their reset tokens.

    For code that cannot reset (an async generator may be closed in another
    context), the values simply stay bound to the current context.
    """
    return [_var(name).set(value) for name, value in ids.items()]


def usage_ids() -> dict[str, Any]:
    """The attribution ids bound in this context, by record field name."""
    return {name: var.get() for name, var in USAGE_ID_VARS.items()}


@contextmanager
def usage_scope(**ids: Any) -> Iterator[None]:
    """Run one job with only its own attribution: every id is set, unnamed ones to ``None``.

    All of them are restored on exit, so a long-lived process (the queue worker)
    never lets one job's ids leak onto the next job's rows.
    """
    for name in ids:
        _var(name)
    tokens = [(var, var.set(ids.get(name))) for name, var in USAGE_ID_VARS.items()]
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
