"""Blocking token-bucket rate limiter for outbound API calls.

A synchronous port of the proposal agent's asyncio bucket. Defaults stay
inside Upwork's sane ceiling (~5–10 requests/s, 40k calls/day). `clock`
and `sleep` are injectable so tests can run without waiting.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class TokenBucket:
    def __init__(
        self,
        rate_per_sec: float = 5.0,
        burst: int = 10,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rate = float(rate_per_sec)
        self.capacity = float(burst)
        self._tokens = float(burst)
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = threading.Lock()

    def acquire(self, cost: float = 1.0) -> float:
        """Block until `cost` tokens are available. Returns seconds waited."""
        waited = 0.0
        with self._lock:
            while True:
                now = self._clock()
                self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= cost:
                    self._tokens -= cost
                    return waited
                delay = (cost - self._tokens) / self.rate
                self._sleep(delay)
                waited += delay

    @property
    def tokens(self) -> float:
        return self._tokens
