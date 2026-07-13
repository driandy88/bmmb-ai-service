"""
Regenerates test2_conflict.json from raw_extraction_example.json.

Run this after editing raw_extraction_example.json or
buggy_adapter_demo.py's mapping logic.

Usage (from repo root, or just "Run" this file directly in an IDE):
    ./venv/bin/python -m examples.generate_conflict_demo
"""

import json
import os
import sys

# Allow running this file directly (e.g. an IDE's "Run" button executes it
# as a plain script, not `-m examples.generate_conflict_demo`) by putting
# the repo root on sys.path ourselves — otherwise `services` isn't importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.validation.examples.buggy_adapter_demo import adapt_raw_extraction

EXAMPLES_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    with open(os.path.join(EXAMPLES_DIR, "raw_extraction_example.json")) as f:
        raw = json.load(f)

    bundle = adapt_raw_extraction(raw)

    out_path = os.path.join(EXAMPLES_DIR, "test2_conflict.json")
    with open(out_path, "w") as f:
        f.write(bundle.model_dump_json(indent=2))

    print(f"Wrote {out_path}")
    print(bundle.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
