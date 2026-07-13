"""
BMMB document bundle validation agent.

One straightforward pipeline, not a chat/tool-calling loop:

    1. ValidationEngine runs directly in Python (engine.py) — deterministic,
       always computed, never trusted to an LLM echo.
    2. One Gemini call reviews that deterministic report (plus the
       canonical bundle and, if available, the raw pre-adapter extraction)
       and flags anything that looks like a false result — e.g. an adapter
       mapping bug — as an ai_finding. No tools, no chat session, no
       multi-turn function-calling: everything the model needs is given
       directly in the prompt, and the response is forced into strict JSON
       via response_json_schema.

AgenticValidationReport { deterministic, ai_findings, narrative }

Guardrail: ai_findings severity is hard-clamped in code (_clamp_severity)
to warning/needs_review only — even if the model returns something else,
it's coerced to needs_review. The AI review step can never override the
deterministic pass/fail verdict, because `deterministic` never comes from
the model in the first place — it's set from ValidationEngine before the
Gemini call even happens.

Usage (as a library, from a host application):
    from services.validation import ValidationBundle, run_agentic_validation
    report = run_agentic_validation(bundle, raw_extraction)

    # Deterministic-only, no Gemini call and no GCP credentials required:
    report = run_agentic_validation(bundle, raw_extraction, enable_ai_review=False)

    # Starting from raw extraction results instead of an already-built
    # bundle -- builds the bundle via extraction_adapter.py
    # first, then runs the same pipeline as above with that bundle as both
    # the canonical bundle AND the raw_extraction passed to the AI review
    # (so it can compare the adapter's mapping against the source). Only
    # extracted_by_template is required -- everything else is auto-derived
    # or defaulted (see build_validation_bundle()'s docstring):
    from services.validation import run_agentic_validation_from_extraction
    report = run_agentic_validation_from_extraction(
        {"SSM Form 24": {...}, "Bank Statements": {...}, ...},
    )

The Gemini call goes through Vertex AI, authenticated via Application
Default Credentials (`gcloud auth application-default login`, or a service
account in the runtime environment) rather than an API key. Vertex still
needs a GCP project and region, read from GCP_PROJECT_ID / VERTEX_LOCATION
(location defaults to asia-southeast1 if unset); the model name itself is
fixed below as MODEL_NAME.

Usage (standalone, from the repo root):
    export GCP_PROJECT_ID=...
    export VERTEX_LOCATION=...   (optional, defaults to asia-southeast1; or put both in a .env file)
    ./venv/bin/python -m services.validation.agent [bundle.json] [raw_extraction.json]
"""

import json
import logging
import os
import sys
import time
from datetime import date
from typing import Optional

import httpx
from dotenv import load_dotenv
from google import genai
from google.auth import exceptions as google_auth_errors
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import ValidationError

from .bundle import ValidationBundle
from .engine import ValidationEngine, ValidationReport
from .extraction_adapter import build_validation_bundle
from .schemas import AgenticValidationReport, AIReview

logger = logging.getLogger(__name__)

load_dotenv()  # Load GCP_PROJECT_ID/VERTEX_LOCATION from .env if present

MODEL_NAME = "gemini-2.5-flash"

_DEFAULT_VERTEX_LOCATION = "asia-southeast1"  # matches the Cloud Run region

# Transient Gemini failures worth a retry: 429 (rate limit) and any 5xx.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.0

SYSTEM_INSTRUCTION = """\
You are reviewing the output of a deterministic BMMB document bundle validation engine.

You will be given:
- The deterministic validation report: entity_name, entity_type, and a list
  of check results (each with check, passed, message, details). This is the
  final verdict on every rule — you cannot override it.
- The canonical document bundle the engine ran against.
- The raw, pre-adapter extraction for the same documents, if available.

Your job is to spot cases where a deterministic FAIL looks like it might be
caused by a data-mapping bug (the adapter that converts raw extraction into
the canonical bundle mis-mapped a field) rather than a genuine compliance
gap. For any failed or suspicious check, compare the canonical bundle's
fields to the raw extraction; if they don't line up in a way that suggests
a mapping bug, say so as a finding. If raw extraction isn't available, or
everything lines up, say so too — don't invent a conflict that isn't there.

Output:
- ai_findings: one entry per notable observation.
  - finding: a short (one-line) label for what was observed, e.g. "Consent
    form signatory does not match SSM shareholder record."
  - severity must be either "warning" (a real, confirmed compliance
    concern) or "needs_review" (looks like a data artifact, or requires a
    human to confirm). Never output "fail" or "pass" — those verdicts
    belong only to the deterministic report.
  - detail: written for the end user preparing/submitting the document
    bundle, not for a developer — this text is copied verbatim into
    user-facing documentation of what to fix. It must give them a clear,
    concrete, self-contained next step. Always include, in plain language:
      1. Which specific document(s) and field(s) are involved (e.g.
         "the consent_form for MOHD AIMAN BIN ZULKIFLI" or "the SSM Form
         49 shareholder listing"), by name/type, not internal IDs.
      2. What exactly looks wrong or inconsistent, stated as a comparison
         (e.g. "the NRIC on the consent form reads 880214-14-5124, but the
         SSM form lists 880214-14-5123 for the same person").
      3. A concrete step the user can take to verify it themselves — e.g.
         "open the consent form and confirm the NRIC printed next to the
         signature matches 880214-14-5123" or "check page 2 of the SSM
         Form 49 for the shareholder's listed NRIC" — something they can
         check against the physical/scanned document without needing
         access to this system's internals.
      4. What to do once verified — re-check the source document,
         re-upload a clearer scan, correct a typo, obtain a missing
         document, or confirm with the extraction team that the mapping is
         correct — whichever applies. If the likely cause is an
         adapter/extraction mapping bug rather than a real document
         problem, say that plainly instead of asking the user to
         re-submit paperwork that's probably fine.
- narrative: a short (2-4 sentence) summary of your overall assessment.
"""


