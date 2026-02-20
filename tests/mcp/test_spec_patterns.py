"""Tests for the dynamic spec pattern builder."""

from __future__ import annotations

import re

from src.mcp_servers.web_scraper_mcp.spec_patterns import (
    _DEFAULT_CRITERIA,
    _SPECIAL_PATTERNS,
    _UNIT_PATTERN_TEMPLATES,
    build_extraction_patterns,
)


class TestBuildExtractionPatternsDefaults:
    """Tests using the default criteria (no argument)."""

    def test_returns_list_of_tuples(self):
        patterns = build_extraction_patterns()
        assert isinstance(patterns, list)
        for item in patterns:
            assert len(item) == 3
            assert isinstance(item[0], re.Pattern)
            assert isinstance(item[1], str)
            assert isinstance(item[2], int)

    def test_covers_all_default_keys(self):
        patterns = build_extraction_patterns()
        keys = {key for _, key, _ in patterns}
        # All default keys (except those with unknown units) should be present
        for key in _DEFAULT_CRITERIA:
            if key == "price":
                continue
            unit = _DEFAULT_CRITERIA[key].get("unit", "")
            if key in _SPECIAL_PATTERNS or (unit and unit in _UNIT_PATTERN_TEMPLATES):
                assert key in keys, f"Missing pattern for default key: {key}"

    def test_skips_price_key(self):
        patterns = build_extraction_patterns()
        keys = {key for _, key, _ in patterns}
        assert "price" not in keys

    def test_none_argument_uses_defaults(self):
        explicit = build_extraction_patterns(None)
        implicit = build_extraction_patterns()
        assert len(explicit) == len(implicit)
        assert {k for _, k, _ in explicit} == {k for _, k, _ in implicit}


class TestBuildExtractionPatternsCustom:
    """Tests with custom criteria dicts."""

    def test_unit_based_pattern(self):
        criteria = {"noise_level": {"unit": "dB"}}
        patterns = build_extraction_patterns(criteria)
        assert len(patterns) == 1
        pat, key, _ = patterns[0]
        assert key == "noise_level"
        assert pat.search("39 dB")

    def test_special_pattern_takes_priority(self):
        criteria = {"resolution": {"unit": ""}}
        patterns = build_extraction_patterns(criteria)
        assert len(patterns) == 1
        pat, key, _ = patterns[0]
        assert key == "resolution"
        assert pat.search("4K TV")

    def test_unknown_unit_skipped(self):
        criteria = {"some_metric": {"unit": "foobar"}}
        patterns = build_extraction_patterns(criteria)
        assert len(patterns) == 0

    def test_price_key_always_skipped(self):
        criteria = {"price": {"unit": "USD"}, "weight": {"unit": "kg"}}
        patterns = build_extraction_patterns(criteria)
        keys = {k for _, k, _ in patterns}
        assert "price" not in keys
        assert "weight" in keys

    def test_mixed_criteria(self):
        criteria = {
            "noise_level": {"unit": "dB"},
            "resolution": {"unit": ""},
            "weight": {"unit": "kg"},
            "unknown_thing": {"unit": "zorps"},
        }
        patterns = build_extraction_patterns(criteria)
        keys = {k for _, k, _ in patterns}
        assert keys == {"noise_level", "resolution", "weight"}


class TestPatternMatching:
    """Verify that generated patterns actually match expected text."""

    def test_db_pattern(self):
        patterns = build_extraction_patterns({"noise": {"unit": "dB"}})
        pat, _, idx = patterns[0]
        m = pat.search("39 dB")
        assert m
        assert m.group(idx).strip() in ("39 dB", "39")

    def test_kg_pattern(self):
        patterns = build_extraction_patterns({"weight": {"unit": "kg"}})
        pat, _, idx = patterns[0]
        m = pat.search("Weight: 5.5 kg")
        assert m

    def test_resolution_special(self):
        patterns = build_extraction_patterns({"resolution": {"unit": ""}})
        pat, _, idx = patterns[0]
        for text in ("4K TV", "Full HD monitor", "1080p display"):
            assert pat.search(text), f"Should match: {text}"

    def test_processor_special(self):
        patterns = build_extraction_patterns({"processor": {"unit": ""}})
        pat, _, idx = patterns[0]
        m = pat.search("Intel i7-13700H laptop")
        assert m
        assert "i7-13700H" in m.group(idx)

    def test_energy_rating_group_index(self):
        patterns = build_extraction_patterns({"energy_rating": {"unit": ""}})
        pat, _, idx = patterns[0]
        m = pat.search("A+ Energy class")
        assert m
        assert m.group(idx).strip() == "A+"

    def test_case_insensitive(self):
        patterns = build_extraction_patterns({"noise": {"unit": "dB"}})
        pat, _, _ = patterns[0]
        assert pat.search("39 DB")
        assert pat.search("39 Db")
