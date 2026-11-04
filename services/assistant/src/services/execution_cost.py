"""The LLM cost of one assistant execution, for the token ledger.

vie-api mints an ``X-Execution-Id`` per chat turn, library chat or action and
debits the request's real cost when it hears back. Each endpoint starts an
:class:`ExecutionCost` for the request (a contextvar, so concurrent requests
never share one); :mod:`src.services.llm_provider` adds the cost of every
final LLM response the request makes (tool rounds and streamed answers
included); when the request ends the total goes to vie-api's
``POST /internal/assistant/usage`` in the background. ``llm_usage`` rows are
written as before; this is a separate, in-process total.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal, Protocol

from src.logging_config import get_logger

logger = get_logger(__name__)

ExecutionKind = Literal["chat", "library_chat", "action"]


class UsageReporter(Protocol):
    """What reports a finished execution's cost (the vie-api client)."""

    async def report_execution_usage(
        self,
        user_id: str,
        *,
        execution_id: str,
        request_id: str,
        kind: ExecutionKind,
        cost_usd: float,
        calls: int,
    ) -> bool: ...


@dataclass
class ExecutionCost:
    """Running total of one request's LLM spend."""

    execution_id: str
    kind: ExecutionKind
    user_id: str
    request_id: str
    cost_usd: float = 0.0
    calls: int = 0

    def add(self, cost_usd: float) -> None:
        self.calls += 1
        if cost_usd > 0:
            self.cost_usd += cost_usd


_execution_cost_var: ContextVar[ExecutionCost | None] = ContextVar("execution_cost", default=None)

# Strong references so fire-and-forget usage reports aren't GC'd mid-POST.
_REPORT_TASKS: set[asyncio.Task[None]] = set()


def start_execution(
    *,
    execution_id: str | None,
    kind: ExecutionKind,
    user_id: str | None,
    request_id: str | None,
) -> ExecutionCost | None:
    """Start metering this request; None (no meter) without an execution id or a user.

    Always sets the contextvar, so a request without a meter never adds to
    one an earlier request on the same task left behind.
    """
    meter = (
        ExecutionCost(
            execution_id=execution_id,
            kind=kind,
            user_id=user_id,
            request_id=request_id or execution_id,
        )
        if execution_id and user_id
        else None
    )
    _execution_cost_var.set(meter)
    return meter


def record_llm_cost(cost_usd: float) -> None:
    """Add one final LLM response's cost to the request's meter, if it has one."""
    meter = _execution_cost_var.get()
    if meter is not None:
        meter.add(cost_usd)


def report_in_background(meter: ExecutionCost | None, reporter: UsageReporter | None) -> None:
    """Send the meter's total to vie-api without holding up (or failing) the response.

    Fire-and-forget on purpose: a client that disconnects cancels the
    response, and the report must still go out. A lost report is backfilled
    by vie-api's repair job from ``llm_usage``.
    """
    if meter is None or reporter is None:
        return

    async def _report() -> None:
        try:
            await reporter.report_execution_usage(
                meter.user_id,
                execution_id=meter.execution_id,
                request_id=meter.request_id,
                kind=meter.kind,
                cost_usd=round(meter.cost_usd, 8),
                calls=meter.calls,
            )
        except Exception as exc:  # noqa: BLE001 — the report is best-effort
            logger.warning(
                "execution_usage_report_failed", execution_id=meter.execution_id, error=str(exc)
            )

    task = asyncio.create_task(_report())
    _REPORT_TASKS.add(task)
    task.add_done_callback(_REPORT_TASKS.discard)


async def metered_stream(
    events: AsyncIterator[str],
    meter: ExecutionCost | None,
    reporter: UsageReporter | None,
) -> AsyncIterator[str]:
    """Pass a streamed response through and report its cost once it ends (or is cut off)."""
    try:
        async for event in events:
            yield event
    finally:
        report_in_background(meter, reporter)
