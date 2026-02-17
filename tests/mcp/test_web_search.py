"""Tests for web search module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_servers.web_search_mcp.search import (
    SearchResult,
    _is_captcha_page,
    build_search_url,
    extract_search_results,
    search_products,
)


class TestBuildSearchUrl:
    def test_default_english_us(self):
        url = build_search_url("wireless headphones")
        assert "google.com" in url
        assert "hl=en" in url
        assert "buy+online" in url or "buy%20online" in url.lower()

    def test_hebrew_israel(self):
        url = build_search_url("אוזניות", language="he", market="il")
        assert "google.co.il" in url
        assert "hl=iw" in url

    def test_unknown_market_defaults_to_google_com(self):
        url = build_search_url("laptop", market="zz")
        assert "google.com" in url

    def test_query_augmentation_english(self):
        url = build_search_url("microwave")
        # The query should include "buy online"
        assert "buy" in url.lower()

    def test_query_augmentation_hebrew(self):
        url = build_search_url("מיקרוגל", language="he")
        assert "%D7%A7%D7%A0%D7%99%D7%99%D7%94" in url  # "קנייה" URL-encoded


class TestExtractSearchResults:
    @pytest.mark.asyncio
    async def test_extracts_results_from_page(self):
        # Mock a link element with title
        mock_title_el = AsyncMock()
        mock_title_el.inner_text.return_value = "Great Product"

        mock_link = AsyncMock()
        mock_link.get_attribute.return_value = "https://shop.example.com/product/123"
        mock_link.query_selector.return_value = mock_title_el

        mock_parent_handle = AsyncMock()
        mock_parent_el = AsyncMock()
        mock_parent_el.query_selector.return_value = None
        mock_parent_handle.as_element.return_value = mock_parent_el
        mock_link.evaluate_handle.return_value = mock_parent_handle

        mock_page = AsyncMock()
        mock_page.query_selector_all.return_value = [mock_link]

        results = await extract_search_results(mock_page)
        assert len(results) == 1
        assert results[0].url == "https://shop.example.com/product/123"
        assert results[0].title == "Great Product"

    @pytest.mark.asyncio
    async def test_skips_links_without_title(self):
        mock_link = AsyncMock()
        mock_link.get_attribute.return_value = "https://example.com"
        mock_link.query_selector.return_value = None  # No h3 title

        mock_page = AsyncMock()
        mock_page.query_selector_all.return_value = [mock_link]

        results = await extract_search_results(mock_page)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_skips_google_internal_links(self):
        mock_title = AsyncMock()
        mock_title.inner_text.return_value = "More results"

        mock_link = AsyncMock()
        mock_link.get_attribute.return_value = "https://www.google.com/search?q=test"
        mock_link.query_selector.return_value = mock_title

        mock_page = AsyncMock()
        mock_page.query_selector_all.return_value = [mock_link]

        results = await extract_search_results(mock_page)
        assert len(results) == 0


class TestCaptchaDetection:
    def test_detects_captcha_page(self):
        assert _is_captcha_page("We detected unusual traffic from your network") is True

    def test_detects_recaptcha(self):
        assert _is_captcha_page('<div class="g-recaptcha"></div>') is True

    def test_normal_page_not_captcha(self):
        assert _is_captcha_page("<html><body>Search results here</body></html>") is False


class TestSearchProducts:
    @pytest.mark.asyncio
    async def test_returns_results_on_success(self):
        mock_title_el = AsyncMock()
        mock_title_el.inner_text.return_value = "Product Title"

        mock_link = AsyncMock()
        mock_link.get_attribute.return_value = "https://shop.example.com/p/1"
        mock_link.query_selector.return_value = mock_title_el
        mock_parent_handle = AsyncMock()
        mock_parent_el = AsyncMock()
        mock_parent_el.query_selector.return_value = None
        mock_parent_handle.as_element.return_value = mock_parent_el
        mock_link.evaluate_handle.return_value = mock_parent_handle

        mock_page = AsyncMock()
        mock_page.query_selector_all.return_value = [mock_link]
        mock_page.content.return_value = "<html>normal page</html>"

        mock_browser = AsyncMock()

        with patch("src.mcp_servers.web_search_mcp.search.get_page") as mock_get_page:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_page)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_page.return_value = mock_ctx

            results = await search_products(mock_browser, "test product")

        assert len(results) == 1
        assert results[0].title == "Product Title"

    @pytest.mark.asyncio
    async def test_returns_empty_on_captcha(self):
        mock_page = AsyncMock()
        mock_page.content.return_value = "We detected unusual traffic from your network"

        mock_browser = AsyncMock()

        with patch("src.mcp_servers.web_search_mcp.search.get_page") as mock_get_page:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_page)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_page.return_value = mock_ctx

            results = await search_products(mock_browser, "test product")

        assert results == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_timeout(self):
        mock_page = AsyncMock()
        mock_page.goto.side_effect = TimeoutError("Navigation timeout")

        mock_browser = AsyncMock()

        with patch("src.mcp_servers.web_search_mcp.search.get_page") as mock_get_page:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_page)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_page.return_value = mock_ctx

            results = await search_products(mock_browser, "test product")

        assert results == []