def load_bundle(path: str) -> ValidationBundle:
    with open(path, "r") as f:
        raw = json.load(f)
    return ValidationBundle(**raw)


def load_raw_extraction(path: Optional[str]) -> Optional[dict]:
    if not path or not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return json.load(f)


def review_deterministic_report(
    client,
    bundle: ValidationBundle,
    deterministic: ValidationReport,
    raw_extraction: Optional[dict],
    adapter_warnings: Optional[list] = None,
) -> AIReview:
    """The single Gemini call: review the deterministic report for likely data-mapping artifacts.

    `adapter_warnings`, when the bundle came from
    run_agentic_validation_from_extraction(), is the list of
    AdapterWarnings the adapter itself already flagged (null values,
    array-length mismatches) while building the bundle -- handed to the
    model explicitly rather than making it re-derive the same anomalies by
    comparing the bundle against raw_extraction from scratch.
    """
    warnings_block = (
        "\n".join(
            f"- [{w.document_type}/{w.document_id}] {w.field}: {w.message} "
            f"(current: {w.current_state}; expected: {w.expected_state})"
            for w in adapter_warnings
        )
        if adapter_warnings
        else "None."
    )
    prompt = (
        "Deterministic validation report:\n"
        f"```json\n{deterministic.model_dump_json(indent=2)}\n```\n\n"
        "Canonical document bundle:\n"
        f"```json\n{bundle.model_dump_json(indent=2)}\n```\n\n"
        "Raw pre-adapter extraction:\n"
        f"```json\n{json.dumps(raw_extraction, indent=2) if raw_extraction else 'Not available.'}\n```\n\n"
        "Adapter warnings (data anomalies the extraction->bundle adapter already "
        "detected while building this bundle -- each states what it found vs what "
        "was expected; treat every one of these as worth a finding, not just the "
        "checks that failed):\n"
        f"{warnings_block}"
    )

    last_error: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    # response_json_schema (raw JSON Schema), not response_schema
                    # (the OpenAPI-style Schema object): the deterministic report
                    # embedded in the prompt has Dict[str, Any] detail fields
                    # elsewhere in this codebase, and response_schema's stricter
                    # conversion path rejects `additionalProperties`; keeping this
                    # consistent even though AIReview itself doesn't need it.
                    response_json_schema=AIReview.model_json_schema(),
                ),
            )
            return AIReview.model_validate_json(response.text)
        except google_auth_errors.GoogleAuthError:
            # Missing/invalid credentials (e.g. no `gcloud auth application-
            # default login` locally) won't fix itself on retry.
            raise
        except (genai_errors.APIError, httpx.HTTPError) as e:
            last_error = e
            # httpx.HTTPError (raw network errors like timeouts/connection
            # drops that never made it into an HTTP response) has no status
            # code; treat those as retryable transient failures too.
            status_code = getattr(e, "code", None)
            retryable = status_code in _RETRYABLE_STATUS_CODES or isinstance(e, httpx.HTTPError)
            if retryable and attempt < _MAX_RETRIES:
                delay = _RETRY_BACKOFF_SECONDS * (2**attempt)
                logger.warning(
                    "Gemini call failed (status=%s, attempt=%d/%d), retrying in %.1fs: %s",
                    status_code, attempt + 1, _MAX_RETRIES, delay, e,
                )
                time.sleep(delay)
                continue
            raise

    # Unreachable in practice: the loop above always either returns or raises.
    raise last_error  # pragma: no cover


def _clamp_severity(review: AIReview) -> AIReview:
    """Guardrail: ai_findings severity can only ever be warning/needs_review, never a pass/fail verdict."""
    for finding in review.ai_findings:
        if finding.severity not in ("warning", "needs_review"):
            finding.severity = "needs_review"
    return review


