"""
Thin wrapper around the google-genai SDK. Isolated in its own module so tests
can monkeypatch `run_extraction` without needing a real API key or network
access, and so the model name / system instruction live in exactly one place.
"""
import json

from google import genai
from google.genai import types

DEFAULT_MODEL = "gemini-2.5-flash"

SYSTEM_INSTRUCTION = (
    "You are a precise document data extractor. "
    "Extract only the requested fields from the provided document. "
    "Return only the extracted data as JSON matching the provided schema. "
    "Use null for any field that is not present or cannot be determined with "
    "confidence — never guess or infer a plausible-looking value."
)


class GeminiCallError(Exception):
    """Raised when the Gemini API call itself fails (network, auth, quota, etc.)."""


class GeminiParseError(Exception):
    """Raised when Gemini returns a 200 but the body isn't valid JSON."""


def run_extraction(
    api_key: str,
    file_bytes: bytes,
    mime_type: str,
    prompt: str,
    schema: dict,
    model: str = DEFAULT_MODEL,
):
    """Calls Gemini with the document + prompt, constrained to `schema`.

    Returns the parsed JSON (dict or list, depending on the template's kind).
    Raises GeminiCallError / GeminiParseError on failure — callers translate
    these into HTTP errors.
    """
    client = genai.Client(api_key=api_key)
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
