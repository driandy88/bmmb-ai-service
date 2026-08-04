"""Phase 1 — grounded, cited answers (program Q&A + guidelines).

A fake retriever supplies chunks so the whole path runs deterministically under
the stub LLM: retrieve -> synthesize_answer -> sentences[] + numbered citations.
"""
from app.agents.guidelines.guidelines import GuidelinesAgent
from app.agents.program_advisor.advisor import ProgramAdvisor
from app.agents.rag.retriever import RetrievalChunk, Retriever
from app.integrations.llm import StubLLMClient


class FakeRetriever(Retriever):
    def __init__(self, chunks):
        self._chunks = chunks

    def retrieve(self, query, corpus, top_k=5, *, program_code=None, channel="customer"):
        return self._chunks[:top_k]


def _chunk(text, *, section="financing_rate", score=0.71, page=2):
    return RetrievalChunk(
        text=text, corpus="program", ref=f"gs://bmmb/SalesKit_MIHP-i.pdf#page={page}",
        score=score,
        metadata={"doc_id": "mihp_i", "doc_title": "MIHP-i Sales Kit",
                  "section": section, "access_tier": "customer"},
    )


def test_program_fact_question_returns_grounded_cited_answer():
    chunks = [_chunk("MIHP-I › Financing rate › Profit rate is 3% flat per annum.")]
    adv = ProgramAdvisor(StubLLMClient(), FakeRetriever(chunks))
    res = adv.handle("what's the profit rate for industrial hire purchase?", [], {})
    assert res["grounded"] is True
    assert res["ui_action"]["type"] == "none"               # answered, not funnelled
    assert res["sentences"][0]["cites"] == [1]
    cit = res["citations"][0]
    assert cit["n"] == 1 and cit["page"] == 2 and cit["doc_title"] == "MIHP-i Sales Kit"


def test_program_discovery_question_still_funnels():
    adv = ProgramAdvisor(StubLLMClient(), FakeRetriever([_chunk("x")]))
    res = adv.handle("what SME financing programmes do you offer?", [], {})
    assert res["ui_action"]["type"] == "show_program_options"   # funnel, unchanged
    assert not res.get("grounded")


def test_program_empty_retriever_falls_through_to_funnel():
    adv = ProgramAdvisor(StubLLMClient(), FakeRetriever([]))   # stub-like: no chunks
    res = adv.handle("what is the profit rate?", [], {})
    assert res["ui_action"]["type"] == "show_program_options"
    assert not res.get("grounded")


def test_guidelines_grounded_when_corpus_has_chunks():
    chunks = [_chunk("Shariah › SME financing is structured under Tawarruq.", section="shariah")]
    agent = GuidelinesAgent(StubLLMClient(), FakeRetriever(chunks))
    res = agent.handle("is your SME financing shariah compliant?", [])
    assert res["grounded"] is True
    assert res["citations"][0]["n"] == 1


def test_guidelines_empty_corpus_uses_fallback():
    agent = GuidelinesAgent(StubLLMClient(), FakeRetriever([]))
    res = agent.handle("is your SME financing shariah compliant?", [])
    assert not res.get("grounded")
    assert res["citations"] == []


def test_envelope_surfaces_grounded_sentences_and_citations():
    # The dispatch → ChatResponse seam: sentences[] + enriched citations survive.
    from app.orchestrator.graph import _assemble
    final = {
        "session_id": "s1", "reply": "Profit rate is 3% flat per annum.",
        "sentences": [{"text": "Profit rate is 3% flat per annum.", "cites": [1]}],
        "grounded": True,
        "citations": [{"n": 1, "corpus": "program", "ref": "gs://k.pdf#page=2",
                       "snippet": "Profit rate is 3% flat per annum.", "doc_id": "mihp_i",
                       "doc_title": "MIHP-i Sales Kit", "section": "financing_rate",
                       "page": 2, "score": 0.71, "access_tier": "customer"}],
        "intent": {"primary": "INS-02", "confidence": 0.9},
        "trace_id": "t", "route": "", "rule_version": "", "timestamp": "", "decision_inputs": {},
    }
    resp = _assemble(final, taxonomy=None)
    assert resp.grounded is True
    assert resp.sentences[0].cites == [1]
    assert resp.citations[0].n == 1 and resp.citations[0].page == 2
    assert resp.citations[0].doc_title == "MIHP-i Sales Kit"
