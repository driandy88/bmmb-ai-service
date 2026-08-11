"""The wall-clock cap for third-party calls — a hung call must not stall the turn forever."""
import time

import pytest

from app.utils.timeouts import ExternalTimeout, call_with_timeout


def test_fast_call_returns_its_value():
    assert call_with_timeout(lambda: 42, timeout=1) == 42


def test_a_hang_is_capped_not_infinite():
    # A call that sleeps far longer than the budget is abandoned and raises — quickly.
    start = time.perf_counter()
    with pytest.raises(ExternalTimeout):
        call_with_timeout(lambda: time.sleep(30), timeout=0.15, retries=1)
    assert time.perf_counter() - start < 2.0  # two 0.15s attempts, not 30s


def test_errors_retry_then_surface():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        raise ValueError("boom")

    with pytest.raises(ValueError):
        call_with_timeout(flaky, timeout=1, retries=1)
    assert calls["n"] == 2  # original + one retry
