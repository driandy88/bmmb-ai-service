"""Smart clarify — an ambiguous message gets a targeted question + tappable in-scope options,
with the default R8 line + preset chips as the offline/failure fallback. Still ask-once."""
import types

from services.chat.app.config.loader import load_config
from services.chat.app.integrations.llm import StubLLMClient
from services.chat.app.orchestrator.nodes import clarify_node


def _state(msg="I need funding", primary="AMB-03"):
    return {"message": msg, "history": [], "intent": {"primary": primary}}


def test_clarify_falls_back_to_default_line_and_chips_offline():
    # The stub offers no smart clarify -> default R8 line + preset chips; still ask-once.
    deps = types.SimpleNamespace(llm=StubLLMClient(), config=load_config())
    out = clarify_node(_state(), deps)
    assert out["awaiting_clarification"] is True
    assert out["reply"]                              # R8 clarification line
    assert out["suggestions"]                        # preset explore chips now present
    assert out["ui_action"]["type"] == "none"


class _ClarifyLLM:
    def generate_clarify(self, message, history):
        return {
            "question": "What's the financing mainly for?",
            "options": [
                {"label": "Working capital", "value": "I need working capital financing"},
                {"label": "Equipment", "value": "I need financing for equipment"},
            ],
        }


def test_clarify_uses_smart_question_and_options_when_available():
    deps = types.SimpleNamespace(llm=_ClarifyLLM(), config=load_config())
    out = clarify_node(_state(), deps)
    assert out["reply"] == "What's the financing mainly for?"
    assert [s["label"] for s in out["suggestions"]] == ["Working capital", "Equipment"]
    assert out["awaiting_clarification"] is True     # still ask-once — hands off next turn if unresolved
