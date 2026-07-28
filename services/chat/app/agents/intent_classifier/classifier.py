"""
Intent classifier agent (brief §7) — thin wrapper over the LLM's NLU.

Returns {primary, confidence, secondary} using the taxonomy from
config/intents.yaml. It makes NO routing decision (that's routing.py). It only
validates that the labels the model returned are real cat_ids — an unknown
label is treated as "unsure" (low confidence) so the router clarifies rather
than dispatching on a hallucinated category.
"""
from __future__ import annotations

from app.config.loader import load_config
from app.integrations.llm import LLMClient


class IntentClassifier:
    def __init__(self, llm: LLMClient):
        self._llm = llm

    def classify(self, message: str, history: list[dict] | None = None) -> dict:
        tax = load_config().taxonomy
        out = self._llm.classify_intent(message, history or [])

        primary = out.get("primary")
        secondary = out.get("secondary")
        confidence = max(0.0, min(1.0, float(out.get("confidence") or 0.0)))   # clamp to [0,1]

        # Reject hallucinated labels; drop a bad secondary silently. (The Vertex
        # schema enum already prevents this, but the stub and any future backend
        # aren't constrained — so validate here regardless.)
        if primary is not None and tax.get(primary) is None:
            primary, confidence = None, min(confidence, 0.3)
        if secondary is not None and tax.get(secondary) is None:
            secondary = None
        if secondary == primary:
            secondary = None

        return {"primary": primary, "confidence": confidence, "secondary": secondary}
