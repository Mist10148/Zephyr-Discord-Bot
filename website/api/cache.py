"""Small dependency-free cache with per-key request coalescing."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Event, Lock
from time import monotonic
from typing import Callable, TypeVar

T = TypeVar("T")

WAIT_SECONDS = 20


@dataclass
class _Item:
    value: object
    expires_at: float


@dataclass
class _Load:
    """One in-flight load that other callers for the same key wait on.

    ``error`` carries the owner's failure to its waiters.  Without it every
    waiter woke to an empty cache and retried in turn, so N callers waiting on
    a failing upstream produced N failing loads back to back.
    """

    done: Event = field(default_factory=Event)
    error: BaseException | None = None


class TTLCache:
    def __init__(self, maxsize: int = 256):
        self.maxsize, self._items, self._waiters, self._lock = maxsize, OrderedDict(), {}, Lock()

    def get_or_load(self, key: str, ttl: int, loader: Callable[[], T]) -> T:
        with self._lock:
            item = self._items.get(key)
            if item and item.expires_at > monotonic():
                # Least-recently-used eviction: without this a hot key kept its
                # first-insertion slot and was the first to be evicted.
                self._items.move_to_end(key)
                return item.value  # type: ignore[return-value]
            load = self._waiters.get(key)
            if load is None:
                load, owner = _Load(), True
                self._waiters[key] = load
            else: owner = False
        if not owner:
            finished = load.done.wait(WAIT_SECONDS)
            with self._lock:
                item = self._items.get(key)
                if item: return item.value  # type: ignore[return-value]
            if finished and load.error is not None:
                raise load.error
            # Only a load that outlived the wait is retried.
            return self.get_or_load(key, ttl, loader)
        try:
            value = loader()
            with self._lock:
                self._items[key] = _Item(value, monotonic() + ttl)
                self._items.move_to_end(key)
                while len(self._items) > self.maxsize: self._items.popitem(last=False)
            return value
        except Exception as exc:
            with self._lock:
                if item: return item.value  # type: ignore[return-value]
            load.error = exc
            raise
        finally:
            with self._lock:
                self._waiters.pop(key, None)
            load.done.set()

    def clear(self) -> None:
        """Drop every entry.  A process-wide cache is otherwise shared between
        tests, where one test's forecast silently answers the next one's."""
        with self._lock:
            self._items.clear()
