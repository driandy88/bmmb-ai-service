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
    "Extract the requested fields from the provided document(s). "
    "You may be given more than one source document at once, each preceded by a "
    "'--- Source document: <name> ---' marker; treat them as complementary parts of "
    "the same extraction (one document may supply fields another doesn't) and return "
    "a single combined result, not one result per document. "
    "For each extracted field, populate its entry in _locations with: "
    "real_page — the actual sequential page number of the source document/PDF file the value was "
    "found on, counting the first page of that document as 1 regardless of any printed page numbers "
    "or cover/title pages (page numbering restarts at 1 for each source document); "
    "shown_page — the page number or label as it is printed/displayed on that page itself (e.g. a "
    "footer or header page number, which may be a roman numeral, a section-restarted number, or "
    "differ from real_page due to unnumbered front matter) — null if the page has no visible label; "
    "section — the nearest heading or section title where the value was found; "
    "document — the exact source document name (matching one of the "
    "'--- Source document: <name> ---' markers) the value came from. "
    "Return only the extracted data as JSON matching the provided schema. "
    "Use null for any field or location that is not present or cannot be determined."
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
    files: list[tuple[str, str, bytes]],
    prompt: str,
    schema: dict,
    model: str = DEFAULT_MODEL,
):
    """Calls Gemini (via Vertex AI) with the document(s) + prompt, constrained to `schema`.

    `files` is a list of (filename, mime_type, file_bytes) tuples. Each is
    preceded by a "--- Source document: <name> ---" marker so the model can
    treat multiple uploads as one combined extraction (see SYSTEM_INSTRUCTION)
    and so _locations.document in the response can reference the right one.

    Returns the parsed JSON (dict or list, depending on the template's kind).
    Raises GeminiConfigError / GeminiCallError / GeminiParseError on failure —
    callers translate these into HTTP errors.
    """
    client = _get_client()
    contents = []
    for filename, mime_type, file_bytes in files:
        contents.append(f"--- Source document: {filename} ---")
        contents.append(types.Part.from_bytes(data=file_bytes, mime_type=mime_type))
    contents.append(prompt)

    try:
        response = client.models.generate_content(
            model=model,
            contents=contents,
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