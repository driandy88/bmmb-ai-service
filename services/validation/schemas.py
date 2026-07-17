"""Output schemas for the agentic validation pipeline (agent.py)."""

from typing import Dict, List

from pydantic import BaseModel, computed_field

from .engine import CheckResult, ValidationReport
from .extraction_adapter import AdapterWarning


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
    # Only ever populated by run_agentic_validation_from_extraction() --
    # empty for a bundle built by hand and passed to run_agentic_validation()
    # directly, since there's no adapter step in that path. Each entry
    # states a data anomaly (null value / array misalignment) the adapter
    # hit while building the bundle, with current_state vs expected_state
    # spelled out -- see extraction_adapter.py's module docstring. Listed
    # first: these are inputs the deterministic/AI checks
    # below were run against, not a result of them.
    adapter_warnings: List[AdapterWarning] = []
    deterministic: ValidationReport
    ai_findings: List[AIFinding]
    narrative: str

    @computed_field
    @property
    def results_by_document(self) -> Dict[str, List[CheckResult]]:
        """Same grouping as deterministic.results_by_document, surfaced at
        the top level so callers don't have to reach into `deterministic`
        for it."""
        return self.deterministic.results_by_document
