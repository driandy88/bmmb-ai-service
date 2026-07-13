"""
Demonstrates the blind spot the agentic path exists to cover.

examples/buggy_adapter_demo.py's _adapt_consent_form has a deliberate bug:
it maps a consent form's "Authorized Names" raw field into entity_name
instead of individual_name/nric_passport (see buggy_adapter_demo.py's
module docstring for the full story). test2_conflict.json is the canonical bundle
that bug produces — run `./venv/bin/python -m examples.generate_conflict_demo`
to regenerate it from raw_extraction_example.json if either changes.

This script runs the same bundle through two paths and prints both:
  1. ValidationEngine alone — deterministic-only, no access to raw
     extraction. It reports a real-looking FAIL: a missing consent form
     and an entity name mismatch.
  2. The full pipeline (services.validation.agent.run_agentic_validation)
     — same deterministic engine, plus one Gemini call given the raw
     extraction directly in its prompt. It should still surface the same
     deterministic FAIL verbatim (the review step never overrides that
     verdict), but *also* flag in ai_findings that the failure looks like
     an adapter mapping bug, not a real missing consent — because the raw
     data shows Signature Captured=true for the same person the
     deterministic check says has no consent.

Usage (from repo root, or just "Run" this file directly in an IDE):
    export GCP_PROJECT_ID=...
    export VERTEX_LOCATION=...   (optional, defaults to asia-southeast1; or put both in a .env file)
    gcloud auth application-default login
    ./venv/bin/python -m examples.test_conflict_example
"""

import os
import sys

# Allow running this file directly (e.g. an IDE's "Run" button executes it
# as a plain script, not `-m examples.test_conflict_example`) by putting the
# repo root on sys.path ourselves — otherwise `services` isn't importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.validation import ValidationEngine
from services.validation import agent

EXAMPLES_DIR = os.path.dirname(os.path.abspath(__file__))


def section(title: str):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def main():
    bundle = agent.load_bundle(os.path.join(EXAMPLES_DIR, "test2_conflict.json"))
    raw_extraction = agent.load_raw_extraction(os.path.join(EXAMPLES_DIR, "raw_extraction_example.json"))

    section("1. Deterministic engine only (no raw extraction access)")
    deterministic_report = ValidationEngine().run(bundle)
    for r in deterministic_report.results:
        if r.passed is None:
            continue  # skip "not applicable" noise for this comparison
        status = "PASS" if r.passed else "FAIL"
        print(f"[{status}] {r.check}: {r.message}")
    print(
        "\n-> Both failures above look like real compliance problems from here: "
        "no way to tell that MOHD AIMAN's consent form actually exists and is "
        "signed, just mapped into the wrong fields."
    )

    section("2. Agentic pipeline (same engine + raw extraction access)")
    report = agent.run_agentic_validation(bundle, raw_extraction)

    print("Deterministic verdict (echoed verbatim from Call 1, unchanged):")
    for r in report.deterministic.results:
        if r.passed is None:
            continue
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.check}: {r.message}")

    print("\nAI findings (severity hard-clamped to warning/needs_review):")
    if not report.ai_findings:
        print("  (none — the model didn't flag anything beyond the deterministic results)")
    for finding in report.ai_findings:
        print(f"  [{finding.severity}] {finding.finding}")
        print(f"      {finding.detail}")

    print(f"\nNarrative:\n  {report.narrative}")

    section("Summary")
    print(
        "The deterministic verdict is identical in both paths — the agent never\n"
        "overrides a pass/fail. What differs is whether a human reading the report\n"
        "knows the consent-form failure is worth investigating as a possible\n"
        "adapter bug versus treating it as a genuine missing-consent rejection."
    )


if __name__ == "__main__":
    main()
