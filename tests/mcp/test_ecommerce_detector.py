"""Tests for e-commerce detector module."""

from __future__ import annotations

import pytest

from src.mcp_servers.web_search_mcp.ecommerce_detector import (
    EcommerceSignal,
    detect_ecommerce,
    extract_domain,
    identify_ecommerce_sites,
)


class TestExtractDomain:
    def test_strips_www_prefix(self):
        assert extract_domain("https://www.amazon.com/dp/123") == "amazon.com"

    def test_without_www(self):
        assert extract_domain("https://ksp.co.il/product/123") == "ksp.co.il"

    def test_with_port(self):
        assert extract_domain("http://localhost:8080/shop") == "localhost"

    def test_empty_url(self):
        assert extract_domain("") == ""


class TestDetectEcommerce:
    def test_known_non_ecommerce(self):
        signal = detect_ecommerce("https://www.youtube.com/watch?v=xyz")
        assert signal.is_ecommerce is False
        assert signal.confidence == 0.0
        assert "known_non_ecommerce" in signal.signals

    def test_wikipedia_non_ecommerce(self):
        signal = detect_ecommerce("https://en.wikipedia.org/wiki/Microwave")
        assert signal.is_ecommerce is False

    def test_manufacturer_site_rejected(self):
        signal = detect_ecommerce("https://us.braun.com/en-us/service/products/6031")
        assert signal.is_ecommerce is False
        assert "manufacturer_site" in signal.signals

    def test_manufacturer_site_country_variant(self):
        signal = detect_ecommerce("https://www.braun.hu/hu-hu/male-grooming")
        assert signal.is_ecommerce is False

    def test_path_pattern_products(self):
        signal = detect_ecommerce("https://unknown-shop.com/products/widget-123")
        assert signal.is_ecommerce is True
        assert signal.confidence >= 0.3
        assert any("path_pattern" in s for s in signal.signals)

    def test_path_pattern_product(self):
        signal = detect_ecommerce(
            "https://www.public.gr/product/prosopiki-frontida/apotrixotiki-mixani/1925186"
        )
        assert signal.is_ecommerce is True
        assert any("path_pattern" in s for s in signal.signals)

    def test_path_pattern_shop(self):
        signal = detect_ecommerce("https://mysite.com/shop/item-456")
        assert signal.is_ecommerce is True

    def test_path_pattern_dp(self):
        """Amazon /dp/ path should be detected."""
        signal = detect_ecommerce("https://www.amazon.com/dp/B123")
        assert signal.is_ecommerce is True
        assert any("path_pattern" in s for s in signal.signals)

    def test_keywords_in_title(self):
        signal = detect_ecommerce(
            "https://newshop.com/page",
            title="Buy the best microwave - free shipping - order now",
        )
        assert signal.is_ecommerce is True
        assert any("keywords" in s for s in signal.signals)

    def test_unknown_no_signals(self):
        signal = detect_ecommerce(
            "https://blog.example.com/post/123",
            title="My Blog Post",
            snippet="This is a blog about cooking",
        )
        assert signal.is_ecommerce is False
        assert signal.confidence < 0.3

    def test_combined_signals(self):
        """Path pattern + keywords should stack for higher confidence."""
        signal = detect_ecommerce(
            "https://www.example.com/products/table",
            title="Buy this table - best price",
        )
        assert signal.is_ecommerce is True
        assert signal.confidence > 0.4  # Path + keywords

    def test_keyword_cap_at_0_6(self):
        signal = detect_ecommerce(
            "https://unknown.com/page",
            title="buy shop price order delivery free shipping in stock add to cart",
        )
        # Even with many keywords, keyword contribution capped at 0.6
        assert signal.confidence <= 0.6

    def test_currency_symbols_as_keywords(self):
        """Currency symbols should work as universal ecommerce keywords."""
        signal = detect_ecommerce(
            "https://shop.example.com/page",
            snippet="Great product for only €199",
        )
        assert any("keywords" in s for s in signal.signals)


class TestIdentifyEcommerceSites:
    @pytest.mark.asyncio
    async def test_filters_to_ecommerce_only(self):
        urls_data = [
            {"url": "https://www.amazon.com/dp/B123", "title": "Widget", "snippet": ""},
            {"url": "https://www.youtube.com/watch?v=xyz", "title": "Review", "snippet": ""},
            {"url": "https://blog.example.com/post", "title": "Blog", "snippet": ""},
        ]
        results = await identify_ecommerce_sites(urls_data)
        # amazon.com should be detected via path pattern /dp/
        ecom_domains = {r.domain for r in results}
        assert "amazon.com" in ecom_domains
        assert "youtube.com" not in ecom_domains

    @pytest.mark.asyncio
    async def test_empty_input(self):
        assert await identify_ecommerce_sites([]) == []

    @pytest.mark.asyncio
    async def test_handles_missing_fields(self):
        """URL with product path but no title/snippet should still be detected."""
        urls_data = [{"url": "https://www.ebay.com/item/123"}]
        results = await identify_ecommerce_sites(urls_data)
        assert len(results) >= 1
        assert results[0].domain == "ebay.com"

    @pytest.mark.asyncio
    async def test_market_tld_boost(self):
        """Greek sites should rank above non-market sites when market=gr."""
        urls_data = [
            {"url": "https://www.bestbuy.com/product/widget/123", "title": "Buy widget", "snippet": "price $99"},
            {"url": "https://www.public.gr/product/widget/123", "title": "Widget", "snippet": "€99"},
        ]
        results = await identify_ecommerce_sites(urls_data, market="gr")
        assert len(results) == 2
        # Greek site should rank first
        assert results[0].domain == "public.gr"
        assert any("market_match" in s for s in results[0].signals)


class TestPathPatterns:
    def test_path_pattern_and_keywords_stack(self):
        """Path pattern + keywords should stack for higher confidence."""
        signal = detect_ecommerce(
            "https://www.example.com/products/table",
            title="Buy this table - best price",
        )
        assert signal.is_ecommerce is True
        assert signal.confidence > 0.4

    def test_com_domain_no_tld_boost(self):
        """.com domains should NOT get the CC-TLD boost."""
        signal = detect_ecommerce("https://blog.example.com/post/123")
        assert not any("commercial_tld" in s for s in signal.signals)
