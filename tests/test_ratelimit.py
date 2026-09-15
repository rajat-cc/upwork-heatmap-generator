"""Token bucket maths with an injected clock, so the test never sleeps."""

from __future__ import annotations

from core.ratelimit import TokenBucket


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_burst_is_free_then_calls_are_paced():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_sec=5.0, burst=2, clock=clock, sleep=clock.sleep)

    assert bucket.acquire() == 0.0
    assert bucket.acquire() == 0.0
    waited = bucket.acquire()  # bucket empty: must wait for one token at 5/s
    assert abs(waited - 0.2) < 1e-9
    assert clock.slept == [0.2]


def test_tokens_refill_over_time():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_sec=10.0, burst=5, clock=clock, sleep=clock.sleep)
    for _ in range(5):
        bucket.acquire()
    clock.now += 1.0  # a full second refills to capacity (not beyond)
    assert bucket.acquire() == 0.0
    assert abs(bucket.tokens - 4.0) < 1e-9
