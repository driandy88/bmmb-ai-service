"""Program-identity guard (Change Brief §3.5, §6).

Inside the `mihp_i` document (program MIHP-I), a page whose Pass B reads the
program as MHP-I must flag — this is the highest-risk error in the corpus
(MHP-i / MIHP-i are different products). A matching program passes.
"""
from pipeline.verify import _check_page, _load_sanity

BOUNDS = _load_sanity()["bounds"]


def _page(page_program: str, doc_program: str) -> dict:
    verbatim = "RM 20,000"
    md = f"## Overview\n\nFinancing from {verbatim}.\n"
    facts = {
        "program_code": page_program,
        "self_consistency": "identical",
        "facts": [{"field": "financing_size_min", "value": 20000, "unit": "MYR", "verbatim": verbatim}],
    }
    return _check_page(1, md, facts, {"program_code": doc_program}, BOUNDS, product_max=None)


def test_mismatched_program_flags():
    result = _page(page_program="MHP-I", doc_program="MIHP-I")
    assert result["status"] == "flag"
    assert any(c["category"] == "program_identity" for c in result["checks"])


def test_matching_program_has_no_identity_flag():
    result = _page(page_program="MIHP-I", doc_program="MIHP-I")
    assert not any(c["category"] == "program_identity" for c in result["checks"])
