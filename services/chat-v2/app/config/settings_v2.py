"""
v2-only knobs.

`app/config/settings.py` is vendored byte-identical from v1 and must stay that
way (tests/test_no_drift.py enforces it), so anything v2 needs on top lives
here. Everything v1 already defines — project, region, model, RAG backend, DB,
embedding pins, handoff hours, application URL — is read from that Settings
object unchanged, which is what keeps the A/B honest: both services read the
same environment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class V2Settings:
    # Hard stop on the agent loop. A backstop against a model that keeps calling
    # tools, not a budget to spend — most turns finish in one or two calls. When
    # this trips we return what we have rather than erroring, and flag it in the
    # audit so a rising count is visible.
    agent_max_steps: int = int(os.getenv("AGENT_MAX_STEPS", "8"))

    # Deterministic decoding. v1 runs its NLU at temperature 0 for reproducible
    # classification; v2 keeps that so eval runs are comparable turn to turn.
    temperature: float = float(os.getenv("AGENT_TEMPERATURE", "0"))

    # Chunks per knowledge search. v1 uses top_k=4 for grounded answers.
    search_top_k: int = int(os.getenv("SEARCH_TOP_K", "4"))

    # Wall-clock ceiling for one turn, across all steps.
    turn_timeout_s: float = float(os.getenv("AGENT_TURN_TIMEOUT_S", "45"))


@lru_cache(maxsize=1)
def get_v2_settings() -> V2Settings:
    return V2Settings()