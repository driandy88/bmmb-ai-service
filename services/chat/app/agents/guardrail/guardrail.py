"""
Guardrail agent (brief §7) — pre-classification security gate.

Two stages, in order:
  1. Deterministic denylist (denylist.py) — fast, model-independent, catches the
     obvious injection/extraction/exfil/encoding attempts.
  2. LLM adversarial classifier (ADV-01..08) for the subtler cases.

A hit in EITHER stage flags the turn. Runs on the CURRENT message every turn,
never on client history (§5.1). The verdict is emitted as {flagged, category};
the detection reasoning is never surfaced to the user (§12).
"""
from __future__ import annotations

from app.agents.guardrail.denylist import scan
from app.integrations.llm import LLMClient


class Guardrail:
    def __init__(self, llm: LLMClient):
        self._llm = llm

    def check(self, message: str) -> dict:
        # Stage 1 — deterministic denylist wins immediately.
        hit = scan(message)
        if hit:
            return {"flagged": True, "category": hit.category, "source": "denylist"}

        # Stage 2 — LLM classifier.
        verdict = self._llm.detect_adversarial(message)
        if verdict.get("flagged"):
            return {"flagged": True, "category": verdict.get("category"), "source": "llm"}
        return {"flagged": False, "category": None, "source": "none"}
