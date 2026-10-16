"""In-memory sliding-window rate limits (per IP, plus a global send cap)."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Request

from app.api.deps import client_ip
from app.core.errors import RateLimited


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self.hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            retry = int(self.window - (now - q[0])) + 1
            raise RateLimited(f"Rate limit reached; retry in {retry}s", details={"retry_after_seconds": retry},
                              headers={"Retry-After": str(retry)})
        q.append(now)


class Limits:
    def __init__(self, research_per_10min: int, send_per_hour: int, send_per_day_global: int):
        self.research = SlidingWindowLimiter(research_per_10min, 600)
        self.verify = SlidingWindowLimiter(10, 600)
        self.professors = SlidingWindowLimiter(5, 600)
        self.draft = SlidingWindowLimiter(10, 3600)
        self.approve = SlidingWindowLimiter(10, 3600)
        self.send = SlidingWindowLimiter(send_per_hour, 3600)
        self.send_global = SlidingWindowLimiter(send_per_day_global, 86400)
        self.replay = SlidingWindowLimiter(20, 600)


def limit(name: str):
    def dep(request: Request) -> None:
        limits: Limits = request.app.state.limits
        getattr(limits, name).check(client_ip(request))
    return dep