def run_agentic_validation(
    bundle: ValidationBundle,
    raw_extraction: Optional[dict] = None,
    enable_ai_review: bool = True,
    adapter_warnings: Optional[list] = None,
) -> AgenticValidationReport:
    """Run the deterministic engine, then optionally have Gemini review the result.

    enable_ai_review defaults to True (opt-out) to preserve existing behavior.
    Set it to False to skip the Gemini call entirely and get deterministic-only
    results back — in that case no GCP project/credentials are needed.

    `adapter_warnings` is normally left unset here -- it's populated by
    run_agentic_validation_from_extraction() (which has an adapter step to
    generate them from); passed through so the caller always gets the same
    AgenticValidationReport shape either way.
    """
    deterministic = ValidationEngine().run(bundle)
    adapter_warnings = adapter_warnings or []

    if not enable_ai_review:
        return AgenticValidationReport(
            deterministic=deterministic,
            ai_findings=[],
            narrative="(AI review disabled; showing deterministic results only.)",
            adapter_warnings=adapter_warnings,
        )

    project = os.environ.get("GCP_PROJECT_ID")
    location = os.environ.get("VERTEX_LOCATION", _DEFAULT_VERTEX_LOCATION)
    if not project:
        raise SystemExit(
            "GCP_PROJECT_ID must be set to call Gemini via Vertex AI. Put it "
            "in a .env file or:\n"
            "  export GCP_PROJECT_ID=your-project-id\n"
            "Also make sure Application Default Credentials are configured "
            "(`gcloud auth application-default login`).\n"
            "Alternatively, pass enable_ai_review=False to skip the AI review step."
        )
    client = genai.Client(vertexai=True, project=project, location=location)

    try:
        review = review_deterministic_report(client, bundle, deterministic, raw_extraction, adapter_warnings)
    except (ValidationError, ValueError) as e:
        logger.warning("AI review response failed to parse: %s", e)
        review = AIReview(
            ai_findings=[],
            narrative=f"(AI review failed to parse: {e}; showing deterministic results only.)",
        )
    except google_auth_errors.GoogleAuthError as e:
        logger.warning("AI review call failed (auth): %s", e)
        review = AIReview(
            ai_findings=[],
            narrative=(
                f"(AI review call failed due to a Vertex AI auth error: {e}; "
                "showing deterministic results only. Run `gcloud auth "
                "application-default login` if testing locally.)"
            ),
        )
    except (genai_errors.APIError, httpx.HTTPError) as e:
        logger.warning("AI review call failed: %s", e)
        review = AIReview(
            ai_findings=[],
            narrative=f"(AI review call failed: {e}; showing deterministic results only.)",
        )

    review = _clamp_severity(review)

    return AgenticValidationReport(
        deterministic=deterministic,
        ai_findings=review.ai_findings,
        narrative=review.narrative,
        adapter_warnings=adapter_warnings,
    )


def run_agentic_validation_from_extraction(
    extracted_by_template: dict,
    *,
    bundle_id: Optional[str] = None,
    system_date: Optional[date] = None,
    entity_type: Optional[str] = None,
    tenure_months: Optional[int] = None,
    repayment_frequency: Optional[str] = None,
    signature_present: Optional[bool] = None,
    tax_declaration_entity_name: Optional[str] = None,
    tax_declaration_fye_dates: Optional[list] = None,
    enable_ai_review: bool = True,
) -> AgenticValidationReport:
    """Entry point for callers that have raw extraction results, not an
    already-built ValidationBundle -- calls
    extraction_adapter.build_validation_bundle() to map
    `extracted_by_template` (one entry per POST /extract call, keyed by
    template name -- see examples/extraction_results_example.json)
    into a bundle, then runs the same deterministic + AI-review pipeline as
    run_agentic_validation().

    `extracted_by_template` is the only required argument -- a raw
    extraction results dump, unmodified, is a valid call on its own. The
    raw dict is also passed through as the AI review's raw_extraction
    context, so a suspicious deterministic result can still be cross-checked
    against the adapter's own mapping (the same mapping-bug detection
    run_agentic_validation() already does for a hand-built bundle -- see
    this module's docstring).

    Every other keyword arg exists only because extraction has no source
    attribute for it yet, or because the caller may want to override an
    auto-derived value -- see
    extraction_adapter.build_validation_bundle()'s
    docstring for exactly what's derived/defaulted and which attribute to
    add to close each gap for real. None of these ever raise: every
    omission and every null/misaligned value found while building the
    bundle is recorded as an AdapterWarning instead, so this always returns
    a complete report -- check the returned report's `adapter_warnings`
    even when every deterministic check passes, since a warning means a
    value was defaulted rather than genuinely present.
    """
    result = build_validation_bundle(
        extracted_by_template,
        bundle_id=bundle_id,
        system_date=system_date,
        entity_type=entity_type,
        tenure_months=tenure_months,
        repayment_frequency=repayment_frequency,
        signature_present=signature_present,
        tax_declaration_entity_name=tax_declaration_entity_name,
        tax_declaration_fye_dates=tax_declaration_fye_dates,
    )
    return run_agentic_validation(
        result.bundle, extracted_by_template,
        enable_ai_review=enable_ai_review, adapter_warnings=result.warnings,
    )


def main():
    bundle_path = sys.argv[1] if len(sys.argv) > 1 else "examples/sample_bundle.json"
    raw_extraction_path = sys.argv[2] if len(sys.argv) > 2 else None

    bundle = load_bundle(bundle_path)
    raw_extraction = load_raw_extraction(raw_extraction_path)

    report = run_agentic_validation(bundle, raw_extraction)
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
