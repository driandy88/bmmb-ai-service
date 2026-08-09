"""
Phase 1 — the one-`understand()` path in program_advisor (settings.use_understand ON).

Proves the ENGINE: given an understanding, the advisor reuses the same terminal handlers and covers
the cases the current path kept breaking — anaphora-safe disambiguation with topic-carrying chips,
offer replies read by MEANING (incl. Malay), honest unindexed redirect, funnel, and clarify. The
offline stub is a scaffold (its rewrite is a dumb substring match); the topic-carry and Malay program
resolution are proven LIVE via `poc/run.py --live`. Here we lock the wiring deterministically.
"""
import pytest

import app.agents.program_advisor.advisor as advmod
from app.agents.program_advisor.advisor import ProgramAdvisor
from app.agents.rag.retriever import RetrievalChunk, Retriever
from app.integrations.llm import StubLLMClient, normalize_understanding


class FakeRetriever(Retriever):
    def __init__(self, chunks, programs=None):
        self._chunks = chunks
        self._programs = programs or []

    def retrieve(self, query, corpus, top_k=5, *, program_code=None, channel="customer"):
        return self._chunks[:top_k]

    def programs(self):
        return self._programs


def _chunk(text):
    return RetrievalChunk(
        text=text, corpus="program", ref="gs://bmmb/SalesKit_MIHP-i.pdf#page=2", score=0.71,
        metadata={"doc_id": "mihp_i", "doc_title": "MIHP-i Sales Kit",
                  "section": "documents", "access_tier": "customer"})


PROGS = [("MIHP-I", "Muamalat Industrial Hire Purchase (MIHP-i) Sales Kit"),
         ("MHP-I", "Muamalat Hire Purchase-i (MHP-i) Sales Kit"),
         ("GGSM3", "Government Guarantee Scheme Madani 3 (GGSM3) Sales Kit")]


class _Settings:
    use_understand = True
    new_application_url = "https://apply.example/new"


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    """Flip the whole file onto the understand path."""
    monkeypatch.setattr(advmod, "get_settings", lambda: _Settings())


def _adv(llm=None, chunks=None):
    return ProgramAdvisor(llm or StubLLMClient(), FakeRetriever(chunks or [_chunk("x")], PROGS))


def test_named_program_gets_grounded_answer():
    res = _adv(chunks=[_chunk("MIHP-i › documents › SSM, bank statements, IC.")]).handle(
        "documents for MIHP-I", [], {})
    assert res["grounded"] is True
    assert res["ui_action"]["type"] == "none"


def test_disambiguation_carries_the_topic_onto_the_chips():
    # When the model condenses "what about MHIP" (mid-tenure) into a topic-carrying query, each chip
    # re-sends that query with just the programme corrected — so the pick stays on TENURE.
    class _LLM(StubLLMClient):
        def understand(self, message, history=None, *, programs=None, stage=""):
            return normalize_understanding({
                "turn_type": "program_info", "attribute": "tenure",
                "retrieval_query": "financing tenure for MHIP",
                "disambiguation": {"needed": True, "candidates": ["MHP-I", "MIHP-I"]}})
    res = _adv(_LLM()).handle("what about MHIP?", [], {})
    assert res["ui_action"]["type"] == "none" and not res.get("grounded")
    values = [s["value"] for s in res["suggestions"]]
    assert any("tenure" in v.lower() for v in values)                 # topic kept
    assert any("MIHP-I" in v for v in values) and any("MHP-I" in v for v in values)


def test_ambiguous_typo_caught_by_the_fuzzy_backstop():
    # Even when the model flags nothing (the stub), the deterministic near-match still clarifies.
    res = _adv().handle("what about MHIP?", [], {})
    assert res["ui_action"]["type"] == "none" and not res.get("grounded")
    values = [s["value"] for s in res["suggestions"]]
    assert any("MIHP-I" in v for v in values) and any("MHP-I" in v for v in values)


def test_offer_apply_read_by_meaning_in_malay():
    res = _adv().handle("boleh saya mohon sekarang?", [], {"last_program": "MIHP-I"}, stage="program_offer")
    assert res["ui_action"]["type"] == "open_application_link"


