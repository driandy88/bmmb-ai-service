"""
Thin wrapper around the google-genai SDK, authenticated via Vertex AI.

Auth comes from Application Default Credentials (ADC) -- automatically the
attached service account when running on Cloud Run, or whatever
`gcloud auth application-default login` set up when running locally. No API
key is ever passed around, stored, or logged.

Isolated in its own module so tests can monkeypatch `run_extraction` without
needing real credentials or network access, and so the model name / system
instruction / project config live in exactly one place.
"""
import json
import os

from google import genai
from google.genai import types

DEFAULT_MODEL = "gemini-2.5-flash"

# Required: the GCP project whose Vertex AI API this service calls, and the
# region to call it in. Set these as environment variables on the service
# (Cloud Run: --set-env-vars; local: in .env). No default project -- fail
# loudly rather than silently calling the wrong project.
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "asia-southeast1")

SYSTEM_INSTRUCTION = (
    "You are a precise document data extractor. "
    "Extract only the requested fields from the provided document. "
    "Return only the extracted data as JSON matching the provided schema. "
    "Use null for any field that is not present or cannot be determined with "
    "confidence — never guess or infer a plausible-looking value."
)


class GeminiConfigError(Exception):
    """Raised when required config (e.g. GCP_PROJECT_ID) is missing."""


class GeminiCallError(Exception):
    """Raised when the Gemini API call itself fails (network, auth, quota, etc.)."""


class GeminiParseError(Exception):
    """Raised when Gemini returns a 200 but the body isn't valid JSON."""


def _get_client() -> genai.Client:
    if not GCP_PROJECT_ID:
        raise GeminiConfigError(
            "GCP_PROJECT_ID is not set. Set it as an environment variable "
            "(Cloud Run: --set-env-vars=GCP_PROJECT_ID=...; local: in .env)."
        )
    return genai.Client(vertexai=True, project=GCP_PROJECT_ID, location=VERTEX_LOCATION)


def run_extraction(
    file_bytes: bytes,
    mime_type: str,
    prompt: str,
    schema: dict,
    model: str = DEFAULT_MODEL,
):
    """Calls Gemini (via Vertex AI) with the document + prompt, constrained to `schema`.

    Returns the parsed JSON (dict or list, depending on the template's kind).
    Raises GeminiConfigError / GeminiCallError / GeminiParseError on failure —
    callers translate these into HTTP errors.
    """
    client = _get_client()
    try:
        response = client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
                prompt,
            ],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - deliberately broad, re-raised as our own type
        raise GeminiCallError(str(exc)) from exc

    try:
        return json.loads(response.text)
    except (ValueError, AttributeError) as exc:
        raise GeminiParseError(str(exc)) from exc