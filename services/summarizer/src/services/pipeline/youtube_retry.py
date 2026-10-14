"""A blocked proxy exit, told to the client: "YouTube is rate-limiting, retrying".

The metadata extract and the caption fetch rotate YOUTUBE_PROXY_URL's sticky
exits on a bot check or a 429 (``download_utils.try_proxy_exits``) for up to
``EXIT_ROTATION_BUDGET_S``. That runs in a worker thread, inside one awaited
call, so the phase awaiting it would show nothing — and the client used to see
only the failure after the last exit. Each of the two fetches runs with its own
``RetryNotice`` (``with_retry_notice``: a ContextVar the worker threads
inherit); a blocked exit sets it (``notify_exit_blocked``, thread-safe), and the
phase awaiting the fetch relays it once as an SSE ``phase`` event
(``relay_retry_notice``: ``metadata_retry`` / ``transcript_retry``), which the
web shows as "YouTube is rate-limiting, retrying" until the next phase event.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, TypeVar

from src.services.pipeline.pipeline_helpers import sse_event

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

METADATA_RETRY_PHASE = "metadata_retry"
TRANSCRIPT_RETRY_PHASE = "transcript_retry"


class RetryNotice:
    """Set once a proxy exit got blocked during one fetch; safe to set from any thread."""

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._blocked = asyncio.Event()

    def mark_blocked(self) -> None:
        """Record a blocked exit (called from the fetch's worker thread)."""
        self._loop.call_soon_threadsafe(self._blocked.set)

    async def wait(self) -> None:
        await self._blocked.wait()


_notice_var: ContextVar[RetryNotice | None] = ContextVar("youtube_retry_notice", default=None)


def notify_exit_blocked() -> None:
    """Tell the fetch's notice (if any) that a proxy exit was blocked. Never raises."""
    notice = _notice_var.get()
    if notice is None:
        return
    try:
        notice.mark_blocked()
    except RuntimeError as exc:  # the run's loop already closed: nobody to tell
        logger.debug("Retry notice dropped: %s", exc)


async def with_retry_notice(notice: RetryNotice, fetch: Awaitable[_T]) -> _T:
    """Await ``fetch`` with ``notice`` as the context's notice (its threads inherit it)."""
    _notice_var.set(notice)
    return await fetch


@asynccontextmanager
async def retry_noticed(
    fetch: Awaitable[_T],
) -> AsyncIterator[tuple[asyncio.Task[_T], RetryNotice]]:
    """Run ``fetch`` as a task under its own notice; cancel it if the caller leaves early.

    The caller relays the notice (``relay_retry_notice``) and awaits the task
    inside the block; a failure, a cancel or a closed SSE stream before that
    cancels the fetch instead of orphaning it.
    """
    notice = RetryNotice()
    task = asyncio.create_task(with_retry_notice(notice, fetch))
    try:
        yield task, notice
    finally:
        if not task.done():
            task.cancel()


async def relay_retry_notice(
    task: asyncio.Future[Any], notice: RetryNotice, phase: str
) -> AsyncGenerator[str, None]:
    """Yield one ``phase`` event if ``notice`` is set before ``task`` ends; then return.

    Never awaits the task's result: the caller does, so its errors stay its own.
    """
    waiter = asyncio.ensure_future(notice.wait())
    try:
        done, _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if waiter in done and not task.done():
            yield sse_event("phase", {"phase": phase})
            await asyncio.wait({task})
    finally:
        waiter.cancel()
