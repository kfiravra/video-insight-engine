"""Socket guard: any outbound connect or DNS lookup during a replay is a leak.

Attempts are recorded AND refused — recording matters because pipeline code
swallows most I/O errors (status callback, SponsorBlock, S3), so a refused
connect alone would not fail anything. Unix-domain sockets stay allowed
(local IPC, never network).

The patch is process-wide, but only the replay's own threads are policed:
the thread that enters the guard, executor/pool workers, and any thread
started while it is active. Threads that already existed (a library's
background thread from an earlier test, e.g. qdrant-client's version check)
pass through untouched — they are not the replay's traffic, and refusing
them would break someone else's test.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

# Pool workers may predate the guard yet run the replay's to_thread work.
_POOL_THREAD_PREFIXES = ("ThreadPoolExecutor", "asyncio_")


class NetworkBlockedError(OSError):
    """Raised in place of a real connect while the guard is active."""


def _is_unix(sock: socket.socket) -> bool:
    return getattr(socket, "AF_UNIX", None) is not None and sock.family == socket.AF_UNIX


def _foreign_thread_ids() -> frozenset[int]:
    """Threads alive before the guard that cannot be running replay work."""
    current = threading.get_ident()
    return frozenset(
        t.ident
        for t in threading.enumerate()
        if t.ident is not None
        and t.ident != current
        and not t.name.startswith(_POOL_THREAD_PREFIXES)
    )


@contextmanager
def block_network() -> Iterator[list[str]]:
    """Refuse outbound connects/DNS; yields the list of attempted targets."""
    attempts: list[str] = []
    foreign = _foreign_thread_ids()
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def _exempt(sock: socket.socket | None = None) -> bool:
        return threading.get_ident() in foreign or (sock is not None and _is_unix(sock))

    def _refuse(target: str) -> NetworkBlockedError:
        attempts.append(target)
        return NetworkBlockedError(f"replay network guard refused {target}")

    def guarded_connect(sock: socket.socket, address: Any) -> None:
        if _exempt(sock):
            return real_connect(sock, address)
        raise _refuse(f"connect {address!r}")

    def guarded_connect_ex(sock: socket.socket, address: Any) -> int:
        if _exempt(sock):
            return real_connect_ex(sock, address)
        raise _refuse(f"connect_ex {address!r}")

    def guarded_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> list[Any]:
        if _exempt():
            return real_getaddrinfo(host, port, *args, **kwargs)
        raise _refuse(f"getaddrinfo {host!r}:{port!r}")

    with (
        patch.object(socket.socket, "connect", guarded_connect),
        patch.object(socket.socket, "connect_ex", guarded_connect_ex),
        patch.object(socket, "getaddrinfo", guarded_getaddrinfo),
    ):
        yield attempts
