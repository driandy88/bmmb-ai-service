"""Phase 1 — grounded, cited answers (program Q&A + guidelines).

A fake retriever supplies chunks + a programs() set so the whole path runs
deterministically under the stub LLM: name a programme -> retrieve -> synthesize ->
sentences[] + numbered citations. A general question keeps running the funnel.
"""
from app.agents.guidelines.guidelines import GuidelinesAgent
from app.agents.program_advisor.advisor import ProgramAdvisor
from app.agents.rag.retriever import RetrievalChunk, Retriever
from app.integrations.llm import StubLLMClient


class FakeRetriever(Retriever):
    def __init__(self, chunks, programs=None):
        self._chunks = chunks
        self._programs = programs or []

    def retrieve(self, query, corpus, top_k=5, *, program_code=None, channel="customer"):
        return self._chunks[:top_k]

    def programs(self):
        return self._programs


def _chunk(text, *, section="financing_rate", score=0.71, page=2):
    return RetrievalChunk(
        text=text, corpus="program", ref=f"gs://bmmb/SalesKit_MIHP-i.pdf#page={page}",
        score=score,
        metadata={"doc_id": "mihp_i", "doc_title": "MIHP-i Sales Kit",
                  "section": section, "access_tier": "customer"},
    )


PROGRAMS = [("MIHP-I", "MIHP-i Sales Kit"), ("GGSM3", "GGSM3 Sales Kit")]


def test_named_program_returns_grounded_cited_answer():
    chunks = [_chunk("MIHP-I › Financing rate › Profit rate is 3% flat per annum.")]
    adv = ProgramAdvisor(StubLLMClient(), FakeRetriever(chunks, PROGRAMS))
    res = adv.handle("what's the profit rate for MIHP-I?", [], {})
    assert res["grounded"] is True
    assert res["ui_action"]["type"] == "none"               # answered, not funnelled
    assert res["sentences"][0]["cites"] == [1]
    cit = res["citations"][0]
    assert cit["n"] == 1 and cit["page"] == 2 and cit["doc_title"] == "MIHP-i Sales Kit"


def test_named_program_answers_even_with_funnel_slots_set():
    # The bug fix: a stuck funnel state must NOT block a named-programme question.
    chunks = [_chunk("GGSM3 › Financing rate › Profit rate is BFR + 2% per annum.")]
    adv = ProgramAdvisor(StubLLMClient(), FakeRetriever(chunks, PROGRAMS))
    res = adv.handle("what's the profit rate for GGSM3?", [],
                     {"funnel_purpose": 4, "funnel_amount": 300000})
    assert res["grounded"] is True
    assert res["ui_action"]["type"] == "none"


def test_program_offer_followup_inherits_last_program():
    # Flow fix: in the post-answer offer, an anaphoric follow-up that names no programme
    # ("elaborate more, the profit rate") is ANSWERED about the one just discussed
    # (last_program), not deflected with "would you like to apply for GGSM3…".
    chunks = [_chunk("GGSM3 › Financing rate › Profit rate is BFR + 2% per annum.")]
    adv = ProgramAdvisor(StubLLMClient(), FakeRetriever(chunks, PROGRAMS))
    res = adv.handle("can you elaborate more, like the profit rate and other details?",
                     [], {"last_program": "GGSM3"}, stage="program_offer")
    assert res["grounded"] is True
    assert res["ui_action"]["type"] == "none"          # answered, not deflected
    assert res["stage"] == "program_offer"             # still open for more follow-ups
    assert res["slots"]["last_program"] == "GGSM3"


def test_program_offer_other_programmes_opens_funnel():
    # "what else do you have?" in the offer leaves the current programme and opens the
    # discovery funnel, instead of dead-ending on "apply for GGSM3?".
    adv = ProgramAdvisor(StubLLMClient(), FakeRetriever([_chunk("x")], PROGRAMS))
    res = adv.handle("what else do you have?", [],
                     {"last_program": "GGSM3"}, stage="program_offer")
    assert res["ui_action"]["type"] == "show_program_options"
    assert res["ui_action"]["payload"]["step"] == "purpose"
    assert not res.get("grounded")
    assert "last_program" not in res["slots"]          # moved off the old programme


def test_program_offer_stray_reply_still_reprompts():
    # A stray reply that is neither answerable nor a browse request keeps the original
    # gentle re-prompt (unchanged behaviour) rather than dropping into the funnel.
    adv = ProgramAdvisor(StubLLMClient(), FakeRetriever([], programs=PROGRAMS))
    res = adv.handle("hmm cool", [], {"last_program": "GGSM3"}, stage="program_offer")
    assert res["ui_action"]["type"] == "none"
    assert not res.get("grounded")
    assert "GGSM3" in res["reply"]


def test_general_question_naming_no_programme_still_funnels():
    adv = ProgramAdvisor(StubLLMClient(), FakeRetriever([_chunk("x")], PROGRAMS))
    res = adv.handle("what SME financing programmes do you offer?", [], {})
    assert res["ui_action"]["type"] == "show_program_options"   # funnel, §6a
    assert not res.get("grounded")


def test_bare_purpose_answer_continues_funnel():
    adv = ProgramAdvisor(StubLLMClient(), FakeRetriever([_chunk("x")], PROGRAMS))
    res = adv.handle("machinery", [], {})                       # a lone purpose -> funnel nav
    assert res["ui_action"]["type"] == "show_program_options"
    assert not res.get("grounded")


def test_empty_index_falls_through_to_funnel():
    adv = ProgramAdvisor(StubLLMClient(), FakeRetriever([], programs=[]))
    res = adv.handle("tell me about GGSM3", [], {})
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
