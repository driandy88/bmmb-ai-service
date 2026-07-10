"""
Tests for bbox_aligner (v3) matching/scoring logic. These operate on
hand-built word lists (the same shape extract_words()/extract_words_image()
produce) so they don't need a real PDF/image fixture on disk.
"""
from services.bbox_generator.bbox_aligner import (
    _merge,
    _norm_token,
    _try_parse_date,
    _try_parse_number,
    align_extraction,
    align_fields,
    find_value_bbox,
    find_value_candidates,
    snap_to_words,
)


def _word(text, page=1, x0=0.0, y0=0.0, x1=0.1, y1=0.05):
    return {"page": page, "text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


class TestNormToken:
    def test_strips_punctuation_and_casefolds(self):
        assert _norm_token("SEBASTIAN,") == "sebastian"

    def test_thousand_separators_stripped(self):
        assert _norm_token("1,000.00") == "100000"


class TestParseNumber:
    def test_plain_integer(self):
        assert _try_parse_number("340980") == 340980.0

    def test_thousand_separated_decimal(self):
        assert _try_parse_number("340,980.00") == 340980.0

    def test_currency_prefix(self):
        assert _try_parse_number("RM 328,400.00") == 328400.0

    def test_parens_means_negative(self):
        assert _try_parse_number("(3,124,300)") == -3124300.0

    def test_non_numeric_returns_none(self):
        assert _try_parse_number("hello") is None


class TestParseDate:
    def test_iso_format(self):
        assert _try_parse_date("2023-03-14") is not None

    def test_malay_month_translated(self):
        d1 = _try_parse_date("14 Mac 2023")
        d2 = _try_parse_date("14 March 2023")
        assert d1 == d2

    def test_malay_month_abbreviation(self):
        assert _try_parse_date("31 Dis 2023") == _try_parse_date("31 December 2023")

    def test_month_year_only(self):
        d = _try_parse_date("July 2023")
        assert d is not None and d.month == 7 and d.year == 2023

    def test_unparseable_returns_none(self):
        assert _try_parse_date("not-a-date") is None


class TestFindValueCandidatesExact:
    def test_single_word_exact_match(self):
        words = [_word("Hello"), _word("SEBASTIAN", x0=0.2, x1=0.4), _word("World", x0=0.5)]
        cands = find_value_candidates("SEBASTIAN", words)
        assert len(cands) == 1
        assert cands[0]["tier"] == "exact"
        assert cands[0]["bbox"] == {"page": 1, "x0": 0.2, "y0": 0.0, "x1": 0.4, "y1": 0.05}

    def test_multi_word_exact_match_merges_boxes(self):
        words = [_word("John", x0=0.0, x1=0.1), _word("Smith", x0=0.1, x1=0.25)]
        cands = find_value_candidates("John Smith", words)
        assert any(c["tier"] == "exact" and c["bbox"]["x1"] == 0.25 for c in cands)

    def test_no_match_crossing_pages_rejected(self):
        words = [_word("John", page=1, x0=0.0, x1=0.1), _word("Smith", page=2, x0=0.0, x1=0.1)]
        cands = find_value_candidates("John Smith", words)
        assert cands == []

    def test_null_value(self):
        assert find_value_candidates(None, []) == []


class TestFindValueCandidatesNumber:
    def test_matches_thousand_separated_variant(self):
        words = [_word("340,980.00", x0=0.0, x1=0.2)]
        cands = find_value_candidates(340980, words, data_type="float")
        assert any(c["tier"] == "exact_number" for c in cands)

    def test_matches_currency_prefixed_two_word_amount(self):
        words = [_word("RM", x0=0.0, x1=0.05), _word("328,400.00", x0=0.05, x1=0.2)]
        cands = find_value_candidates(328400.0, words, data_type="float")
        assert any(c["tier"] == "exact_number" and c["bbox"]["x1"] == 0.2 for c in cands)

    def test_matches_parenthesized_negative(self):
        words = [_word("(3,124,300)", x0=0.0, x1=0.2)]
        cands = find_value_candidates(-3124300, words, data_type="float")
        assert any(c["tier"] == "exact_number" for c in cands)

    def test_numeric_python_value_matches_even_without_declared_type(self):
        # numeric parsing triggers on a real int/float value regardless of
        # the declared data_type, since isinstance(value, (int, float)) is
        # itself sufficient — unlike a numeric-looking *string*, which stays
        # on the plain token-match path unless data_type says otherwise.
        words = [_word("340,980.00", x0=0.0, x1=0.2)]
        cands = find_value_candidates(340980, words, data_type="string")
        assert any(c["tier"] == "exact_number" for c in cands)

    def test_numeric_looking_string_without_declared_type_does_not_number_match(self):
        words = [_word("340,980.00", x0=0.0, x1=0.2)]
        cands = find_value_candidates("340980", words, data_type="string")
        assert not any(c["tier"] == "exact_number" for c in cands)


class TestFindValueCandidatesDate:
    def test_date_phrase_different_format_matches(self):
        words = [_word("14"), _word("March", x0=0.1, x1=0.25), _word("2023", x0=0.25, x1=0.35)]
        cands = find_value_candidates("2023-03-14", words, data_type="date")
        assert any(c["tier"] == "exact_date" for c in cands)

    def test_malay_month_document_matches_english_value(self):
        words = [_word("14"), _word("Mac", x0=0.1, x1=0.2), _word("2023", x0=0.2, x1=0.3)]
        cands = find_value_candidates("14 March 2023", words, data_type="date")
        assert any(c["tier"] == "exact_date" for c in cands)

    def test_month_year_granularity(self):
        words = [_word("Julai", x0=0.0, x1=0.15), _word("2023", x0=0.15, x1=0.25)]
        cands = find_value_candidates("July 2023", words, data_type="date")
        assert any(c["tier"] == "exact_date" for c in cands)

    def test_unparseable_date_falls_back_to_token_match(self):
        # verbatim text present in the doc should still resolve, just via the
        # plain token-sequence path rather than the date path
        words = [_word("not-a-date")]
        cands = find_value_candidates("not-a-date", words, data_type="date")
        assert any(c["tier"] == "exact" for c in cands)


class TestFindValueCandidatesFuzzy:
    def test_split_differently_still_matches(self):
        words = [_word("AB", x0=0.0, x1=0.05), _word("1234", x0=0.05, x1=0.15)]
        cands = find_value_candidates("AB1234", words)
        assert any(c["tier"] == "fuzzy" for c in cands)

    def test_ocr_misread_matches_within_tolerance(self):
        words = [_word("SEBASTIAI", x0=0.0, x1=0.3)]
        cands = find_value_candidates("SEBASTIAN", words, ocr_tolerance=0.8)
        assert any(c["tier"].startswith("fuzzy_ocr") for c in cands)

    def test_not_found_when_absent(self):
        words = [_word("Completely"), _word("Unrelated")]
        cands = find_value_candidates("NoSuchValue", words, ocr_tolerance=0.99)
        assert cands == []


class TestMerge:
    def test_merge_takes_min_max_across_window(self):
        window = [_word("a", x0=0.1, y0=0.2, x1=0.2, y1=0.3),
                   _word("b", x0=0.2, y0=0.1, x1=0.3, y1=0.25)]
        assert _merge(window) == {"page": 1, "x0": 0.1, "y0": 0.1, "x1": 0.3, "y1": 0.3}


class TestSnapToWords:
    def test_snaps_to_nearby_matching_word(self):
        words = [_word("SEBASTIAN", x0=0.20, y0=0.10, x1=0.40, y1=0.13)]
        # coarse proposal centered near, but not exactly on, the real word
        proposal = {"page": 1, "x0": 0.22, "y0": 0.09, "x1": 0.42, "y1": 0.14}
        bbox, tier = snap_to_words(proposal, "SEBASTIAN", words)
        assert tier == "snapped:exact"
        assert (bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"]) == (0.20, 0.10, 0.40, 0.13)

    def test_snap_only_lands_on_a_window_that_matches_the_value(self):
        # a wrong-value word sits closer to the proposal center than the
        # correct value's word -- snap must never land on it, since
        # find_value_candidates() only returns windows matching the value.
        words = [
            _word("WrongValue", x0=0.05, y0=0.10, x1=0.15, y1=0.13),
            _word("SEBASTIAN", x0=0.60, y0=0.10, x1=0.80, y1=0.13),
        ]
        proposal = {"page": 1, "x0": 0.06, "y0": 0.10, "x1": 0.16, "y1": 0.13}
        bbox, tier = snap_to_words(proposal, "SEBASTIAN", words, radius=1.0)
        assert tier == "snapped:exact"
        assert bbox["x0"] == 0.60

    def test_out_of_radius_fails_to_snap(self):
        words = [_word("SEBASTIAN", x0=0.90, y0=0.90, x1=0.99, y1=0.95)]
        proposal = {"page": 1, "x0": 0.0, "y0": 0.0, "x1": 0.05, "y1": 0.05}
        bbox, tier = snap_to_words(proposal, "SEBASTIAN", words, radius=0.1)
        assert bbox is None
        assert tier == "snap_failed"

    def test_no_matching_value_anywhere_fails_to_snap(self):
        words = [_word("Unrelated", x0=0.0, x1=0.2)]
        proposal = {"page": 1, "x0": 0.0, "y0": 0.0, "x1": 0.2, "y1": 0.05}
        bbox, tier = snap_to_words(proposal, "SEBASTIAN", words, radius=1.0)
        assert bbox is None
        assert tier == "snap_failed"

    def test_different_page_from_proposal_is_ignored(self):
        words = [_word("SEBASTIAN", page=2, x0=0.0, x1=0.2)]
        proposal = {"page": 1, "x0": 0.0, "y0": 0.0, "x1": 0.2, "y1": 0.05}
        bbox, tier = snap_to_words(proposal, "SEBASTIAN", words, radius=1.0)
        assert bbox is None
        assert tier == "snap_failed"

    def test_numeric_value_snaps_to_formatted_variant(self):
        words = [_word("340,980.00", x0=0.50, y0=0.20, x1=0.62, y1=0.23)]
        proposal = {"page": 1, "x0": 0.48, "y0": 0.19, "x1": 0.60, "y1": 0.24}
        bbox, tier = snap_to_words(proposal, 340980, words, data_type="float")
        assert tier == "snapped:exact_number"
        assert bbox["x0"] == 0.50


class TestFindValueBboxBackCompat:
    def test_returns_best_candidate(self):
        words = [_word("Hello"), _word("SEBASTIAN", x0=0.2, x1=0.4)]
        bbox, quality = find_value_bbox("SEBASTIAN", words)
        assert quality == "exact"
        assert bbox["x0"] == 0.2

    def test_null_value(self):
        assert find_value_bbox(None, []) == (None, "null_value")

    def test_not_found(self):
        bbox, quality = find_value_bbox("NoSuchValue", [_word("Other")], ocr_tolerance=0.99)
        assert bbox is None and quality == "not_found"


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

    def test_null_value_short_circuits(self, monkeypatch):
        import services.bbox_generator.bbox_aligner as mod
        monkeypatch.setattr(mod, "extract_words", lambda path: [])
        result = align_fields({"field_a": None}, {"field_a": "string"}, "doc.pdf")
        assert result["field_a"] == {"value": None, "bbox": None, "match_quality": "null_value"}


class TestAlignExtractionSingle:
    def test_single_record_dict_returns_dict(self, monkeypatch):
        import services.bbox_generator.bbox_aligner as mod
        monkeypatch.setattr(mod, "extract_words", lambda path: [_word("Acme", x0=0.0, x1=0.2)])

        result = align_extraction({"business_name": "Acme"}, {"business_name": "string"}, "doc.pdf")
        assert isinstance(result, dict)
        assert result["business_name"]["match_quality"] == "exact"

    def test_hints_bias_toward_matching_page(self, monkeypatch):
        import services.bbox_generator.bbox_aligner as mod
        # same value appears on two pages; hint points at page 2
        words = [_word("Acme", page=1, x0=0.0, x1=0.2), _word("Acme", page=2, x0=0.3, x1=0.5)]
        monkeypatch.setattr(mod, "extract_words", lambda path: words)

        record = {"business_name": "Acme", "_locations": {"business_name": {"page": 2}}}
        result = align_extraction(record, {"business_name": "string"}, "doc.pdf")
        assert result["business_name"]["bbox"]["page"] == 2

    def test_hints_cannot_force_an_unsupported_match(self, monkeypatch):
        import services.bbox_generator.bbox_aligner as mod
        # value isn't in the document at all; a hint must not fabricate a box
        monkeypatch.setattr(mod, "extract_words", lambda path: [_word("Unrelated")])

        record = {"business_name": "Acme", "_locations": {"business_name": {"page": 1}}}
        result = align_extraction(record, {"business_name": "string"}, "doc.pdf")
        assert result["business_name"]["bbox"] is None
        assert result["business_name"]["match_quality"] == "not_found"


class TestAlignExtractionArrayRowAnchoring:
    def test_row_anchor_disambiguates_repeated_values_across_rows(self, monkeypatch):
        import services.bbox_generator.bbox_aligner as mod
        # two rows of a bank-statement-like table: same "withdrawal" amount
        # appears on both rows' lines, but each row's own month should anchor
        # the amount to the right row.
        words = [
            _word("January", page=1, x0=0.0, y0=0.10, x1=0.15, y1=0.13),
            _word("100.00", page=1, x0=0.5, y0=0.10, x1=0.6, y1=0.13),
            _word("February", page=1, x0=0.0, y0=0.20, x1=0.15, y1=0.23),
            _word("100.00", page=1, x0=0.5, y0=0.20, x1=0.6, y1=0.23),
        ]
        monkeypatch.setattr(mod, "extract_words", lambda path: words)

        records = [
            {"month": "January", "amount": 100.0},
            {"month": "February", "amount": 100.0},
        ]
        field_types = {"month": "string", "amount": "float"}
        results = align_extraction(records, field_types, "doc.pdf")

        assert isinstance(results, list) and len(results) == 2
        row0_amount_y = results[0]["amount"]["bbox"]["y0"]
        row1_amount_y = results[1]["amount"]["bbox"]["y0"]
        # each row's amount should land on that row's own line, not both
        # collapsing onto the same (e.g. first) occurrence.
        assert row0_amount_y != row1_amount_y
        assert abs(row0_amount_y - 0.10) < 1e-6
        assert abs(row1_amount_y - 0.20) < 1e-6

    def test_underscore_prefixed_keys_excluded_from_anchor_and_output(self, monkeypatch):
        import services.bbox_generator.bbox_aligner as mod
        monkeypatch.setattr(mod, "extract_words", lambda path: [_word("Acme", x0=0.0, x1=0.2)])

        record = {"_locations": {}, "business_name": "Acme"}
        result = align_extraction(record, {"business_name": "string"}, "doc.pdf")
        assert "_locations" not in result
        assert result["business_name"]["match_quality"] == "exact"
