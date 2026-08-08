"""Deterministic programme-name gate — rescues "what is GGSM/MIHP/TERAJU" from being filed as
off-topic (the classifier is blind to the acronyms) so it reaches the programme advisor."""
import types

from app.agents.program_advisor.program_match import mentions_program
from app.config.loader import load_config
from app.orchestrator.nodes import classify_node

_cfg = load_config()


def _deps(primary, confidence=0.9):
    classifier = types.SimpleNamespace(
        classify=lambda msg, hist: {"primary": primary, "confidence": confidence, "secondary": None}
    )
    return types.SimpleNamespace(classifier=classifier, config=_cfg)


def test_mentions_program_detects_acronyms_and_naming_drift():
    for m in ["what is GGSM", "what is the tenure for GGSM3", "explain MIHP", "what about MHP-i",
              "tell me about TERAJU", "is SJUM available", "do you have BIZJAMIN"]:
        assert mentions_program(m), m
    for m in ["what's your fixed deposit rate", "how's the weather today", "I want a savings account",
              "hello there", "I'm proud of my business"]:  # "proud" must NOT match a programme
        assert not mentions_program(m), m


def test_classify_rescues_offtopic_programme_query_to_ins02():
    # LLM filed "what is GGSM" as off-topic (OOS-05) -> rescued to a programme query (INS-02).
    out = classify_node({"message": "what is GGSM", "history": []}, _deps("OOS-05"))
    assert out["intent"]["primary"] == "INS-02"
    assert out["intent"]["confidence"] >= 0.9


def test_classify_does_not_hijack_genuine_in_scope_intent():
    # "I'd like to apply for GGSM" is INS-05 (apply) — naming a programme must NOT turn it into a query.
    out = classify_node({"message": "I'd like to apply for GGSM", "history": []}, _deps("INS-05"))
    assert out["intent"]["primary"] == "INS-05"


def test_classify_leaves_non_programme_offtopic_alone():
    out = classify_node({"message": "what's your fixed deposit rate", "history": []}, _deps("OOS-01"))
    assert out["intent"]["primary"] == "OOS-01"


def test_classify_yields_to_specific_topic_named_with_a_programme():
    # "fixed deposit rate for GGSM?" is a specific-topic OOS intent (OOS-01, specific_topic in
    # intents.yaml) — the stray "GGSM" must not hijack it into a programme query; it stays
    # out-of-scope (→ the smart deflection). Config declares the boundary, not code.
    out = classify_node({"message": "what's your fixed deposit rate for GGSM?", "history": []}, _deps("OOS-01"))
    assert out["intent"]["primary"] == "OOS-01"


def test_classify_still_rescues_vague_offtopic_naming_a_programme():
    # A vague off-topic bucket (OOS-05, not specific_topic) — where an unrecognised acronym lands —
    # still rescues to the advisor even at high confidence.
    out = classify_node({"message": "tell me a joke about GGSM", "history": []}, _deps("OOS-05", 0.95))
    assert out["intent"]["primary"] == "INS-02"
