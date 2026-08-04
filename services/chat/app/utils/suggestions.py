"""
Preset next-step suggestions for "done / dead-end" turns.

When a turn ends the thread (a sign-off) or can't move it forward (an
out-of-scope redirect), we re-surface the start-session presets as chips so the
customer always has a way forward — but WORDED to continue the conversation, not
to greet them again (see nodes.canned_node). Mirrors the frontend welcome
presets (ChatPanel `SUGGESTIONS`); each `value` is the message sent on click, so
it flows through normal routing exactly as if typed.
"""
from __future__ import annotations

from typing import Optional


def explore_suggestions(last_program: Optional[str] = None) -> list[dict]:
    """The four next-step chips. `Apply` names the programme just discussed when
    we know it (so it doesn't reset to a generic 'apply'), else stays generic."""
    apply = (
        {"label": f"Apply for {last_program}", "value": f"I'd like to apply for {last_program}"}
        if last_program else
        {"label": "Apply for financing", "value": "I'd like to apply for SME financing"}
    )
    return [
        {"label": "Explore programmes", "value": "What SME financing programmes do you offer?"},
        apply,
        {"label": "Talk to our team", "value": "I'd like to talk to your SME financing team"},
        {"label": "Track an application", "value": "I'd like to track my application"},
    ]
