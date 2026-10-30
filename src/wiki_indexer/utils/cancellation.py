"""Cooperative cancellation for long-running background work."""

from __future__ import annotations

import threading


class OperationCancelled(Exception):
    """Raised when a cancellable operation observes a cancellation request."""


class CancellationToken:
    """Thread-safe flag checked by workers at safe points."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise OperationCancelled("Operation cancelled by user")
