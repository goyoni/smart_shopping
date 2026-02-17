"""Tests for e-commerce detector module."""

from __future__ import annotations

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
    def test_known_ecommerce_domain(self):
        signal = detect_ecommerce("https://www.amazon.com/dp/B09ABC")
        assert signal.is_ecommerce is True
        assert signal.confidence >= 0.8
        assert any("known_ecommerce" in s for s in signal.signals)

    def test_known_israeli_ecommerce(self):
        signal = detect_ecommerce("https://ksp.co.il/web/cat/1234")
        assert signal.is_ecommerce is True
        assert signal.confidence >= 0.8

    def test_known_non_ecommerce(self):
        signal = detect_ecommerce("https://www.youtube.com/watch?v=xyz")
        assert signal.is_ecommerce is False
        assert signal.confidence == 0.0
        assert "known_non_ecommerce" in signal.signals

    def test_wikipedia_non_ecommerce(self):
        signal = detect_ecommerce("https://en.wikipedia.org/wiki/Microwave")
        assert signal.is_ecommerce is False

    def test_path_pattern_products(self):
        signal = detect_ecommerce("https://unknown-shop.com/products/widget-123")
        assert signal.is_ecommerce is True
        assert signal.confidence >= 0.3
        assert any("path_pattern" in s for s in signal.signals)

    def test_path_pattern_shop(self):
        signal = detect_ecommerce("https://mysite.com/shop/item-456")
        assert signal.is_ecommerce is True

    def test_keywords_in_title(self):
        signal = detect_ecommerce(
            "https://newshop.com/page",
            title="Buy the best microwave - free shipping - order now",
        )
        assert signal.is_ecommerce is True
        assert any("keywords" in s for s in signal.signals)

    def test_hebrew_keywords(self):
        signal = detect_ecommerce(
            "https://example.co.il/page",
            title="מחיר מיוחד - משלוח חינם - הזמנה עכשיו",
        )
        assert signal.is_ecommerce is True

    def test_unknown_no_signals(self):
        signal = detect_ecommerce(
            "https://blog.example.com/post/123",
            title="My Blog Post",
            snippet="This is a blog about cooking",
        )
        assert signal.is_ecommerce is False
        assert signal.confidence < 0.3

    def test_combined_signals(self):
        signal = detect_ecommerce(
            "https://www.amazon.com/dp/B123",
            title="Buy Widget - Best Price",
        )
        assert signal.is_ecommerce is True
        assert signal.confidence > 0.8  # Known domain + keywords

    def test_keyword_cap_at_0_4(self):
        signal = detect_ecommerce(
            "https://unknown.com/page",
            title="buy shop price order delivery free shipping in stock add to cart",
        )
        # Even with many keywords, keyword contribution capped at 0.4
        assert signal.confidence <= 0.4


class TestIdentifyEcommerceSites:
    def test_filters_to_ecommerce_only(self):
        urls_data = [
            {"url": "https://www.amazon.com/dp/B123", "title": "Widget", "snippet": ""},
            {"url": "https://www.youtube.com/watch?v=xyz", "title": "Review", "snippet": ""},
            {"url": "https://blog.example.com/post", "title": "Blog", "snippet": ""},
        ]
        results = identify_ecommerce_sites(urls_data)
        assert len(results) == 1
        assert results[0].domain == "amazon.com"

    def test_sorts_by_confidence_descending(self):
        urls_data = [
            {"url": "https://unknown-shop.com/products/123", "title": "", "snippet": ""},
            {"url": "https://www.amazon.com/dp/B123", "title": "Buy Widget", "snippet": ""},
        ]
        results = identify_ecommerce_sites(urls_data)
        assert len(results) == 2
        assert results[0].domain == "amazon.com"
        assert results[0].confidence >= results[1].confidence

    def test_empty_input(self):
        assert identify_ecommerce_sites([]) == []

    def test_handles_missing_fields(self):
        urls_data = [{"url": "https://www.ebay.com/itm/123"}]
        results = identify_ecommerce_sites(urls_data)
        assert len(results) == 1
        assert results[0].domain == "ebay.com"
