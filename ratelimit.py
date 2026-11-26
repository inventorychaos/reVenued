"""Tiny in-memory sliding-window rate limiter (per process; fine for a single small server)."""
import threading
import time
from collections import deque


def parse_rate(spec: str):
    """'10/600' -> (10, 600): at most 10 hits per 600 seconds."""
    n, window = spec.split("/")
    return int(n), int(window)


class RateLimiter:
    MAX_KEYS = 5000        # memory guard: beyond this, idle keys are dropped
    IDLE_SECONDS = 3600

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.hits = {}
        self.lock = threading.Lock()

    def _recent(self, key, window, now):
        dq = self.hits.get(key)
        if dq is None:
            return None
        while dq and now - dq[0] >= window:
            dq.popleft()
        return dq

    def _retry_after(self, dq, window, now):
        return max(1, int(window - (now - dq[0])) + 1)

    def blocked(self, key, limit, window):
        """Seconds until allowed again, or 0 if not blocked. Does not record a hit."""
        with self.lock:
            now = self.clock()
            dq = self._recent(key, window, now)
            return self._retry_after(dq, window, now) if dq and len(dq) >= limit else 0

    def hit(self, key):
        """Record one event (e.g. a failed login)."""
        with self.lock:
            self.hits.setdefault(key, deque()).append(self.clock())
            self._gc()

    def check(self, key, limit, window):
        """Record a hit unless over the limit. Returns 0 if allowed, else seconds to wait."""
        with self.lock:
            now = self.clock()
            dq = self._recent(key, window, now)
            if dq is not None and len(dq) >= limit:
                return self._retry_after(dq, window, now)
            self.hits.setdefault(key, deque()).append(now)
            self._gc()
            return 0

    def _gc(self):
        if len(self.hits) > self.MAX_KEYS:
            now = self.clock()
            for k in [k for k, dq in self.hits.items() if not dq or now - dq[-1] > self.IDLE_SECONDS]:
                del self.hits[k]
