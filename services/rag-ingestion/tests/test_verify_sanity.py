"""Check 2 — numeric sanity bounds (Change Brief §4, §6).

A financing MAXIMUM of RM 5,000 or RM 80,000,000 is implausible and flags; a
RM 2,000,000 maximum is in range and passes. Bounds come from config/sanity_rules.yaml,
so this also proves the config wiring, not just the code path.
"""
from pipeline.verify import _check_page, _load_sanity

BOUNDS = _load_sanity()["bounds"]


def _page_with_max(value: int, verbatim: str) -> dict:
    # md contains the verbatim string so cross-pass agreement passes and only the
    # bounds check is under test. Same declared program on page + doc (no identity flag).
    md = f"## Financing size\n\nMaximum financing of {verbatim}.\n"
    facts = {
        "program_code": "MIHP-I",
        "self_consistency": "identical",
        "facts": [{"field": "financing_size_max", "value": value, "unit": "MYR", "verbatim": verbatim}],
    }
    return _check_page(1, md, facts, {"program_code": "MIHP-I"}, BOUNDS, product_max=None)


def test_financing_max_too_low_flags():
    result = _page_with_max(5000, "RM 5,000")
    assert result["status"] == "flag"
    assert any(c["category"] == "out_of_bounds" for c in result["checks"])


def test_financing_max_too_high_flags():
    result = _page_with_max(80000000, "RM 80,000,000")
    assert result["status"] == "flag"
    assert any(c["category"] == "out_of_bounds" for c in result["checks"])


def test_financing_max_in_range_passes():
    result = _page_with_max(2000000, "RM 2,000,000")
    assert result["status"] == "pass", result["checks"]
