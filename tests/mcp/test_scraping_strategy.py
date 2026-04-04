"""Tests for scraping strategy module."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_servers.web_scraper_mcp.strategy import (
    ScrapingStrategy,
    _detect_currency,
    _discover_via_llm,
    _get_scraper_llm_model,
    _looks_like_price,
)


class TestScrapingStrategy:
    def test_json_roundtrip(self):
        strategy = ScrapingStrategy(
            product_container=".product-card",
            name_selector="h2 a",
            price_selector="[class*='price']",
            image_selector="img",
            url_selector="a[href]",
            currency_hint="USD",
            version=1,
            discovery_method="css_candidates",
        )
        json_str = strategy.to_json()
        parsed = ScrapingStrategy.from_json(json_str)

        assert parsed.product_container == ".product-card"
        assert parsed.name_selector == "h2 a"
        assert parsed.price_selector == "[class*='price']"
        assert parsed.currency_hint == "USD"

    def test_json_roundtrip_with_criteria_selectors(self):
        strategy = ScrapingStrategy(
            product_container=".product",
            name_selector="h2",
            criteria_selectors={"noise_level": "[class*='noise']", "weight": "[data-spec='weight']"},
        )
        json_str = strategy.to_json()
        parsed = ScrapingStrategy.from_json(json_str)

        assert parsed.criteria_selectors == {
            "noise_level": "[class*='noise']",
            "weight": "[data-spec='weight']",
        }

    def test_from_json_unknown_fields_ignored(self):
        data = json.dumps({
            "product_container": ".item",
            "name_selector": "h3",
            "unknown_future_field": "should be ignored",
        })
        strategy = ScrapingStrategy.from_json(data)
        assert strategy.product_container == ".item"
        assert strategy.name_selector == "h3"

    def test_from_json_missing_criteria_selectors_defaults_empty(self):
        data = json.dumps({
            "product_container": ".item",
            "name_selector": "h3",
        })
        strategy = ScrapingStrategy.from_json(data)
        assert strategy.criteria_selectors == {}

    def test_default_values(self):
        strategy = ScrapingStrategy(product_container=".card")
        assert strategy.name_selector == ""
        assert strategy.price_selector == ""
        assert strategy.version == 1
        assert strategy.discovery_method == "css_candidates"
        assert strategy.criteria_selectors == {}


class TestLooksLikePrice:
    def test_usd_price(self):
        assert _looks_like_price("$299.99") is True

    def test_nis_price(self):
        assert _looks_like_price("₪1,299") is True

    def test_euro_price(self):
        assert _looks_like_price("€49.90") is True

    def test_plain_number_with_decimal(self):
        assert _looks_like_price("299.99") is True

    def test_no_digits(self):
        assert _looks_like_price("no price here") is False

    def test_empty_string(self):
        assert _looks_like_price("") is False

    def test_just_digits(self):
        assert _looks_like_price("299") is True


class TestDetectCurrency:
    def test_detect_usd(self):
        assert _detect_currency("$29.99") == "USD"

    def test_detect_ils(self):
        assert _detect_currency("₪1,299") == "ILS"

    def test_detect_eur(self):
        assert _detect_currency("€49.90") == "EUR"

    def test_detect_gbp(self):
        assert _detect_currency("£19.99") == "GBP"

    def test_detect_nis_text(self):
        assert _detect_currency("1299 NIS") == "ILS"

    def test_no_currency(self):
        assert _detect_currency("299") == ""


class TestGetScraperLlmModel:
    def test_defaults_to_llm_model(self):
        with patch("src.mcp_servers.web_scraper_mcp.strategy.model.settings") as mock_settings:
            mock_settings.scraper_llm_model = ""
            mock_settings.llm_model = "gpt-4o-mini"
            assert _get_scraper_llm_model() == "gpt-4o-mini"

    def test_override(self):
        with patch("src.mcp_servers.web_scraper_mcp.strategy.model.settings") as mock_settings:
            mock_settings.scraper_llm_model = "ollama/llama3"
            mock_settings.llm_model = "gpt-4o-mini"
            assert _get_scraper_llm_model() == "ollama/llama3"


class TestDiscoverViaLlm:
    @pytest.fixture
    def mock_page(self):
        page = AsyncMock()
        # Simulate containers with name and price elements
        container = AsyncMock()
        name_el = AsyncMock()
        name_el.inner_text = AsyncMock(return_value="Braun PL5147 IPL")
        price_el = AsyncMock()
        price_el.inner_text = AsyncMock(return_value="€369.18")

        def _query_selector(sel):
            if "name" in sel or sel in ("a", "h2", "h3"):
                return name_el
            if "price" in sel:
                return price_el
            return None

        container.query_selector = AsyncMock(side_effect=_query_selector)
        page.query_selector_all = AsyncMock(return_value=[container, container, container])
        page.evaluate = AsyncMock(return_value="<div class='sku'>" + "x" * 120 + "</div>")
        return page

    @pytest.mark.asyncio
    async def test_skipped_without_api_key(self):
        page = AsyncMock()
        with patch("src.mcp_servers.web_scraper_mcp.strategy.llm_discovery.settings") as mock_settings:
            mock_settings.llm_api_key = ""
            mock_settings.scraper_llm_model = ""
            mock_settings.llm_model = "gpt-4o-mini"
            result = await _discover_via_llm(page, "pl5147")
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_discovery(self, mock_page):
        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = json.dumps({
            "container": "li.cf.card",
            "name": "a.js-sku-link",
            "price": "span.price",
            "image": "img",
            "url": "a.js-sku-link",
            "currency": "EUR",
        })

        with (
            patch("src.mcp_servers.web_scraper_mcp.strategy.llm_discovery.settings") as mock_settings,
            patch("src.mcp_servers.web_scraper_mcp.strategy.llm_discovery.litellm") as mock_litellm,
        ):
            mock_settings.llm_api_key = "test-key"
            mock_settings.scraper_llm_model = ""
            mock_settings.llm_model = "gpt-4o-mini"
            mock_litellm.acompletion = AsyncMock(return_value=llm_response)

            result = await _discover_via_llm(mock_page, "pl5147")

        assert result is not None
        assert result.product_container == "li.cf.card"
        assert result.discovery_method == "llm"
        assert result.currency_hint == "EUR"

    @pytest.mark.asyncio
    async def test_rejects_invalid_json(self, mock_page):
        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = "Sorry, I can't do that."

        with (
            patch("src.mcp_servers.web_scraper_mcp.strategy.llm_discovery.settings") as mock_settings,
            patch("src.mcp_servers.web_scraper_mcp.strategy.llm_discovery.litellm") as mock_litellm,
        ):
            mock_settings.llm_api_key = "test-key"
            mock_settings.scraper_llm_model = ""
            mock_settings.llm_model = "gpt-4o-mini"
            mock_litellm.acompletion = AsyncMock(return_value=llm_response)

            result = await _discover_via_llm(mock_page, "pl5147")

        assert result is None

    @pytest.mark.asyncio
    async def test_rejects_selector_with_too_few_containers(self):
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value="<div>snapshot" + "x" * 120 + "</div>")
        page.query_selector_all = AsyncMock(return_value=[AsyncMock()])  # Only 1

        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = json.dumps({
            "container": "div.nonexistent",
            "name": "a", "price": "span", "image": "", "url": "", "currency": "",
        })

        with (
            patch("src.mcp_servers.web_scraper_mcp.strategy.llm_discovery.settings") as mock_settings,
            patch("src.mcp_servers.web_scraper_mcp.strategy.llm_discovery.litellm") as mock_litellm,
        ):
            mock_settings.llm_api_key = "test-key"
            mock_settings.scraper_llm_model = ""
            mock_settings.llm_model = "gpt-4o-mini"
            mock_litellm.acompletion = AsyncMock(return_value=llm_response)

            result = await _discover_via_llm(page, "pl5147")

        assert result is None

    @pytest.mark.asyncio
    async def test_uses_scraper_llm_model_override(self, mock_page):
        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = json.dumps({
            "container": "div.product",
            "name": "a", "price": "span.price",
            "image": "", "url": "", "currency": "",
        })

        with (
            patch("src.mcp_servers.web_scraper_mcp.strategy.llm_discovery.settings") as mock_settings,
            patch("src.mcp_servers.web_scraper_mcp.strategy.model.settings") as mock_model_settings,
            patch("src.mcp_servers.web_scraper_mcp.strategy.llm_discovery.litellm") as mock_litellm,
        ):
            mock_settings.llm_api_key = "test-key"
            mock_settings.scraper_llm_model = "ollama/llama3"
            mock_settings.llm_model = "gpt-4o-mini"
            mock_model_settings.scraper_llm_model = "ollama/llama3"
            mock_model_settings.llm_model = "gpt-4o-mini"
            mock_litellm.acompletion = AsyncMock(return_value=llm_response)

            await _discover_via_llm(mock_page, "pl5147")

            # Verify the override model was used
            call_kwargs = mock_litellm.acompletion.call_args
            assert call_kwargs.kwargs["model"] == "ollama/llama3"
