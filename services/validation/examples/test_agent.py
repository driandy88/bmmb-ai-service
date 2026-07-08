"""
Step-by-step manual walkthrough of the BMMB validation agent's pipeline.

Unlike tests/test_rules.py (pytest, tests the rules in isolation), this
script exercises the actual pipeline end to end against sample_bundle.json
and prints what happens at every stage: the deterministic engine running
directly in Python, then the single Gemini review call, then the final
combined report.

For the scenario the raw-extraction review step exists to catch (an
adapter mapping bug that looks like a real compliance failure to the
deterministic engine alone), see test_conflict_example.py instead.

Usage (from repo root, or just "Run" this file directly in an IDE):
    export GCP_PROJECT_ID=...
    export VERTEX_LOCATION=...   (optional, defaults to asia-southeast1; or put both in a .env file)
    gcloud auth application-default login
    ./venv/bin/python -m examples.test_agent
"""

import os
import sys

# Allow running this file directly (e.g. an IDE's "Run" button executes it
# as a plain script, not `-m examples.test_agent`) by putting the repo root
# on sys.path ourselves — otherwise `services` isn't importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google import genai

from services.validation import ValidationEngine
from services.validation import agent

EXAMPLES_DIR = os.path.dirname(os.path.abspath(__file__))


def step(title: str):
    print(f"\n{'=' * 60}\nSTEP: {title}\n{'=' * 60}")


def main():
    # ------------------------------------------------------------------
    step("1. Check Vertex AI config")
    # ------------------------------------------------------------------
    project = os.environ.get("GCP_PROJECT_ID")
    location = os.environ.get("VERTEX_LOCATION", agent._DEFAULT_VERTEX_LOCATION)
    if not project:
        raise SystemExit(
            "GCP_PROJECT_ID must be set. Put it in a .env file or:\n"
            "  export GCP_PROJECT_ID=your-project-id"
        )
    print(f"Using Vertex AI project={project!r} location={location!r}.")

    # ------------------------------------------------------------------
    step("2. Load and schema-validate sample_bundle.json")
    # ------------------------------------------------------------------
    # In production this file is replaced by whatever the extraction agent
    # hands off — this script only cares that it's a bundle ready to be
    # validated, not how it was produced.
    bundle = agent.load_bundle(os.path.join(EXAMPLES_DIR, "sample_bundle.json"))
    raw_extraction = agent.load_raw_extraction(None)  # no raw extraction for this bundle
    print(f"Bundle '{bundle.bundle_id}' validated against the schema.")
    print(f"  system_date: {bundle.metadata.system_date}")
    print(f"  documents:   {len(bundle.extracted_documents)}")
    for doc in bundle.extracted_documents:
        print(f"    - {doc.document_id}: {doc.document_type}")

    # ------------------------------------------------------------------
    step("3. Run the deterministic engine directly (no LLM involved)")
    # ------------------------------------------------------------------
    deterministic = ValidationEngine().run(bundle)
    print(f"entity_name: {deterministic.entity_name}")
    print(f"entity_type: {deterministic.entity_type}")
    print(f"overall_passed: {deterministic.overall_passed}")
    for r in deterministic.results:
        status = "SKIP" if r.passed is None else ("PASS" if r.passed else "FAIL")
        print(f"  [{status}] {r.check}: {r.message}")

    # ------------------------------------------------------------------
    step("4. Single Gemini call reviews the deterministic report")
    # ------------------------------------------------------------------
    print("Sending request... (this calls the real Gemini API via Vertex AI)")
    client = genai.Client(vertexai=True, project=project, location=location)
    review = agent.review_deterministic_report(client, bundle, deterministic, raw_extraction)
    review = agent._clamp_severity(review)

    print(f"AI findings: {len(review.ai_findings)}")
    for finding in review.ai_findings:
        print(f"  [{finding.severity}] {finding.finding}")
        print(f"      {finding.detail}")
    print(f"\nNarrative:\n  {review.narrative}")

    # ------------------------------------------------------------------
    step("5. Combine into the final AgenticValidationReport shape")
    # ------------------------------------------------------------------
    # Reuses the deterministic report + review already fetched above
    # instead of calling run_agentic_validation again (which would repeat
    # both the engine run and the Gemini call).
    report = agent.AgenticValidationReport(
        deterministic=deterministic,
        ai_findings=review.ai_findings,
        narrative=review.narrative,
    )
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
