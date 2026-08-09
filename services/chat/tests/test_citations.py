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


def test_grounded_offer_drops_redundant_cta_sentence():
    # The offer chips already carry Apply / Connect-to-Sales, so the grounded answer must NOT
    # append a "would you like to apply…" sentence — it just duplicated the buttons (robotic).
    chunks = [_chunk("GGSM3 › Financing rate › Profit rate is BFR + 2% per annum.")]
    adv = ProgramAdvisor(StubLLMClient(), FakeRetriever(chunks, PROGRAMS))
    res = adv.handle("what's the profit rate for GGSM3?", [], {})
    blob = (res["reply"] + " " + " ".join(s["text"] for s in res["sentences"])).lower()
    assert "would you like to apply" not in blob
    assert "speak with our sme team" not in blob
    labels = [s["label"] for s in res["suggestions"]]        # buttons still present
    assert any("Apply" in lbl for lbl in labels)
    assert any("Sales" in lbl for lbl in labels)


class _LabeledLLM:
    """A synthesiser that returns a plain lead + one labelled key fact."""

    def synthesize_answer(self, query, chunks, history=None):
        return {"grounded": True, "sentences": [
            {"text": "GGSM3 helps SMEs fund working capital.", "cites": [1]},
            {"label": "Profit rate", "text": "BFR + 2% per annum", "cites": [1]},
        ]}


def test_grounded_answer_keeps_labels_and_builds_readable_reply():
    # A labelled sentence is a key fact; the lead has no label. The plain-text reply reads
    # "Label: value" so history/logging stay legible even without the structured UI.
    from app.agents.rag.synthesize import grounded_answer
    from app.agents.rag.retriever import Corpus

    chunks = [_chunk("GGSM3 › Financing rate › Profit rate is BFR + 2% per annum.")]
    ans = grounded_answer(_LabeledLLM(), FakeRetriever(chunks, PROGRAMS), "tell me about GGSM3", Corpus.PROGRAM)
    assert ans["sentences"][0].get("label") is None           # lead: no label
    assert ans["sentences"][1]["label"] == "Profit rate"
    assert "Profit rate: BFR + 2% per annum" in ans["reply"]


class _BulletLLM:
    """A synthesiser that lists documents — a lead-in sentence, then one bullet per item."""

    def synthesize_answer(self, query, chunks, history=None):
        return {"grounded": True, "sentences": [
            {"text": "For GGSM3 you'll need to prepare:", "cites": [1]},
            {"text": "A copy of your IC and the company's SSM registration.", "cites": [1], "bullet": True},
            {"text": "Six months of business bank statements.", "cites": [1], "bullet": True},
        ]}


def test_grounded_answer_carries_bullet_flag_and_renders_list_reply():
    # Adaptive formatting: when the synthesiser lists discrete items it flags each with bullet=True.
    # The flag must survive into sentences[] (the UI groups a run of them into a <ul>), and the
    # plain-text reply must render each on its own "- " line so a list still reads as a list.
    from app.agents.rag.synthesize import grounded_answer
    from app.agents.rag.retriever import Corpus

    chunks = [_chunk("GGSM3 › documents › IC, SSM, 6-month bank statements.")]
    ans = grounded_answer(_BulletLLM(), FakeRetriever(chunks, PROGRAMS), "what documents for GGSM3?", Corpus.PROGRAM)
    assert ans["sentences"][0].get("bullet") is None          # lead-in: prose, not a bullet
    assert ans["sentences"][1]["bullet"] is True
    assert ans["sentences"][2]["bullet"] is True
    assert "\n- A copy of your IC" in ans["reply"]             # bullets on their own lines
    assert "\n- Six months of business bank statements." in ans["reply"]


def test_grounded_answer_threads_conversation_history_to_synthesizer():
    # History must reach synthesize_answer so a follow-up ("what about GGSM?") can inherit the
    # attribute from the previous turn and answer only that, instead of recapping the programme.
    captured = {}

    class _SpyLLM(StubLLMClient):
        def synthesize_answer(self, query, chunks, history=None):
            captured["history"] = history
            return {"grounded": True, "sentences": [{"text": "GGSM3 tenure is up to 5 years.", "cites": [1]}]}

    adv = ProgramAdvisor(_SpyLLM(), FakeRetriever([_chunk("x")], programs=[("GGSM3", "GGSM3 Sales Kit")]))
    hist = [{"role": "user", "content": "what's the profit rate for MIHP?"}]
    adv.handle("what is the tenure for GGSM3?", hist, {})
    assert captured["history"] == hist


