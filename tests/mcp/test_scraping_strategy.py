"""Tests for scraping strategy module."""

from __future__ import annotations

import json

from src.mcp_servers.web_scraper_mcp.strategy import (
    ScrapingStrategy,
    _detect_currency,
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

    def test_from_json_unknown_fields_ignored(self):
        data = json.dumps({
            "product_container": ".item",
            "name_selector": "h3",
            "unknown_future_field": "should be ignored",
        })
        strategy = ScrapingStrategy.from_json(data)
        assert strategy.product_container == ".item"
        assert strategy.name_selector == "h3"

    def test_default_values(self):
        strategy = ScrapingStrategy(product_container=".card")
        assert strategy.name_selector == ""
        assert strategy.price_selector == ""
        assert strategy.version == 1
        assert strategy.discovery_method == "css_candidates"


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
