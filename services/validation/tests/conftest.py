"""
Puts the repo root on sys.path so tests can `from services.validation... import`
regardless of the directory pytest is invoked from — same convention already
used by services/validation/examples/*.py.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


def _load_json(name: str) -> dict:
    with open(EXAMPLES_DIR / name) as f:
        return json.load(f)


@pytest.fixture
def passing_bundle_raw() -> dict:
    """A bundle where every deterministic check should pass."""
    return _load_json("sample_bundle_passing.json")


@pytest.fixture
def failing_bundle_raw() -> dict:
    """A bundle with a real gap: no consent form for the second shareholder."""
    return _load_json("sample_bundle.json")


@pytest.fixture
def raw_extraction_conflict() -> dict:
    """Raw pre-adapter extraction that trips adapter.py's known consent-form mapping bug."""
    return _load_json("raw_extraction_example.json")
