"""Bounded in-memory TTL/LRU caches. Never store student profiles here."""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    def __init__(self, max_items: int, ttl_seconds: float):
        self.max_items = max_items
        self.ttl = ttl_seconds
        self._d: OrderedDict[K, tuple[float, V]] = OrderedDict()

    def get(self, key: K) -> V | None:
        item = self._d.get(key)
        if item is None:
            return None
        ts, val = item
        if time.monotonic() - ts > self.ttl:
            self._d.pop(key, None)
            return None
        self._d.move_to_end(key)
        return val

    def set(self, key: K, value: V) -> None:
        self._d[key] = (time.monotonic(), value)
        self._d.move_to_end(key)
        while len(self._d) > self.max_items:
            self._d.popitem(last=False)

    def __len__(self) -> int:
        return len(self._d)


SIX_HOURS = 6 * 3600
