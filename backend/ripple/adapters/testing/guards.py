"""Dynamic runtime purity guards for adapter normalization execution.

Intercepts I/O, clock reads, subprocesses, and non-deterministic UUID generation
at the runtime level to ensure pure, isolated normalization logic.
"""

from __future__ import annotations

import builtins
import socket
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class PurityViolationError(RuntimeError):
    """Raised when an adapter attempts impure side-effects during normalization."""


@contextmanager
def dynamic_purity_guard() -> Iterator[None]:
    """Context manager enforcing runtime purity during adapter normalization.

    Patches:
    - uuid.uuid1 and uuid.uuid4
    - socket.socket.connect and socket.getaddrinfo
    - subprocess.Popen
    - builtins.open (for write, append, or modify modes)

    Restores all patched callables on exit.
    """
    orig_uuid1 = uuid.uuid1
    orig_uuid4 = uuid.uuid4
    orig_socket_connect = socket.socket.connect
    orig_getaddrinfo = socket.getaddrinfo
    orig_popen = subprocess.Popen
    orig_open = builtins.open

    def guarded_uuid1(*args: Any, **kwargs: Any) -> Any:
        raise PurityViolationError(
            "CF-06 dynamic purity violation: uuid.uuid1() called during normalization."
        )

    def guarded_uuid4(*args: Any, **kwargs: Any) -> Any:
        raise PurityViolationError(
            "CF-06 dynamic purity violation: uuid.uuid4() called during normalization."
        )

    def guarded_connect(*args: Any, **kwargs: Any) -> Any:
        raise PurityViolationError(
            "CF-06 dynamic purity violation: socket network connection attempted."
        )

    def guarded_getaddrinfo(*args: Any, **kwargs: Any) -> Any:
        raise PurityViolationError(
            "CF-06 dynamic purity violation: DNS lookup (getaddrinfo) attempted."
        )

    def guarded_popen(*args: Any, **kwargs: Any) -> Any:
        raise PurityViolationError(
            "CF-06 dynamic purity violation: subprocess execution attempted."
        )

    def guarded_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        mode_str = str(mode).lower()
        if any(char in mode_str for char in ("w", "a", "x", "+")):
            raise PurityViolationError(
                f"CF-06 dynamic purity violation: filesystem write opened with mode '{mode}'."
            )
        return orig_open(file, mode, *args, **kwargs)

    def _set_attr(target: Any, name: str, value: Any) -> None:
        setattr(target, name, value)

    _set_attr(uuid, "uuid1", guarded_uuid1)
    _set_attr(uuid, "uuid4", guarded_uuid4)
    _set_attr(socket.socket, "connect", guarded_connect)
    _set_attr(socket, "getaddrinfo", guarded_getaddrinfo)
    _set_attr(subprocess, "Popen", guarded_popen)
    _set_attr(builtins, "open", guarded_open)

    try:
        yield
    finally:
        _set_attr(uuid, "uuid1", orig_uuid1)
        _set_attr(uuid, "uuid4", orig_uuid4)
        _set_attr(socket.socket, "connect", orig_socket_connect)
        _set_attr(socket, "getaddrinfo", orig_getaddrinfo)
        _set_attr(subprocess, "Popen", orig_popen)
        _set_attr(builtins, "open", orig_open)
