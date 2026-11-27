"""Bounded admission without unbounded waiters or a cross-conversation lock."""

from contextlib import contextmanager


class RequestBusy(Exception):
    """Capacity reached or this conversation already has a command in flight."""


class Admission:
    def __init__(self, limit=8):
        self.limit = limit
        self.active = set()

    @contextmanager
    def enter(self, key):
        # Synchronous check/insert is atomic on HA's event loop.
        if key in self.active or len(self.active) >= self.limit:
            raise RequestBusy
        self.active.add(key)
        try:
            yield
        finally:
            self.active.remove(key)
