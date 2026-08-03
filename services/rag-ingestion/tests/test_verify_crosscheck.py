"""Check 3 — cross-check against product truth (Change Brief §4, §6).

An extracted financing_size_max of RM 5,000,000 for SJUM flags as
CONTRADICTS_PRODUCT_CONFIG, because products.yaml (the workbook quantum table the
chat service owns) caps SJUM at RM 2,000,000. The expected value is read from the
real products.yaml via the program_crosscheck map, so the whole wiring is tested.
"""
from config.settings import get_settings
from pipeline.verify import _check_page, _load_products, _load_sanity

BOUNDS = _load_sanity()["bounds"]


def _sjum_product_max() -> int:
    sanity = _load_sanity()
    products = _load_products(get_settings())
    key = sanity["program_crosscheck"]["SJUM"]      # SJUM -> SJUM
    return products[key]["max"]


def test_sjum_config_is_two_million():
    # Guards the fixture assumption: products.yaml really says RM 2m for SJUM.
    assert _sjum_product_max() == 2000000


def test_sjum_max_of_five_million_flags_as_contradiction():
    verbatim = "RM 5 million"
    md = f"## Financing size\n\nUp to {verbatim}.\n"
    facts = {
        "program_code": "SJUM",
        "self_consistency": "identical",
        "facts": [{"field": "financing_size_max", "value": 5000000, "unit": "MYR", "verbatim": verbatim}],
    }
    result = _check_page(1, md, facts, {"program_code": "SJUM"}, BOUNDS,
                         product_max=_sjum_product_max())
    assert result["status"] == "flag"
    assert any(c["category"] == "contradicts_product_config" for c in result["checks"])
