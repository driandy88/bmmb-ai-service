"""AI review use case for deterministic validation reports."""

import json
import logging
import time
from typing import Optional

import httpx
from google.auth import exceptions as google_auth_errors
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from ..bundle import ValidationBundle
from ..engine import ValidationReport
from ..schemas import AIReview

logger = logging.getLogger(__name__)
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def review_deterministic_report(
    client,
    bundle: ValidationBundle,
    deterministic: ValidationReport,
    raw_extraction: Optional[dict],
    system_instruction: str,
    adapter_warnings: Optional[list] = None,
    model_name: str = "gemini-2.5-flash",
    max_retries: int = 2,
    retry_backoff_seconds: float = 1.0,
) -> AIReview:
    """Review deterministic results for likely extraction/mapping artifacts."""
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
    for attempt in range(max_retries + 1):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_json_schema=AIReview.model_json_schema(),
                ),
            )
            return AIReview.model_validate_json(response.text)
        except google_auth_errors.GoogleAuthError:
            raise
        except (genai_errors.APIError, httpx.HTTPError) as error:
            last_error = error
            status_code = getattr(error, "code", None)
            retryable = status_code in _RETRYABLE_STATUS_CODES or isinstance(error, httpx.HTTPError)
            if retryable and attempt < max_retries:
                delay = retry_backoff_seconds * (2**attempt)
                logger.warning(
                    "Gemini call failed (status=%s, attempt=%d/%d), retrying in %.1fs: %s",
                    status_code, attempt + 1, max_retries, delay, error,
                )
                time.sleep(delay)
                continue
            raise

    raise last_error  # pragma: no cover


def clamp_severity(review: AIReview) -> AIReview:
    """Prevent AI findings from becoming pass/fail compliance verdicts."""
    for finding in review.ai_findings:
        if finding.severity not in ("warning", "needs_review"):
            finding.severity = "needs_review"
    return review
