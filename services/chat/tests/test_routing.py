"""Sheet-9 precedence — routing.decide() unit tests (brief §10 Part A, §4.7).
Pure: fabricate classifier + guardrail outputs, assert the decided action."""
import pytest

from services.chat.app.config.loader import load_config
from services.chat.app.orchestrator import routing

cfg = load_config()
TH = cfg.settings.confidence_threshold  # 0.7


def decide(primary=None, secondary=None, confidence=0.9, flagged=False, category=None,
           awaiting=False, active=None):
    return routing.decide(
        cfg.taxonomy, cfg.responses,
        intent={"primary": primary, "secondary": secondary, "confidence": confidence},
        guardrail={"flagged": flagged, "category": category},
        threshold=TH, awaiting_clarification=awaiting, active_flow_route=active,
    )


# ── Adversarial precedence: in + adversarial -> refuse, suppress ─────────────
def test_in_plus_adversarial_suppresses_and_refuses():
    d = decide(primary="INS-07", secondary="ADV-02", confidence=0.95)
    assert d.action == routing.REFUSE
    assert d.refusal_ref == "R6"          # ADV-02 -> R6
    assert d.adversarial is True and d.primary_handler is None


def test_guardrail_flag_forces_refuse_even_for_in_scope():
    d = decide(primary="INS-04", confidence=0.99, flagged=True, category="ADV-01")
    assert d.action == routing.REFUSE and d.refusal_ref == "R6"


def test_eligibility_gaming_uses_r7():
    d = decide(primary="ADV-04", confidence=0.9)
    assert d.action == routing.REFUSE and d.refusal_ref == "R7"


# ── Multi-intent: in + out -> dispatch primary + append redirect ─────────────
def test_in_plus_out_answers_and_appends_redirect():
    d = decide(primary="INS-02", secondary="OOS-01", confidence=0.9)
    assert d.action == routing.DISPATCH
    assert d.primary_handler == "program_advisor"
    assert d.secondary.kind == "append_canned" and d.secondary.ref == "R1"


# ── Multi-intent: in + unclear -> dispatch + append R8 clarify ───────────────
def test_in_plus_unclear_appends_clarify():
    d = decide(primary="INS-02", secondary="AMB-03", confidence=0.9)
    assert d.action == routing.DISPATCH
    assert d.secondary.kind == "append_clarify" and d.secondary.ref == "R8"


# ── Confidence gate (Sheet 9.4) ──────────────────────────────────────────────
def test_low_confidence_clarifies_then_hands_off():
    assert decide(primary="INS-02", confidence=0.5).action == routing.CLARIFY
    # second unresolved turn -> handoff (never ask twice)
    assert decide(primary="INS-02", confidence=0.5, awaiting=True).action == routing.HANDOFF


def test_intent_driven_clarification_amb03():
    assert decide(primary="AMB-03", confidence=0.9).action == routing.CLARIFY


# ── OOS canned redirect ──────────────────────────────────────────────────────
def test_oos_investment_advice_is_canned_r3():
    d = decide(primary="OOS-04", confidence=0.9)
    assert d.action == routing.CANNED and d.primary_ref == "R3"


# ── In-scope dispatch mapping ────────────────────────────────────────────────
@pytest.mark.parametrize("cat,handler", [
    ("INS-01", "sales_handoff"), ("INS-02", "program_advisor"), ("INS-03", "guidelines"),
    ("INS-04", "eligibility"), ("INS-05", "initiate"), ("INS-06", "lookup"),
    ("INS-07", "lookup"), ("AMB-05", "guidelines"),
])
def test_route_table(cat, handler):
    assert decide(primary=cat, confidence=0.9).primary_handler == handler


# ── Active-flow continuation ─────────────────────────────────────────────────
def test_continuation_keeps_flow_on_low_confidence_answer():
    d = decide(primary=None, confidence=0.3, active="ROUTE-ELIGIBILITY")
    assert d.action == routing.DISPATCH and d.primary_ref == "ROUTE-ELIGIBILITY"


def test_continuation_yields_to_clear_switch():
    d = decide(primary="INS-01", confidence=0.9, active="ROUTE-ELIGIBILITY")
    assert d.primary_handler == "sales_handoff"   # user switched to sales, honoured


def test_continuation_never_overrides_adversarial():
    d = decide(primary="INS-04", confidence=0.9, flagged=True, category="ADV-01",
               active="ROUTE-ELIGIBILITY")
    assert d.action == routing.REFUSE


def test_contact_flow_yields_to_ambiguous_financing_question():
    # Mid sales-contact flow (awaiting a location), "can I get financing?" classifies AMB-03 — that's
    # a financing question, not a location, so break out and CLARIFY instead of dumping the default
    # contact. (The awkward "No problem — here's the contact" bug.)
    d = decide(primary="AMB-03", confidence=0.9, active="ROUTE-BRANCH")
    assert d.action == routing.CLARIFY


def test_contact_flow_keeps_flow_on_location_answer():
    # A real location answer ("Penang") isn't a financing intent (None / low-confidence / OOS), so
    # the contact flow stays sticky and resolves the contact.
    d = decide(primary=None, confidence=0.3, active="ROUTE-BRANCH")
    assert d.action == routing.DISPATCH and d.primary_ref == "ROUTE-BRANCH"


def test_slotfill_flow_stays_sticky_on_ambiguous_answer():
    # Only the contact flow yields; true slot-fills (funnel / eligibility) legitimately take bare,
    # ambiguous answers, so an AMB reply there continues the flow rather than clarifying.
    d = decide(primary="AMB-03", confidence=0.9, active="ROUTE-ELIGIBILITY")
    assert d.action == routing.DISPATCH and d.primary_ref == "ROUTE-ELIGIBILITY"


def test_active_flow_yields_to_out_of_scope_topic_change():
    # The bug: at the post-answer offer (ROUTE-PROGRAM), a clear out-of-scope question ("what's your
    # fixed deposit rate?") was swallowed into the program handler and answered with "would you like
    # to apply?". A confident out-of-scope turn must break out and be deflected instead.
    d = decide(primary="OOS-01", confidence=0.9, active="ROUTE-PROGRAM")
    assert d.action == routing.CANNED and d.primary_ref == "R1"


def test_active_flow_stays_sticky_on_low_confidence_out_of_scope():
    # A bare offer reply mislabelled as OOS at LOW confidence must NOT break out — the offer handler
    # reads apply/decline itself. Only a confident topic change yields.
    d = decide(primary="OOS-01", confidence=0.5, active="ROUTE-PROGRAM")
    assert d.action == routing.DISPATCH and d.primary_ref == "ROUTE-PROGRAM"
