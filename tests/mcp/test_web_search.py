"""Tests for web search module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.mcp_servers.web_search_mcp.search import (
    SearchResult,
    _extract_ddg_url,
    build_search_url,
    extract_search_results,
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
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = html

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.mcp_servers.web_search_mcp.search.httpx.AsyncClient", return_value=mock_client):
            results = await search_products("test product")

        assert len(results) == 1
        assert results[0].title == "Product Title"

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 429
        mock_response.text = ""

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.mcp_servers.web_search_mcp.search.httpx.AsyncClient", return_value=mock_client):
            results = await search_products("test product")

        assert results == []

    @pytest.mark.asyncio
    async def test_retries_on_network_error(self):
        html = '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fshop.example.com%2Fp%2F1">Retry Product</a>'
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = html

        mock_client_fail = AsyncMock(spec=httpx.AsyncClient)
        mock_client_fail.get.side_effect = httpx.ConnectError("connection failed")
        mock_client_fail.__aenter__ = AsyncMock(return_value=mock_client_fail)
        mock_client_fail.__aexit__ = AsyncMock(return_value=False)

        mock_client_ok = AsyncMock(spec=httpx.AsyncClient)
        mock_client_ok.get.return_value = mock_response
        mock_client_ok.__aenter__ = AsyncMock(return_value=mock_client_ok)
        mock_client_ok.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "src.mcp_servers.web_search_mcp.search.httpx.AsyncClient",
            side_effect=[mock_client_fail, mock_client_ok],
        ):
            results = await search_products("test product")

        assert len(results) == 1
        assert results[0].title == "Retry Product"

    @pytest.mark.asyncio
    async def test_returns_empty_after_all_retries_fail(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.side_effect = httpx.ConnectError("connection failed")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.mcp_servers.web_search_mcp.search.httpx.AsyncClient", return_value=mock_client):
            results = await search_products("test product")

        assert results == []

    @pytest.mark.asyncio
    async def test_sets_http_status_on_span(self):
        html = '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fex.com">P</a>'
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = html

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.mcp_servers.web_search_mcp.search.httpx.AsyncClient", return_value=mock_client):
            await search_products("test")