def test_offer_decline_read_by_meaning_in_malay():
    res = _adv().handle("tak nak dulu, tengok-tengok je", [], {"last_program": "MIHP-I"}, stage="program_offer")
    assert "no problem" in res["reply"].lower()
    assert res["ui_action"]["type"] == "none" and not res.get("grounded")


def test_what_else_lists_the_catalog():
    # "what else do you have?" now LISTS the programmes (catalog) instead of asking funnel questions.
    res = _adv().handle("what else do you have?", [], {"last_program": "MIHP-I"}, stage="program_offer")
    assert res["stage"] == "program_done" and not res.get("grounded")
    assert res["ui_action"]["type"] == "none"                          # a listed answer, not the funnel
    assert "financing" in res["reply"].lower()


def test_besides_named_programmes_lists_the_others():
    res = _adv().handle("any other financing products besides GGSM3 and MIHP-I?", [], {})
    assert res["stage"] == "program_done" and not res.get("grounded")
    assert res["ui_action"]["type"] == "none"                          # catalog list, not a lookup of GGSM3


def test_loan_question_gets_catalog_with_financing_framing():
    # "loan" from a customer → don't refuse or funnel; list what we offer, framed as Islamic financing.
    res = _adv().handle("do you have a loan product?", [], {})
    assert res["stage"] == "program_done" and not res.get("grounded")
    assert 'financing' in res["reply"].lower() and 'loan' in res["reply"].lower()  # gently reframes


def test_attribute_followup_inherits_last_program():
    class _LLM(StubLLMClient):
        def understand(self, message, history=None, *, programs=None, stage=""):
            return normalize_understanding({"turn_type": "program_info", "attribute": "tenure",
                                            "retrieval_query": "financing tenure for MIHP-i"})
    res = _adv(_LLM(), chunks=[_chunk("MIHP-i › tenure › up to 7 years.")]).handle(
        "and the tenure?", [], {"last_program": "MIHP-I"}, stage="program_offer")
    assert res["grounded"] is True                                     # answered, scoped to MIHP-i


def test_known_but_unindexed_programme_is_a_named_redirect():
    res = _adv().handle("what's the profit rate for TERAJU?", [], {})
    assert "TERAJU" in res["reply"] and not res.get("grounded")        # named, not fabricated
    assert any("Sales" in s["label"] for s in res["suggestions"])


def test_named_unindexed_survives_a_misresolve_instead_of_funnelling():
    # The screenshot bug: after a GGSM answer + catalog list, "what about SRF" — SRF is a real
    # programme we DON'T index. If the model anaphora-resolves it to an indexed code (GGSM3) and the
    # index returns nothing for the SRF query, we must NOT ambush with the Program Finder: name SRF
    # honestly and offer Sales. The redirect is driven off the message, not the model's program_code.
    class _Misresolve(StubLLMClient):
        def understand(self, message, history=None, *, programs=None, stage=""):
            return normalize_understanding({"turn_type": "program_info", "program_code": "GGSM3",
                                            "program_status": "indexed", "retrieval_query": "overview of SRF"})
    adv = ProgramAdvisor(_Misresolve(), FakeRetriever([], PROGS))       # index has nothing for the query
    res = adv.handle("what about SRF", [], {"last_program": "GGSM3"}, stage="program_done")
    assert res["ui_action"]["type"] == "none"                          # NOT show_program_options
    assert "SRF" in res["reply"] and not res.get("grounded")
    assert any("Sales" in s["label"] for s in res["suggestions"])


def test_unhandled_turn_gets_soft_help_not_the_funnel():
    # A turn that maps to no programme, no action, and no guidance request must NOT trigger the
    # wizard — offer a warm hand and let the customer steer. The funnel is now opt-in.
    class _Smalltalk(StubLLMClient):
        def understand(self, message, history=None, *, programs=None, stage=""):
            return normalize_understanding({"turn_type": "smalltalk"})
    res = _adv(_Smalltalk()).handle("ok cool thanks", [], {})
    assert res["ui_action"]["type"] == "none"                          # not the Program Finder
    assert res["stage"] == "program_done" and not res.get("grounded")
    labels = [s["label"].lower() for s in res["suggestions"]]
    assert any("choose" in l for l in labels)                          # guided funnel offered, not forced


