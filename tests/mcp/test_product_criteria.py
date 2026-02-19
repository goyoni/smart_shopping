"""Tests for the Product Criteria MCP server."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.mcp_servers.product_criteria_mcp.criteria import (
    CriterionSpec,
    get_criteria,
    merge_criteria,
    normalize_category,
    research_criteria,
)
from src.mcp_servers.product_criteria_mcp.db_cache import (
    _make_cache_key,
    get_cached,
    save_cached,
)


# ---------------------------------------------------------------------------
# Category normalization
# ---------------------------------------------------------------------------

class TestNormalizeCategory:
    def test_english_known(self):
        assert normalize_category("refrigerator") == "refrigerator"

    def test_english_alias(self):
        assert normalize_category("fridge") == "refrigerator"

    def test_english_plural(self):
        assert normalize_category("Refrigerators") == "refrigerator"

    def test_case_insensitive(self):
        assert normalize_category("MICROWAVE") == "microwave"

    def test_hebrew(self):
        assert normalize_category("מקרר") == "refrigerator"

    def test_arabic(self):
        assert normalize_category("ثلاجة") == "refrigerator"

    def test_multi_word(self):
        assert normalize_category("washing machine") == "washing_machine"

    def test_unknown(self):
        assert normalize_category("toaster") == "toaster"

    def test_strips_whitespace(self):
        assert normalize_category("  laptop  ") == "laptop"


# ---------------------------------------------------------------------------
# get_criteria
# ---------------------------------------------------------------------------

class TestGetCriteria:
    def test_known_category(self):
        criteria = get_criteria("refrigerator")
        assert "noise_level" in criteria
        assert "energy_rating" in criteria
        assert "capacity" in criteria
        assert criteria["noise_level"]["display_name"] == "Noise Level"
        assert criteria["noise_level"]["unit"] == "dB"

    def test_unknown_category(self):
        criteria = get_criteria("xyz_unknown_product")
        assert criteria == {}

    def test_via_alias(self):
        criteria = get_criteria("fridge")
        assert "noise_level" in criteria

    def test_all_criteria_have_display_name(self):
        criteria = get_criteria("tv")
        for key, spec in criteria.items():
            assert "display_name" in spec
            assert spec["display_name"]


# ---------------------------------------------------------------------------
# research_criteria
# ---------------------------------------------------------------------------

class TestResearchCriteria:
    def test_discovers_from_snippets(self):
        snippets = [
            "This refrigerator has a noise level of 38 dB",
            "Energy rating A++ with 350L capacity",
        ]
        base = get_criteria("refrigerator")
        result = research_criteria(snippets, base)
        # Should contain at least the base criteria
        assert "noise_level" in result
        assert "energy_rating" in result
        assert "capacity" in result

    def test_adds_new_criteria_not_in_base(self):
        snippets = ["Battery life up to 8 hours, with noise cancelling"]
        base = {}
        result = research_criteria(snippets, base)
        assert "battery_life" in result
        assert "noise_cancelling" in result
        assert result["battery_life"]["importance"] == "low"

    def test_empty_snippets(self):
        base = {"price": {"display_name": "Price", "unit": "", "importance": "high", "description": ""}}
        result = research_criteria([], base)
        assert result == base

    def test_does_not_overwrite_existing(self):
        snippets = ["noise level 40dB"]
        base = {
            "noise_level": {
                "display_name": "Noise Level",
                "unit": "dB",
                "importance": "high",
                "description": "Operating noise",
            }
        }
        result = research_criteria(snippets, base)
        assert result["noise_level"]["importance"] == "high"


# ---------------------------------------------------------------------------
# merge_criteria
# ---------------------------------------------------------------------------

class TestMergeCriteria:
    def test_override_importance(self):
        base = get_criteria("refrigerator")
        user = {"noise_level": "low"}
        merged = merge_criteria(base, user)
        assert merged["noise_level"]["importance"] == "low"
        assert merged["noise_level"]["display_name"] == "Noise Level"

    def test_remove_criterion(self):
        base = get_criteria("refrigerator")
        user = {"weight": None}
        merged = merge_criteria(base, user)
        assert "weight" not in merged

    def test_add_new_criterion(self):
        base = get_criteria("refrigerator")
        user = {"color": "high"}
        merged = merge_criteria(base, user)
        assert "color" in merged
        assert merged["color"]["importance"] == "high"

    def test_merge_dict_value(self):
        base = get_criteria("refrigerator")
        user = {"noise_level": {"unit": "dBA", "importance": "critical"}}
        merged = merge_criteria(base, user)
        assert merged["noise_level"]["unit"] == "dBA"
        assert merged["noise_level"]["importance"] == "critical"
        assert merged["noise_level"]["display_name"] == "Noise Level"

    def test_empty_user_criteria(self):
        base = get_criteria("refrigerator")
        merged = merge_criteria(base, {})
        assert merged == base


# ---------------------------------------------------------------------------
# DB cache
# ---------------------------------------------------------------------------

class TestDbCache:
    @pytest.mark.asyncio
    async def test_roundtrip(self):
        criteria = {"noise_level": {"display_name": "Noise", "unit": "dB", "importance": "high", "description": ""}}
        await save_cached("test_roundtrip_cat", criteria)
        result = await get_cached("test_roundtrip_cat")
        assert result is not None
        assert result["noise_level"]["display_name"] == "Noise"

    @pytest.mark.asyncio
    async def test_not_found(self):
        result = await get_cached("nonexistent_category_12345")
        assert result is None

    @pytest.mark.asyncio
    async def test_ttl_expired(self):
        from src.backend.db.engine import async_session
        from src.backend.db.models import ProductCriteriaCache
        from sqlalchemy import select
        import json

        category = "test_ttl_expired_cat"
        cache_key = _make_cache_key(category)
        criteria = {"test": {"display_name": "Test"}}

        async with async_session() as session:
            record = ProductCriteriaCache(
                category=category,
                criteria_json=json.dumps(criteria),
                cache_key=cache_key,
            )
            session.add(record)
            await session.commit()

            # Backdate the created_at to 10 days ago
            stmt = select(ProductCriteriaCache).where(ProductCriteriaCache.category == category)
            result = await session.execute(stmt)
            row = result.scalar_one()
            row.created_at = datetime.now(timezone.utc) - timedelta(days=10)
            await session.commit()

        result = await get_cached(category)
        assert result is None

    @pytest.mark.asyncio
    async def test_upsert(self):
        category = "test_upsert_cat"
        criteria_v1 = {"a": {"display_name": "A"}}
        criteria_v2 = {"b": {"display_name": "B"}}

        await save_cached(category, criteria_v1)
        result1 = await get_cached(category)
        assert "a" in result1

        await save_cached(category, criteria_v2)
        result2 = await get_cached(category)
        assert "b" in result2
        assert "a" not in result2
