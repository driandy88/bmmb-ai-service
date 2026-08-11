"""
A hard wall-clock cap for blocking third-party calls (Vertex Gemini / embeddings).

google-genai 0.3.0 exposes no per-request timeout, so a single hung or very slow Gemini response
would otherwise stall the WHOLE turn indefinitely — the "typing…" that never returns. We run the call
on a DAEMON thread and give up after `timeout`. A timed-out call is ABANDONED — the daemon thread
finishes in the background (or is killed at process exit; being a daemon it never blocks shutdown) and
its result is discarded. The caller's own try/except then degrades to the deterministic stub, so
nothing hangs. A daemon thread per call is cheap relative to a multi-second network round-trip.

We retry on ERROR (a transient failure usually clears on a fresh request) but NOT on TIMEOUT: a hung
backend just hangs again, so retrying a timeout only doubles the wall-clock (12s→24s of dead wait)
without changing the outcome — it still ends up on the fallback. So a hang degrades on the first
window; only genuine errors get a second attempt.
"""
from __future__ import annotations

import threading
from typing import Callable, TypeVar

from app.utils.logging import get_logger

log = get_logger("timeout")
T = TypeVar("T")


class ExternalTimeout(Exception):
    """A third-party call exceeded its wall-clock budget."""


def call_with_timeout(fn: Callable[[], T], *, timeout: float, retries: int = 1, label: str = "call") -> T:
    """Run `fn()` with a hard `timeout` (seconds). Retry up to `retries` times ON ERROR only;
    a TIMEOUT is NOT retried — a hang won't heal on a second try, so we degrade on the first window
    instead of doubling the wait. Raises the last error if every attempt fails — the caller degrades
    to its own fallback."""
    last: Exception = ExternalTimeout(f"{label} did not run")
    for attempt in range(retries + 1):
        box: dict[str, object] = {}

        def _worker() -> None:
            try:
                box["value"] = fn()
            except Exception as exc:  # noqa: BLE001 — carried out to the caller after the retry budget
                box["error"] = exc

        thread = threading.Thread(target=_worker, name=f"ext-{label}", daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            # A hang: abandon the daemon thread and degrade NOW. Retrying would just wait another
            # full window on the same slow backend and still fall through to the fallback.
            log.warning("%s timed out after %.0fs — degrading (a hang is not retried)", label, timeout)
            raise ExternalTimeout(f"{label} exceeded {timeout:.0f}s")
        if "error" in box:
            last = box["error"]  # type: ignore[assignment]
            log.warning("%s failed (%s) (attempt %d/%d)", label, type(last).__name__, attempt + 1, retries + 1)
            continue
        return box["value"]  # type: ignore[return-value]
    raise last