def test_condensed_followup_query_reaches_retrieval():
    # Anaphora root fix: after "documents for MIHP?", "what about GGSM?" is CONDENSED by the
    # history-aware rewrite into a standalone "documents required for GGSM3". That condensed query —
    # not the raw "what about GGSM?" — must be what retrieval + synthesis see, so the follow-up
    # inherits the ATTRIBUTE (documents) and the resolved programme instead of dumping the whole kit.
    seen = {}

    class _CondenseLLM(StubLLMClient):
        def rewrite_query(self, message, programs, history=None):
            seen["history"] = history
            return {"rewritten_query": "documents required for GGSM3",
                    "program_code": "GGSM3", "is_program_dependent": True}

    class _SpyRetriever(FakeRetriever):
        def retrieve(self, query, corpus, top_k=5, *, program_code=None, channel="customer"):
            seen["query"] = query
            return self._chunks[:top_k]

    adv = ProgramAdvisor(_CondenseLLM(),
                         _SpyRetriever([_chunk("GGSM3 › documents › IC, SSM, 6-month bank statements.")],
                                       PROGRAMS))
    hist = [{"role": "user", "content": "what documents do I need for MIHP?"}]
    res = adv.handle("what about GGSM?", hist, {})
    assert res["grounded"] is True
    assert seen["query"] == "documents required for GGSM3"   # condensed, not the raw "what about GGSM?"
    assert seen["history"] == hist                            # the rewrite is history-aware


def test_synthesis_sees_original_message_even_when_rewrite_broadens():
    # Case 2 (anaphora focus): the real failure is the rewrite extracting the PROGRAMME but dropping
    # the ATTRIBUTE — "what about GGSM?" -> "GGSM3 information". If that broadened query fed the
    # synthesiser, it would dump the whole programme. Fix: retrieval may use the (broad) rewrite for
    # recall, but the synthesiser must receive the customer's ORIGINAL message + history so it
    # resolves the follow-up to the one attribute asked.
    seen = {}

    class _BroadeningLLM(StubLLMClient):
        def rewrite_query(self, message, programs, history=None):
            return {"rewritten_query": "GGSM3 programme information",  # programme kept, attribute lost
                    "program_code": "GGSM3", "is_program_dependent": True}

        def synthesize_answer(self, query, chunks, history=None):
            seen["synth_query"] = query
            seen["synth_history"] = history
            return {"grounded": True, "sentences": [{"text": "GGSM3's profit rate is BFR + 2%.", "cites": [1]}]}

    class _SpyRetriever(FakeRetriever):
        def retrieve(self, query, corpus, top_k=5, *, program_code=None, channel="customer"):
            seen["retrieval_query"] = query
            return self._chunks[:top_k]

    adv = ProgramAdvisor(_BroadeningLLM(),
                         _SpyRetriever([_chunk("GGSM3 › financing rate › BFR + 2%.")], PROGRAMS))
    hist = [{"role": "user", "content": "what's the profit rate for MIHP?"}]
    adv.handle("what about GGSM?", hist, {})
    assert seen["synth_query"] == "what about GGSM?"                # synthesis sees the REAL question
    assert seen["synth_history"] == hist                            # ...with history, to resolve it
    assert seen["retrieval_query"] == "GGSM3 programme information"  # retrieval still used the rewrite


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


def test_program_offer_other_programmes_opens_funnel(monkeypatch):
    # LEGACY path: "what else do you have?" in the offer leaves the current programme and opens the
    # discovery funnel. (The understand path answers this with the catalog list instead — see
    # test_understand.test_what_else_lists_the_catalog.) Pin the flag off so this tests legacy either way.
    import app.agents.program_advisor.advisor as advmod
    monkeypatch.setattr(advmod, "get_settings", lambda: type("S", (), {"use_understand": False})())
    adv = ProgramAdvisor(StubLLMClient(), FakeRetriever([_chunk("x")], PROGRAMS))
    res = adv.handle("what else do you have?", [],
                     {"last_program": "GGSM3"}, stage="program_offer")
    assert res["ui_action"]["type"] == "show_program_options"
    assert res["ui_action"]["payload"]["step"] == "purpose"
    assert not res.get("grounded")
    assert "last_program" not in res["slots"]          # moved off the old programme


def test_program_offer_discovery_question_opens_funnel():
    # A4: after answering about GGSM3 (offer stage), a general "what SME financing programmes do you
    # offer?" is a fresh catalog question (INS-02, not programme-dependent) — open the discovery
    # funnel instead of the awkward "would you like to apply for GGSM3, or ask anything else?".
    adv = ProgramAdvisor(StubLLMClient(), FakeRetriever([_chunk("x")], PROGRAMS))
    res = adv.handle("what SME financing programmes do you offer?", [],
                     {"last_program": "GGSM3"}, stage="program_offer",
                     intent={"primary": "INS-02", "confidence": 0.9})
    assert res["ui_action"]["type"] == "show_program_options"   # funnel, not a reprompt
    assert not res.get("grounded")
    assert "last_program" not in res["slots"]                    # moved off the old programme


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