def test_funnel_from_natural_language_purpose():
    res = _adv().handle("I need working capital", [], {})
    assert res["stage"] == "funnel_amount"                             # purpose captured → asks amount


def test_clarify_when_genuinely_unsure():
    class _LLM(StubLLMClient):
        def understand(self, message, history=None, *, programs=None, stage=""):
            return normalize_understanding({"turn_type": "unclear", "clarify": {
                "needed": True, "question": "Happy to help — what will the financing be for, and roughly how much?"}})
    res = _adv(_LLM()).handle("how much can I get?", [], {})
    assert "what will the financing be for" in res["reply"].lower()
    assert not res.get("grounded")


# ── Phase 2: one read feeds routing AND the advisor ───────────────────────────
def test_handle_reuses_the_passed_understanding_without_recomputing():
    # In Phase 2 the signal is computed once in classify_node and threaded to the advisor. If it's
    # passed, the advisor must NOT call understand() again.
    class _NoUnderstand(StubLLMClient):
        def understand(self, *a, **k):
            raise AssertionError("understand() must not be called when the signal is passed in")
    sig = normalize_understanding({"turn_type": "program_info", "program_code": "MIHP-I",
                                   "program_status": "indexed", "attribute": "documents",
                                   "retrieval_query": "documents required for MIHP-i"})
    adv = ProgramAdvisor(_NoUnderstand(), FakeRetriever([_chunk("MIHP-i docs")], PROGS))
    res = adv.handle("documents for MIHP-i", [], {}, understanding=sig)
    assert res["grounded"] is True


def test_capability_question_is_answered_not_funnelled():
    # "can you compare two programmes?" is a question about what the bot can do — answer it, don't
    # open the Program Finder.
    res = _adv().handle("can you compare two programmes?", [], {})
    assert res["stage"] == "program_done" and not res.get("grounded")
    assert "compare" in res["reply"].lower()
    assert res["ui_action"]["type"] == "none"                # not show_program_options (the funnel)
    labels = [s["label"] for s in res["suggestions"]]
    assert any("eligibility" in l.lower() for l in labels)


def test_capability_question_routes_to_the_advisor():
    # A capability turn must REACH the advisor: the stub classifier can mislabel "compare" as OOS-03,
    # so understand() forces intent to INS-02 (→ ROUTE-PROGRAM).
    res = StubLLMClient().understand("can you compare two programmes?", [], programs=[])
    assert res["turn_type"] == "capability"
    assert res["intent"]["primary"] == "INS-02"


def test_capability_regex_does_not_hijack_a_normal_programme_question():
    # "can you check … " triggers the capability verb, but a resolved programme means it's a real
    # question, not a meta one — so it must NOT become a capability turn.
    sig = StubLLMClient().understand("can you check the tenure for MIHP-I?", [],
                                     programs=[("MIHP-I", "MIHP-i Sales Kit")])
    assert sig["turn_type"] != "capability"


def test_classify_via_understand_stores_signal_and_keeps_routing_parity():
    # classify_node with the flag ON produces the same routing intent as the classic path, and stores
    # the one-read signal for the advisor to reuse. (Stub delegates intent to classify_intent.)
    from types import SimpleNamespace
    from app.config.loader import load_config
    from app.agents.intent_classifier.classifier import IntentClassifier
    from app.orchestrator.nodes import classify_node

    stub, cfg, ret = StubLLMClient(), load_config(), FakeRetriever([_chunk("x")], PROGS)
    state = {"message": "what is the profit rate for MIHP-I?", "history": [], "stage": None}
    on = SimpleNamespace(settings=SimpleNamespace(use_understand=True), llm=stub, retriever=ret,
                         classifier=IntentClassifier(stub), config=cfg)
    off = SimpleNamespace(settings=SimpleNamespace(use_understand=False), llm=stub, retriever=ret,
                          classifier=IntentClassifier(stub), config=cfg)
    out_on, out_off = classify_node(dict(state), on), classify_node(dict(state), off)
    assert out_on["understanding"]["intent"]                # the one-read signal is captured
    assert out_on["intent"] == out_off["intent"]            # routing is identical to the classic path
