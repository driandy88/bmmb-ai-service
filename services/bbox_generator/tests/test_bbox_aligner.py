"""
Tests for bbox_aligner's matching logic. These operate on hand-built word
lists (the same shape extract_words()/extract_words_image() produce) so they
don't need a real PDF/image fixture on disk.
"""
from services.bbox_generator.bbox_aligner import (
    _merge,
    _norm_token,
    align_fields,
    find_value_bbox,
    tokens_match,
)


def _word(text, page=1, x0=0.0, y0=0.0, x1=0.1, y1=0.05):
    return {"page": page, "text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


class TestNormToken:
    def test_strips_punctuation_and_casefolds(self):
        assert _norm_token("SEBASTIAN,") == "sebastian"

    def test_thousand_separators_stripped(self):
        assert _norm_token("1,000.00") == "100000"


class TestTokensMatch:
    def test_exact_match_case_insensitive(self):
        assert tokens_match("John", "john", "string")

    def test_date_type_always_false(self):
        # dates are matched at phrase level in find_value_bbox, not token level
        assert not tokens_match("2023-03-14", "2023-03-14", "date")


class TestFindValueBboxExact:
    def test_single_word_exact_match(self):
        words = [_word("Hello"), _word("SEBASTIAN", x0=0.2, x1=0.4), _word("World", x0=0.5)]
        bbox, quality = find_value_bbox("SEBASTIAN", words)
        assert quality == "exact"
        assert bbox == {"page": 1, "x0": 0.2, "y0": 0.0, "x1": 0.4, "y1": 0.05}

    def test_multi_word_exact_match_merges_boxes(self):
        words = [_word("John", x0=0.0, x1=0.1), _word("Smith", x0=0.1, x1=0.25)]
        bbox, quality = find_value_bbox("John Smith", words)
        assert quality == "exact"
        assert bbox == {"page": 1, "x0": 0.0, "y0": 0.0, "x1": 0.25, "y1": 0.05}

    def test_no_match_crossing_pages_rejected(self):
        words = [_word("John", page=1, x0=0.0, x1=0.1), _word("Smith", page=2, x0=0.0, x1=0.1)]
        bbox, quality = find_value_bbox("John Smith", words)
        assert bbox is None
        assert quality == "not_found"

    def test_null_value(self):
        assert find_value_bbox(None, []) == (None, "null_value")

    def test_empty_value(self):
        assert find_value_bbox("   ", [_word("anything")]) == (None, "empty_value")


class TestFindValueBboxDate:
    def test_date_phrase_different_format_matches(self):
        words = [_word("14"), _word("March", x0=0.1, x1=0.25), _word("2023", x0=0.25, x1=0.35)]
        bbox, quality = find_value_bbox("2023-03-14", words, data_type="date")
        assert quality == "exact_date"
        assert bbox["x0"] == 0.0 and bbox["x1"] == 0.35

    def test_unparseable_date_falls_back_to_fuzzy_match(self):
        # tokens_match() always returns False for data_type="date" (dates are
        # matched at phrase level only), so an unparseable date value falls
        # through past the exact-match pass straight to the fuzzy fallback.
        words = [_word("not-a-date")]
        bbox, quality = find_value_bbox("not-a-date", words, data_type="date")
        assert quality == "fuzzy"


class TestFindValueBboxFuzzy:
    def test_split_differently_still_matches(self):
        # value "AB1234" appears in the doc split across two words "AB" "1234"
        words = [_word("AB", x0=0.0, x1=0.05), _word("1234", x0=0.05, x1=0.15)]
        bbox, quality = find_value_bbox("AB1234", words)
        assert quality == "fuzzy"
        assert bbox["x0"] == 0.0 and bbox["x1"] == 0.15

    def test_ocr_misread_matches_within_tolerance(self):
        words = [_word("SEBASTIAI", x0=0.0, x1=0.3)]  # OCR misread of SEBASTIAN
        bbox, quality = find_value_bbox("SEBASTIAN", words, ocr_tolerance=0.8)
        assert bbox is not None
        assert quality.startswith("fuzzy_ocr")

    def test_not_found_when_absent(self):
        words = [_word("Completely"), _word("Unrelated")]
        bbox, quality = find_value_bbox("NoSuchValue", words, ocr_tolerance=0.99)
        assert bbox is None
        assert quality == "not_found"


class TestMerge:
    def test_merge_takes_min_max_across_window(self):
        window = [_word("a", x0=0.1, y0=0.2, x1=0.2, y1=0.3),
                   _word("b", x0=0.2, y0=0.1, x1=0.3, y1=0.25)]
        assert _merge(window) == {"page": 1, "x0": 0.1, "y0": 0.1, "x1": 0.3, "y1": 0.3}


class TestAlignFields:
    def test_align_fields_routes_by_extension(self, monkeypatch):
        called = {}

        def fake_extract_words(path):
            called["fn"] = "pdf"
            return [_word("Value", x0=0.0, x1=0.2)]

        import services.bbox_generator.bbox_aligner as mod
        monkeypatch.setattr(mod, "extract_words", fake_extract_words)

        result = align_fields({"field_a": "Value"}, {"field_a": "string"}, "doc.pdf")
        assert called["fn"] == "pdf"
        assert result["field_a"]["match_quality"] == "exact"
        assert result["field_a"]["value"] == "Value"

    def test_align_fields_uses_ocr_for_images(self, monkeypatch):
        called = {}

        def fake_extract_words_image(path):
            called["fn"] = "image"
            return [_word("Value", x0=0.0, x1=0.2)]

        import services.bbox_generator.bbox_aligner as mod
        monkeypatch.setattr(mod, "extract_words_image", fake_extract_words_image)

        result = align_fields({"field_a": "Value"}, {"field_a": "string"}, "doc.png")
        assert called["fn"] == "image"
        assert result["field_a"]["bbox"] is not None
