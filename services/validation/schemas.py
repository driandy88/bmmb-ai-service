"""Output schemas for the agentic validation pipeline (agent.py)."""

from typing import List

from pydantic import BaseModel

from .engine import ValidationReport


class AIFinding(BaseModel):
    finding: str
    # NOTE: deliberately `str`, not `Literal["warning", "needs_review"]`.
    # If this were a Literal, model_validate_json would raise the instant
    # Gemini returns anything else (e.g. "fail"), and the caller's broad
    # except would discard the whole finding before _clamp_severity ever
    # got a chance to coerce it. Keeping this loose and enforcing the
    # allowed values only in _clamp_severity is what makes that guardrail
    # actually a guardrail instead of a crash.
    severity: str
    # User-facing, actionable guidance (see SYSTEM_INSTRUCTION in agent.py)
    # — written to be copied into documentation for the person fixing the
    # bundle, not a developer-facing debug message.
    detail: str


class AIReview(BaseModel):
    """What the single Gemini call returns: its take on the deterministic report, nothing else.

    `deterministic` is never part of this — it always comes straight from
    ValidationEngine in Python, never from the model.
    """

    ai_findings: List[AIFinding]
    narrative: str


class AgenticValidationReport(BaseModel):
    deterministic: ValidationReport
    ai_findings: List[AIFinding]
    narrative: str
