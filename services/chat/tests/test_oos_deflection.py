"""Smart out-of-scope deflection: name the topic + redirect instead of the generic canned line.
`compose` returns the canned wording offline/on failure, so this only ever upgrades the reply."""
import types

from services.chat.app.config.loader import load_config
from services.chat.app.integrations.llm import StubLLMClient
from services.chat.app.orchestrator import routing
from services.chat.app.orchestrator.nodes import canned_node

cfg = load_config()


def _deps(llm):
    return types.SimpleNamespace(config=cfg, llm=llm)


def _state(cat_id, ref, message):
    d = routing.RoutingDecision(action=routing.CANNED, route_label="", primary_cat_id=cat_id, primary_ref=ref)
    return {"message": message, "history": [], "slots": {}, "decision": d}


def test_oos_deflection_uses_smart_specific_reply():
    class _LLM(StubLLMClient):
        def compose(self, prompt_name, *, message, history, fallback, **vars):
            return f"Fixed deposit rates are outside what I handle — I focus on SME financing here."

    out = canned_node(_state("OOS-01", "R1", "what's your fixed deposit rate?"), _deps(_LLM()))
    assert "fixed deposit" in out["reply"].lower()          # names what they actually asked
    assert out["suggestions"]                                # explore chips still attached
    assert out["ui_action"]["type"] == "none"
    assert out["stage"] == "redirect"


def test_oos_deflection_falls_back_to_canned_offline():
    seen = {}

    class _LLM(StubLLMClient):
        def compose(self, prompt_name, *, message, history, fallback, **vars):
            seen.update(prompt=prompt_name, fallback=fallback, category=vars.get("category"))
            return fallback  # what the stub / a failed Vertex call does

    out = canned_node(_state("OOS-01", "R1", "what's your fixed deposit rate?"), _deps(_LLM()))
    assert seen["prompt"] == "oos_deflection"
    assert seen["fallback"]                                  # a canned R1 variant was the fallback
    assert seen["category"]                                  # category name passed to the prompt
    assert out["reply"] == seen["fallback"]                  # offline → canned wording, unchanged


def test_social_close_is_not_deflected():
    # A sign-off (SOC-03, type=social) keeps its own reworded close — compose is never invoked.
    class _LLM(StubLLMClient):
        def compose(self, *a, **k):
            raise AssertionError("compose must not run for a social close")

    out = canned_node(_state("SOC-03", "R11", "no thanks, that's all"), _deps(_LLM()))
    assert "help" in out["reply"].lower()
    assert out["suggestions"]
