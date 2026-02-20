"""Tests for web scraper module."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.mcp_servers.web_scraper_mcp.scraper import (
    extract_domain,
    extract_specs_from_text,
    parse_price,
    scrape_page,
)
from src.mcp_servers.web_scraper_mcp.strategy import ScrapingStrategy


class TestParsePrice:
    def test_usd_price(self):
        assert parse_price("$299.99") == 299.99

    def test_nis_price(self):
        assert parse_price("₪1,299") == 1299.0

    def test_european_format(self):
        assert parse_price("1.299,99 €") == 1299.99

    def test_plain_number(self):
        assert parse_price("450") == 450.0

    def test_empty_string(self):
        assert parse_price("") is None

    def test_no_digits(self):
        assert parse_price("free") is None

    def test_decimal_comma(self):
        assert parse_price("12,99") == 12.99

    def test_thousands_comma(self):
        assert parse_price("1,299") == 1299.0


class TestExtractDomain:
    def test_strips_www(self):
        assert extract_domain("https://www.amazon.com/dp/123") == "amazon.com"

    def test_without_www(self):
        assert extract_domain("https://ksp.co.il/product/1") == "ksp.co.il"


class TestScrapePageWithNoStrategy:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_strategy_found(self):
        mock_page = AsyncMock()
        mock_page.query_selector_all.return_value = []  # No containers found

        mock_browser = AsyncMock()

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.get_page") as mock_get_page,
            patch("src.mcp_servers.web_scraper_mcp.scraper.get_cached_strategy", return_value=None),
            patch("src.mcp_servers.web_scraper_mcp.scraper.discover_strategy", return_value=None),
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_page)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_page.return_value = mock_ctx

            results = await scrape_page(mock_browser, "https://shop.example.com/search?q=laptop")

        assert results == []


class TestScrapePageWithCachedStrategy:
    @pytest.mark.asyncio
    async def test_uses_cached_strategy(self):
        strategy = ScrapingStrategy(
            product_container=".product-card",
            name_selector="h2",
            price_selector=".price",
        )

        mock_name_el = AsyncMock()
        mock_name_el.inner_text.return_value = "Test Laptop"

        mock_price_el = AsyncMock()
        mock_price_el.inner_text.return_value = "$999.99"

        mock_container = AsyncMock()

        async def mock_query_selector(selector):
            if selector == "h2":
                return mock_name_el
            if selector == ".price":
                return mock_price_el
            return None

        mock_container.query_selector = mock_query_selector
        mock_container.inner_text = AsyncMock(return_value="Test Laptop $999.99")

        mock_page = AsyncMock()
        mock_page.query_selector_all.return_value = [mock_container]

        mock_browser = AsyncMock()

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.get_page") as mock_get_page,
            patch("src.mcp_servers.web_scraper_mcp.scraper.get_cached_strategy", return_value=strategy),
            patch("src.mcp_servers.web_scraper_mcp.scraper.update_success_rate") as mock_update,
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_page)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_page.return_value = mock_ctx

            results = await scrape_page(mock_browser, "https://shop.example.com/search?q=laptop")

        assert len(results) == 1
        assert results[0].name == "Test Laptop"
        assert results[0].sellers[0].price == 999.99
        assert results[0].sellers[0].currency == "USD"
        mock_update.assert_awaited_once_with("shop.example.com", success=True)


class TestScrapePageCachedStrategyFailure:
    @pytest.mark.asyncio
    async def test_re_discovers_on_cached_failure(self):
        cached_strategy = ScrapingStrategy(
            product_container=".old-selector",
            name_selector="h2",
        )
        new_strategy = ScrapingStrategy(
            product_container=".new-card",
            name_selector="h3",
        )

        mock_name_el = AsyncMock()
        mock_name_el.inner_text.return_value = "New Product"

        mock_container = AsyncMock()

        async def mock_qs(selector):
            if selector == "h3":
                return mock_name_el
            return None

        mock_container.query_selector = mock_qs
        mock_container.inner_text = AsyncMock(return_value="New Product")

        mock_page = AsyncMock()

        call_count = 0

        async def mock_query_selector_all(selector):
            nonlocal call_count
            call_count += 1
            if selector == ".old-selector":
                return []  # Cached strategy fails
            if selector == ".new-card":
                return [mock_container]
            return []

        mock_page.query_selector_all = mock_query_selector_all

        mock_browser = AsyncMock()

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.get_page") as mock_get_page,
            patch("src.mcp_servers.web_scraper_mcp.scraper.get_cached_strategy", return_value=cached_strategy),
            patch("src.mcp_servers.web_scraper_mcp.scraper.update_success_rate") as mock_update,
            patch("src.mcp_servers.web_scraper_mcp.scraper.discover_strategy", return_value=new_strategy),
            patch("src.mcp_servers.web_scraper_mcp.scraper.save_strategy") as mock_save,
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_page)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_page.return_value = mock_ctx

            results = await scrape_page(mock_browser, "https://shop.example.com/products")

        assert len(results) == 1
        assert results[0].name == "New Product"
        # Cached strategy failure should decrement success rate
        mock_update.assert_awaited_with("shop.example.com", success=False)
        # New strategy should be saved
        mock_save.assert_awaited_once()


class TestExtractSpecsFromText:
    def test_extracts_noise_level(self):
        specs = extract_specs_from_text("Samsung Refrigerator 39 dB noise")
        assert specs["noise_level"] == "39 dB"

    def test_extracts_capacity(self):
        specs = extract_specs_from_text("350 liters capacity")
        assert specs["capacity"] == "350 liters"

    def test_extracts_capacity_L(self):
        specs = extract_specs_from_text("Fridge 400L")
        assert specs["capacity"] == "400L"

    def test_extracts_energy_rating(self):
        specs = extract_specs_from_text("A+ Energy class")
        assert specs["energy_rating"] == "A+"

    def test_extracts_resolution(self):
        specs = extract_specs_from_text('Samsung 55" 4K Smart TV')
        assert specs["resolution"] == "4K"
        assert specs["screen_size"] == '55"'

    def test_extracts_processor(self):
        specs = extract_specs_from_text("Laptop i7-13700H 16GB RAM 512GB SSD")
        assert specs["processor"] == "i7-13700H"
        assert specs["ram"] == "16GB RAM"
        assert specs["storage"] == "512GB SSD"

    def test_extracts_anc(self):
        specs = extract_specs_from_text("Sony WH-1000XM5 ANC Headphones")
        assert specs["noise_cancelling"] == "ANC"

    def test_extracts_weight(self):
        specs = extract_specs_from_text("Weight: 55 kg")
        assert specs["weight"] == "55 kg"

    def test_empty_text(self):
        assert extract_specs_from_text("") == {}

    def test_no_specs(self):
        assert extract_specs_from_text("A great product for your home") == {}

    def test_multiple_specs(self):
        text = "LG Refrigerator 350L 39 dB A+ Energy Frost-Free"
        specs = extract_specs_from_text(text)
        assert "capacity" in specs
        assert "noise_level" in specs
        assert "energy_rating" in specs
        assert "frost_free" in specs


class TestExtractSpecsWithCriteria:
    """Tests for extract_specs_from_text with explicit criteria dicts."""

    def test_custom_criteria_noise(self):
        criteria = {"noise_level": {"unit": "dB"}}
        specs = extract_specs_from_text("Operating at 42 dB", criteria=criteria)
        assert "noise_level" in specs

    def test_custom_criteria_unknown_unit(self):
        criteria = {"mystery": {"unit": "zorps"}}
        specs = extract_specs_from_text("mystery 5 zorps", criteria=criteria)
        assert specs == {}

    def test_custom_criteria_weight(self):
        criteria = {"weight": {"unit": "kg"}}
        specs = extract_specs_from_text("Total weight 12 kg", criteria=criteria)
        assert specs["weight"] == "12 kg"

    def test_custom_criteria_only_extracts_requested(self):
        criteria = {"noise_level": {"unit": "dB"}}
        text = "Weight: 55 kg, Noise: 39 dB, 350 liters"
        specs = extract_specs_from_text(text, criteria=criteria)
        assert "noise_level" in specs
        # weight and capacity should NOT be extracted since not in criteria
        assert "weight" not in specs
        assert "capacity" not in specs
