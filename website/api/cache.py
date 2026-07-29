"""Small dependency-free cache with per-key request coalescing."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Event, Lock
from time import monotonic
from typing import Callable, TypeVar

T = TypeVar("T")


@dataclass
class _Item:
    value: object
    expires_at: float


class TTLCache:
    def __init__(self, maxsize: int = 256):
        self.maxsize, self._items, self._waiters, self._lock = maxsize, OrderedDict(), {}, Lock()

    def get_or_load(self, key: str, ttl: int, loader: Callable[[], T]) -> T:
        with self._lock:
            item = self._items.get(key)
            if item and item.expires_at > monotonic(): return item.value  # type: ignore[return-value]
            waiter = self._waiters.get(key)
            if waiter is None:
                waiter, owner = Event(), True
                self._waiters[key] = waiter
            else: owner = False
        if not owner:
            waiter.wait(20)
            with self._lock:
                item = self._items.get(key)
                if item: return item.value  # type: ignore[return-value]
            return self.get_or_load(key, ttl, loader)
        try:
            value = loader()
            with self._lock:
                self._items[key] = _Item(value, monotonic() + ttl)
                while len(self._items) > self.maxsize: self._items.popitem(last=False)
            return value
        except Exception:
            with self._lock:
                if item: return item.value  # type: ignore[return-value]
            raise
        finally:
            with self._lock:
                self._waiters.pop(key, waiter).set()
