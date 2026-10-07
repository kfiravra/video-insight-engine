"""Socket guard: any outbound connect, DNS lookup or UDP send during a replay is a leak.

Attempts are recorded AND refused — recording matters because pipeline code
swallows most I/O errors (status callback, SponsorBlock, S3), so a refused
connect alone would not fail anything. Unix-domain sockets stay allowed
(local IPC, never network).

The patch is process-wide, but only the replay's own threads are policed:
the thread that enters the guard, executor/pool workers, and any thread
started while it is active. Threads that already existed (a library's
background thread from an earlier test, e.g. qdrant-client's version check)
pass through untouched — they are not the replay's traffic, and refusing
them would break someone else's test. Membership is by ``Thread`` object,
not ident: idents are recycled, so a thread started under the guard can
inherit a finished foreign thread's ident.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from typing import Any
from unittest.mock import patch

# Pool workers may predate the guard yet run the replay's to_thread work.
_POOL_THREAD_PREFIXES = ("ThreadPoolExecutor", "asyncio_")
_RESOLVERS = ("getaddrinfo", "gethostbyname", "gethostbyname_ex")


class NetworkBlockedError(OSError):
    """Raised in place of a real connect while the guard is active."""


def _is_unix(sock: socket.socket) -> bool:
    return getattr(socket, "AF_UNIX", None) is not None and sock.family == socket.AF_UNIX


def _foreign_threads() -> frozenset[threading.Thread]:
    """Threads alive before the guard that cannot be running replay work."""
    current = threading.current_thread()
    return frozenset(
        t
        for t in threading.enumerate()
        if t is not current and not t.name.startswith(_POOL_THREAD_PREFIXES)
    )


class _Guard:
    """The guarded replacements, sharing one attempts list and exemption rule."""

    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._foreign = _foreign_threads()

    def exempt(self, sock: socket.socket | None = None) -> bool:
        if sock is not None and _is_unix(sock):
            return True
        return threading.current_thread() in self._foreign

    def refuse(self, target: str) -> NetworkBlockedError:
        self.attempts.append(target)
        return NetworkBlockedError(f"replay network guard refused {target}")

    def socket_method(self, name: str) -> Callable[..., Any]:
        real = getattr(socket.socket, name)

        def guarded(sock: socket.socket, *args: Any) -> Any:
            if self.exempt(sock):
                return real(sock, *args)
            raise self.refuse(f"{name} {args[-1]!r}")

        return guarded

    def resolver(self, name: str) -> Callable[..., Any]:
        real = getattr(socket, name)

        def guarded(host: Any, *args: Any, **kwargs: Any) -> Any:
            if self.exempt():
                return real(host, *args, **kwargs)
            port = f":{args[0]!r}" if args else ""
            raise self.refuse(f"{name} {host!r}{port}")

        return guarded


@contextmanager
def block_network() -> Iterator[list[str]]:
    """Refuse outbound connects/DNS/UDP sends; yields the list of attempted targets."""
    guard = _Guard()
    with ExitStack() as stack:
        for name in ("connect", "connect_ex", "sendto"):
            stack.enter_context(patch.object(socket.socket, name, guard.socket_method(name)))
        for name in _RESOLVERS:
            stack.enter_context(patch.object(socket, name, guard.resolver(name)))
        yield guard.attempts
