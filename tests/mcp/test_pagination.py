"""Tests for pagination detection and URL generation."""

from __future__ import annotations

import pytest

from src.mcp_servers.web_scraper_mcp.scraper.pagination import (
    PaginationInfo,
    build_next_page_url,
    build_page_url,
    deduplicate_products,
    detect_url_pagination,
)
from src.shared.models import ProductResult, Seller


class TestDetectUrlPagination:
    def test_detects_page_param(self):
        result = detect_url_pagination("https://example.com/search?q=test&page=1")
        assert result == ("page", 1)

    def test_detects_p_param(self):
        result = detect_url_pagination("https://example.com/search?q=test&p=3")
        assert result == ("p", 3)

    def test_detects_offset_param(self):
        result = detect_url_pagination("https://example.com/search?q=test&offset=20")
        assert result == ("offset", 20)

    def test_no_pagination(self):
        result = detect_url_pagination("https://example.com/search?q=test")
        assert result is None

    def test_non_numeric_page(self):
        result = detect_url_pagination("https://example.com/search?page=abc")
        assert result is None

    def test_detects_pg_param(self):
        result = detect_url_pagination("https://example.com/items?pg=2&sort=price")
        assert result == ("pg", 2)

    def test_detects_start_param(self):
        result = detect_url_pagination("https://example.com/results?start=10")
        assert result == ("start", 10)


class TestBuildNextPageUrl:
    def test_increments_page(self):
        url = build_next_page_url(
            "https://example.com/search?q=test&page=1", "page", 1,
        )
        assert "page=2" in url
        assert "q=test" in url

    def test_increments_offset(self):
        url = build_next_page_url(
            "https://example.com/search?q=test&offset=0", "offset", 0,
        )
        assert "offset=1" in url

    def test_preserves_other_params(self):
        url = build_next_page_url(
            "https://example.com/search?q=test&sort=price&page=1", "page", 1,
        )
        assert "page=2" in url
        assert "q=test" in url
        assert "sort=price" in url


class TestBuildPageUrl:
    def test_builds_specific_page(self):
        url = build_page_url("https://example.com/search?q=test&page=1", "page", 5)
        assert "page=5" in url
        assert "q=test" in url

    def test_adds_page_param_if_missing(self):
        url = build_page_url("https://example.com/search?q=test", "page", 2)
        assert "page=2" in url
        assert "q=test" in url


class TestDeduplicateProducts:
    def _make_product(
        self, name: str, model_id: str = "", seller_url: str = "",
    ) -> ProductResult:
        sellers = []
        if seller_url:
            sellers.append(Seller(
                name="Test Seller", price=100.0, currency="USD",
                url=seller_url, phone=None, email=None, rating=None,
            ))
        return ProductResult(
            name=name,
            model_id=model_id or None,
            brand=None,
            product_type=None,
            category=None,
            criteria={},
            sellers=sellers,
            image_url=None,
        )

    def test_deduplicates_by_model_id(self):
        products = [
            self._make_product("Product A", model_id="ABC123"),
            self._make_product("Product A (variant)", model_id="ABC123"),
            self._make_product("Product B", model_id="DEF456"),
        ]
        result = deduplicate_products(products)
        assert len(result) == 2
        assert result[0].name == "Product A"
        assert result[1].name == "Product B"

    def test_deduplicates_by_name_and_url(self):
        products = [
            self._make_product("Product A", seller_url="https://shop.com/a"),
            self._make_product("Product A", seller_url="https://shop.com/a"),
            self._make_product("Product A", seller_url="https://other.com/a"),
        ]
        result = deduplicate_products(products)
        assert len(result) == 2

    def test_keeps_unique_products(self):
        products = [
            self._make_product("Product A", model_id="A1"),
            self._make_product("Product B", model_id="B1"),
            self._make_product("Product C", model_id="C1"),
        ]
        result = deduplicate_products(products)
        assert len(result) == 3

    def test_empty_list(self):
        result = deduplicate_products([])
        assert result == []

    def test_case_insensitive_model_id(self):
        products = [
            self._make_product("Product A", model_id="ABC123"),
            self._make_product("Product A", model_id="abc123"),
        ]
        result = deduplicate_products(products)
        assert len(result) == 1


class TestPaginationInfo:
    def test_default_values(self):
        info = PaginationInfo(pagination_type="")
        assert info.pagination_type == ""
        assert info.next_url == ""
        assert info.next_selector == ""
        assert info.current_page == 1

    def test_link_type(self):
        info = PaginationInfo(
            pagination_type="link",
            next_url="https://example.com/search?page=2",
            next_selector='a[rel="next"]',
        )
        assert info.pagination_type == "link"
        assert info.next_url == "https://example.com/search?page=2"
