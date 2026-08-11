"""The wall-clock cap for third-party calls — a hung call must not stall the turn forever."""
import time

import pytest

from services.chat.app.utils.timeouts import ExternalTimeout, call_with_timeout


def test_fast_call_returns_its_value():
    assert call_with_timeout(lambda: 42, timeout=1) == 42


def test_a_hang_is_capped_not_infinite():
    # A call that sleeps far longer than the budget is abandoned and raises — quickly.
    start = time.perf_counter()
    with pytest.raises(ExternalTimeout):
        call_with_timeout(lambda: time.sleep(30), timeout=0.15, retries=1)
    assert time.perf_counter() - start < 1.0  # one 0.15s window, not 30s and not a retried 0.30s


def test_a_hang_is_not_retried():
    # A hang won't heal on a second try, so we degrade on the FIRST window instead of doubling the
    # wait. The call runs exactly once even though retries is budgeted for errors.
    calls = {"n": 0}

    def hangs():
        calls["n"] += 1
        time.sleep(30)

    start = time.perf_counter()
    with pytest.raises(ExternalTimeout):
        call_with_timeout(hangs, timeout=0.15, retries=3)
    assert calls["n"] == 1                        # not retried on timeout
    assert time.perf_counter() - start < 0.5      # one window, not 4 × 0.15s


def test_errors_retry_then_surface():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        raise ValueError("boom")

    with pytest.raises(ValueError):
        call_with_timeout(flaky, timeout=1, retries=1)
    assert calls["n"] == 2  # original + one retry