def test_mistyped_ambiguous_programme_asks_which_one():
    # "what about MHIP?" is a typo one letter off both MIHP and MHP — the rewrite can't settle on one
    # and returns both as candidates. Don't guess or funnel: clarify with a chip per candidate.
    # And the rewrite carries the TOPIC ("documents") per rule 1 even though the programme is
    # ambiguous — so the chip forwards "documents required for <picked>", NOT a bare overview.
    class _AmbiguousLLM(StubLLMClient):
        def rewrite_query(self, message, programs, history=None):
            return {"rewritten_query": "documents required for MHIP", "program_code": None,
                    "program_candidates": ["MIHP-I", "MHP-I"], "is_program_dependent": True}

    progs = [("MIHP-I", "Muamalat Industrial Hire Purchase (MIHP-i) Sales Kit"),
             ("MHP-I", "Micro Hire Purchase-i (MHP-i) Sales Kit")]
    adv = ProgramAdvisor(_AmbiguousLLM(), FakeRetriever([_chunk("x")], programs=progs))
    res = adv.handle("what about MHIP?", [], {})
    assert res["ui_action"]["type"] == "none"                   # a clarify, NOT the funnel
    assert not res.get("grounded")
    assert "did you mean" in res["reply"].lower()
    labels = [s["label"] for s in res["suggestions"]]
    assert any("MIHP-i" in l for l in labels) and any("MHP-i" in l for l in labels)  # both offered as chips
    # each chip forwards the rewrite's resolution with just the programme corrected — TOPIC KEPT
    values = [s["value"] for s in res["suggestions"]]
    assert "documents required for MIHP-I" in values and "documents required for MHP-I" in values
    assert not any("Tell me about" in v for v in values)   # NOT reset to a generic overview


def test_mistyped_name_clarifies_via_deterministic_fallback():
    # The reliability fix: even when the rewrite flags NO candidates (the stub, or an unreliable
    # model), a near-match over the programme list still catches "MHIP" ≈ MIHP & MHP and clarifies —
    # no fake LLM, no typo list. This is the deployed path.
    progs = [("MIHP-I", "Muamalat Industrial Hire Purchase (MIHP-i) Sales Kit"),
             ("MHP-I", "Micro Hire Purchase-i (MHP-i) Sales Kit"),
             ("GGSM3", "GGSM3 Sales Kit")]
    adv = ProgramAdvisor(StubLLMClient(), FakeRetriever([_chunk("x")], programs=progs))
    res = adv.handle("what about MHIP?", [], {})
    assert res["ui_action"]["type"] == "none"                   # a clarify, NOT the funnel
    assert not res.get("grounded")
    values = [s["value"] for s in res["suggestions"]]
    assert "what about MIHP-I?" in values and "what about MHP-I?" in values   # corrected follow-up, topic kept
    # and a normal, correctly-named question is NOT dragged into a typo clarify
    ok = adv.handle("what is the tenure for GGSM3?", [], {})
    assert ok.get("grounded") is True


def test_empty_index_falls_through_to_funnel():
    adv = ProgramAdvisor(StubLLMClient(), FakeRetriever([], programs=[]))
    res = adv.handle("tell me about GGSM3", [], {})
    assert res["ui_action"]["type"] == "show_program_options"
    assert not res.get("grounded")


_INDEX = [("GGSM3", "GGSM3 Sales Kit"), ("MIHP-I", "MIHP-i Sales Kit"), ("SJUM", "SJUM Sales Kit")]


def test_known_programme_without_kit_gets_graceful_response():
    # TERAJU is a real programme (products.yaml) but has no Sales Kit indexed — name it and offer the
    # SME team, instead of a funnel dump or an off-topic deflection.
    adv = ProgramAdvisor(StubLLMClient(), FakeRetriever([_chunk("x")], programs=_INDEX))
    res = adv.handle("explain to me about TERAJU", [], {})
    assert res["ui_action"]["type"] == "none"          # not the funnel
    assert not res.get("grounded")
    assert "TERAJU" in res["reply"]
    assert any("Sales" in s["label"] for s in res["suggestions"])


def test_indexed_programme_is_not_treated_as_missing():
    # A programme WITH a kit (GGSM3) must still be answered, not routed to "no materials".
    chunks = [_chunk("GGSM3 › Financing rate › The financing tenure is up to 5 years.")]
    adv = ProgramAdvisor(StubLLMClient(), FakeRetriever(chunks, programs=_INDEX))
    res = adv.handle("what is the tenure for GGSM3?", [], {})
    assert res.get("grounded") is True


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


def test_envelope_carries_bullet_flag_to_response():
    # The bullet flag must survive AnswerSentence (extra fields are dropped by default) so the UI
    # can group items into a <ul>. A prose sentence has bullet=None; a list item has bullet=True.
    from app.orchestrator.graph import _assemble
    final = {
        "session_id": "s1", "reply": "You'll need to prepare:\n- Your IC and SSM.",
        "sentences": [{"text": "You'll need to prepare:", "cites": [1]},
                      {"text": "Your IC and SSM.", "cites": [1], "bullet": True}],
        "grounded": True, "citations": [],
        "intent": {"primary": "INS-02", "confidence": 0.9},
        "trace_id": "t", "route": "", "rule_version": "", "timestamp": "", "decision_inputs": {},
    }
    resp = _assemble(final, taxonomy=None)
    assert resp.sentences[0].bullet is None       # lead-in: prose
    assert resp.sentences[1].bullet is True        # list item
