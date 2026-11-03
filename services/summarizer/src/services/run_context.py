"""The run id of the pipeline attempt executing in the current context.

Bound by ``stream_summarization`` once it resolved the attempt's run id, read by
``send_video_status`` so every status callback of that attempt (the runner's,
assembly's, translation's) tells vie-api which ``runs[]`` entry it closes. Tasks
spawned inside the run copy the context and inherit it.
"""

from __future__ import annotations

from contextvars import ContextVar

_run_id_var: ContextVar[str | None] = ContextVar("pipeline_run_id", default=None)


def bind_run_id(run_id: str | None) -> None:
    """Bind ``run_id`` to the current context (``None`` clears a stale one).

    Set without a reset token, like ``start_run_timing``: the runner is an async
    generator whose ``aclose()`` can run in another context.
    """
    _run_id_var.set(run_id)


def current_run_id() -> str | None:
    """The run id bound in this context, or ``None`` outside a run."""
    return _run_id_var.get()
