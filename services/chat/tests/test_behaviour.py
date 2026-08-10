"""behaviour.md — the swappable brand-voice preamble, wired into every generation via system_prompt."""
from services.chat.app.utils import prompts
from services.chat.app.utils.prompts import load_prompt, system_prompt


def test_behaviour_voice_is_in_every_generation_prompt():
    sp = system_prompt("answer_synthesis")
    # the safety preamble, the brand voice, AND the task prompt are all present, in that order
    assert "advisory only" in sp.lower()                       # from response_style (safety)
    assert "Better lives, together" in sp                       # from behaviour.md (brand voice)
    assert sp.index("Better lives, together") < sp.index("Sources")  # voice before the task body


def test_behaviour_carries_the_brand_personality_and_tone():
    b = load_prompt("behaviour")
    for trait in ("Trustworthy", "Thoughtful", "Passionate", "Dynamic but rooted",
                  "Simple", "Conversational", "Optimistic", "Casual"):
        assert trait in b, trait


def test_system_prompt_degrades_gracefully_without_behaviour(monkeypatch):
    # Deleting/renaming behaviour.md must not break generation — the safety preamble still stands.
    monkeypatch.setattr(prompts, "_optional_prompt", lambda name: "")
    sp = system_prompt("clarify")
    assert "advisory only" in sp.lower()
    assert "Better lives, together" not in sp
