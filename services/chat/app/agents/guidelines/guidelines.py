"""
Guidelines / Shariah agent (brief §7) — RAG over the GUIDELINES_SHARIAH corpus.

The corpus is a PLACEHOLDER (Sheet 4 is empty — docs pending from BMMB), so the
StubRetriever returns nothing today and the deterministic fallback runs. When
real documents are ingested and RAG_BACKEND flips, the same code path produces
grounded, cited answers with no change here (brief §11.1).

Consumes only the Retriever interface. Never invents policy or a Shariah ruling
it can't ground — for specifics it points to the team.
"""
from __future__ import annotations

from typing import Optional

from ...agents.rag.retriever import Corpus, Retriever
from ...agents.rag.synthesize import grounded_answer
from ...config.loader import AppConfig, load_config
from ...integrations.llm import LLMClient

_FALLBACK = (
    "I can share general guidance on BMMB's SME financing and how our Shariah-compliant "
    "financing works. For a definitive ruling on a specific business activity or policy point, "
    "our SME financing team can confirm the details with you. What would you like to know?"
)


class GuidelinesAgent:
    def __init__(self, llm: LLMClient, retriever: Retriever, config: Optional[AppConfig] = None):
        self._llm = llm
        self._retriever = retriever
        self._cfg = config or load_config()

    def handle(self, message: str, history: list[dict]) -> dict:
        # Grounded, cited answer over the Shariah/guidelines corpus (Phase 1). Returns
        # None while the corpus is empty (Sheet 4 pending) or nothing grounds it.
        ans = grounded_answer(self._llm, self._retriever, message,
                              Corpus.GUIDELINES_SHARIAH, top_k=4, history=history)
        if ans:
            return {
                "reply": ans["reply"],
                "sentences": ans["sentences"],
                "grounded": True,
                "citations": ans["citations"],
                "stage": "guidelines",
                "ui_action": {"type": "none", "payload": {}},
                "handoff": False,
                "handoff_reason": None,
                "decision_inputs": {"corpus": Corpus.GUIDELINES_SHARIAH.value,
                                    "chunks": len(ans["citations"]), "grounded": True},
            }
        return {
            "reply": _FALLBACK,
            "citations": [],
            "stage": "guidelines",
            "ui_action": {"type": "none", "payload": {}},
            "handoff": False,
            "handoff_reason": None,
            "decision_inputs": {"corpus": Corpus.GUIDELINES_SHARIAH.value, "chunks": 0},
        }
