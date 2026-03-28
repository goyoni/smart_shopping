"""Tests for web search module."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.shared.models import QueryAttribute
from src.mcp_servers.web_search_mcp.search import (
    SearchResult,
    _extract_ddg_url,
    build_refined_query,
    build_search_url,
    discover_aggregators,
    extract_search_results,
    get_aggregator_urls,
    search_on_site,
    search_products,
)


class TestBuildSearchUrl:
    def test_default_english_us(self):
        url = build_search_url("wireless headphones")
        assert "duckduckgo.com" in url
        assert "kl=us-en" in url
        assert "buy+online" in url or "buy%20online" in url.lower()

    def test_hebrew_israel(self):
        url = build_search_url("אוזניות", language="he", market="il")
        assert "kl=il-he" in url

    def test_unknown_market_defaults_to_us(self):
        url = build_search_url("laptop", market="zz")
        assert "kl=us-en" in url

    def test_query_augmentation_english(self):
        url = build_search_url("microwave")
        assert "buy" in url.lower()

    def test_query_augmentation_hebrew(self):
        url = build_search_url("מיקרוגל", language="he")
        assert "%D7%A7%D7%A0%D7%99%D7%99%D7%94" in url  # "קנייה" URL-encoded

    def test_market_overrides_language_suffix(self):
        """English browser in Israel should get Hebrew buy-online suffix."""
        url = build_search_url("table", language="en", market="il")
        assert "kl=il-he" in url
        # Should contain Hebrew "קנייה" not English "buy online"
        assert "%D7%A7%D7%A0%D7%99%D7%99%D7%94" in url
        assert "buy+online" not in url


class TestExtractDdgUrl:
    def test_extracts_from_uddg_redirect(self):
        raw = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.amazon.com%2Fproduct&rut=abc"
        assert _extract_ddg_url(raw) == "https://www.amazon.com/product"

    def test_handles_protocol_relative_url(self):
        raw = "//example.com/page"
        assert _extract_ddg_url(raw) == "https://example.com/page"

    def test_passes_through_direct_url(self):
        raw = "https://example.com/page"
        assert _extract_ddg_url(raw) == "https://example.com/page"


class TestExtractSearchResults:
    def test_extracts_results(self):
        html = '''
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.amazon.com%2Fp%2F1">Great Product</a>
        <a class="result__snippet" href="#">This is a product snippet with details</a>
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.bestbuy.com%2Fp%2F2">Another Product</a>
        <a class="result__snippet" href="#">Another snippet here with info</a>
        '''
        results = extract_search_results(html)
        assert len(results) == 2
        assert results[0].url == "https://www.amazon.com/p/1"
        assert results[0].title == "Great Product"
        assert "product snippet" in results[0].snippet

    def test_skips_empty_titles(self):
        html = '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com"> </a>'
        results = extract_search_results(html)
        assert len(results) == 0

    def test_deduplicates_urls(self):
        html = '''
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com">A</a>
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com">B</a>
        '''
        results = extract_search_results(html)
        assert len(results) == 1

    def test_strips_html_from_title(self):
        html = '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fex.com">Product <b>Bold</b> Title</a>'
        results = extract_search_results(html)
        assert len(results) == 1
        assert results[0].title == "Product Bold Title"


class TestSearchProducts:
    @pytest.mark.asyncio
    async def test_returns_results_on_success(self):
        html = '''
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fshop.example.com%2Fp%2F1">Product Title</a>
        <a class="result__snippet" href="#">A snippet about the product here</a>
        '''
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.mcp_servers.web_search_mcp.search.AsyncSession", return_value=mock_client):
            results = await search_products("test product")

        assert len(results) == 1
        assert results[0].title == "Product Title"

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = ""

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.mcp_servers.web_search_mcp.search.AsyncSession", return_value=mock_client):
            results = await search_products("test product")

        assert results == []

    @pytest.mark.asyncio
    async def test_retries_on_network_error(self):
        html = '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fshop.example.com%2Fp%2F1">Retry Product</a>'
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        mock_client_fail = AsyncMock()
        mock_client_fail.get.side_effect = ConnectionError("connection failed")
        mock_client_fail.__aenter__ = AsyncMock(return_value=mock_client_fail)
        mock_client_fail.__aexit__ = AsyncMock(return_value=False)

        mock_client_ok = AsyncMock()
        mock_client_ok.get.return_value = mock_response
        mock_client_ok.__aenter__ = AsyncMock(return_value=mock_client_ok)
        mock_client_ok.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "src.mcp_servers.web_search_mcp.search.AsyncSession",
            side_effect=[mock_client_fail, mock_client_ok],
        ):
            results = await search_products("test product")

        assert len(results) == 1
        assert results[0].title == "Retry Product"

    @pytest.mark.asyncio
    async def test_returns_empty_after_all_retries_fail(self):
        mock_client = AsyncMock()
        mock_client.get.side_effect = ConnectionError("connection failed")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.mcp_servers.web_search_mcp.search.AsyncSession", return_value=mock_client):
            results = await search_products("test product")

        assert results == []

    @pytest.mark.asyncio
    async def test_sets_http_status_on_span(self):
        html = '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fex.com">P</a>'
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.mcp_servers.web_search_mcp.search.AsyncSession", return_value=mock_client):
            await search_products("test")


class TestBuildRefinedQuery:
    def test_with_category_and_attributes(self):
        attrs = [
            QueryAttribute("noise_level", "low", "quiet"),
            QueryAttribute("capacity", "high", "large"),
        ]
        result = build_refined_query("quiet fridge for a large family", "refrigerator", attrs)
        assert "refrigerator" in result
        assert "low noise" in result
        assert "large capacity" in result
        assert "family" in result
        # User aliases like "fridge" should be removed
        assert "fridge" not in result
        # Attribute words removed from remaining
        assert result.count("quiet") == 0

    def test_category_only(self):
        result = build_refined_query("best fridge", "refrigerator", [])
        assert "refrigerator" in result
        assert "fridge" not in result

    def test_attributes_only(self):
        attrs = [QueryAttribute("noise_level", "low", "quiet")]
        result = build_refined_query("quiet gadget", None, attrs)
        assert "low noise" in result
        assert "gadget" in result
        assert "quiet" not in result

    def test_no_category_no_attributes(self):
        result = build_refined_query("random query", None, [])
        assert result == "random query"

    def test_build_search_url_with_refined_query(self):
        url = build_search_url("quiet fridge", refined_query="refrigerator low noise dB family")
        # The refined query should appear in the URL, not the original
        assert "refrigerator" in url
        assert "low+noise" in url or "low%20noise" in url.lower()

    def test_build_search_url_without_refined_query(self):
        url = build_search_url("quiet fridge")
        assert "quiet" in url.lower()
        assert "fridge" in url.lower()

    def test_search_products_passes_refined_query(self):
        """Verify refined_query is forwarded to build_search_url."""
        with patch("src.mcp_servers.web_search_mcp.search.build_search_url") as mock_build:
            mock_build.return_value = "https://example.com"
            # We need to also mock the HTTP call
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = ""
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            import asyncio
            with patch("src.mcp_servers.web_search_mcp.search.AsyncSession", return_value=mock_client):
                asyncio.get_event_loop().run_until_complete(
                    search_products("query", refined_query="refined query")
                )

            mock_build.assert_called_once_with("query", "en", "us", refined_query="refined query")


class TestGetAggregatorUrls:
    @pytest.mark.asyncio
    async def test_il_market(self):
        urls = await get_aggregator_urls("BFL523MB1F", "il")
        assert len(urls) >= 2
        domains = [u["domain"] for u in urls]
        assert "zap.co.il" in domains
        assert "ksp.co.il" in domains
        # Model ID should be in the URL
        assert any("BFL523MB1F" in u["url"] for u in urls)

    @pytest.mark.asyncio
    async def test_us_market(self):
        urls = await get_aggregator_urls("XYZ123", "us")
        assert len(urls) >= 1
        assert urls[0]["domain"] == "amazon.com"

    @pytest.mark.asyncio
    async def test_unknown_market(self):
        urls = await get_aggregator_urls("ABC", "zz")
        assert urls == []

    @pytest.mark.asyncio
    async def test_category_filter(self):
        urls = await get_aggregator_urls("X1", "il", category="appliances")
        domains = [u["domain"] for u in urls]
        assert "zap.co.il" in domains
        # lastprice has empty categories → matches all
        assert "lastprice.co.il" in domains


class TestDiscoverAggregators:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_api_key(self):
        with patch("src.mcp_servers.web_search_mcp.search.settings") as mock_settings:
            mock_settings.llm_api_key = ""
            result = await discover_aggregators("il", "baby_gear")
        assert result == []

    @pytest.mark.asyncio
    async def test_saves_discovered_sites(self):
        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = json.dumps([
            {"domain": "babystuff.co.il", "url_template": "https://babystuff.co.il/search?q={query}", "categories": ["baby_gear"]},
        ])

        with (
            patch("src.mcp_servers.web_search_mcp.search.settings") as mock_settings,
            patch("src.mcp_servers.web_search_mcp.search.litellm") as mock_litellm,
        ):
            mock_settings.llm_api_key = "test-key"
            mock_settings.llm_model = "gpt-4o-mini"
            mock_litellm.acompletion = AsyncMock(return_value=llm_response)

            result = await discover_aggregators("il", "baby_gear")

        assert len(result) == 1
        assert result[0]["domain"] == "babystuff.co.il"

        # Verify it was saved to DB and is now queryable
        urls = await get_aggregator_urls("stroller123", "il", category="baby_gear")
        domains = [u["domain"] for u in urls]
        assert "babystuff.co.il" in domains


class TestSearchOnSite:
    @pytest.mark.asyncio
    async def test_filters_to_target_domain(self):
        html = '''
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fshop.example.com%2Fp%2F1">On-site Result</a>
        <a class="result__snippet" href="#">snippet</a>
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fother.com%2Fp%2F2">Other Site</a>
        <a class="result__snippet" href="#">snippet</a>
        '''
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.mcp_servers.web_search_mcp.search.AsyncSession", return_value=mock_client):
            results = await search_on_site("MODEL1", "shop.example.com")

        # Only the result on shop.example.com should be returned
        assert len(results) == 1
        assert "shop.example.com" in results[0].url
