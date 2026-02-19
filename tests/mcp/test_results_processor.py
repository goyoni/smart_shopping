"""Tests for the Results Processor MCP server."""

from __future__ import annotations

import pytest

from src.mcp_servers.results_processor_mcp.processor import (
    aggregate_sellers,
    format_results,
    validate_results,
)
from src.shared.models import ProductResult, Seller


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _product(
    name: str,
    model_id: str | None = None,
    brand: str | None = None,
    price: float | None = None,
    currency: str = "USD",
    seller_name: str = "shop.com",
    seller_url: str | None = "https://shop.com/product",
    image_url: str | None = None,
    criteria: dict | None = None,
) -> ProductResult:
    return ProductResult(
        name=name,
        model_id=model_id,
        brand=brand,
        image_url=image_url,
        criteria=criteria or {},
        sellers=[
            Seller(name=seller_name, price=price, currency=currency, url=seller_url),
        ],
    )


# ---------------------------------------------------------------------------
# validate_results
# ---------------------------------------------------------------------------

class TestValidateResults:
    def test_complete_results(self):
        products = [
            _product("Product A", price=29.99),
            _product("Product B", price=49.99),
        ]
        validated = validate_results(products)
        assert len(validated) == 2
        assert all(v["valid"] for v in validated)
        assert all(len(v["warnings"]) == 0 for v in validated)

    def test_missing_price(self):
        products = [_product("Product A", price=None)]
        validated = validate_results(products)
        assert validated[0]["valid"] is True  # Still valid (has name)
        assert "no_price" in validated[0]["warnings"]

    def test_missing_name(self):
        products = [ProductResult(name="", sellers=[Seller(name="shop.com", price=10)])]
        validated = validate_results(products)
        assert validated[0]["valid"] is False
        assert "missing_name" in validated[0]["warnings"]

    def test_completeness_score(self):
        criteria = {
            "price": {"display_name": "Price", "importance": "high"},
            "noise_level": {"display_name": "Noise", "importance": "high"},
        }
        products = [_product("Product A", price=29.99)]
        validated = validate_results(products, criteria)
        # price is matched via seller price, noise_level is not in criteria dict
        assert validated[0]["completeness"] == 0.5

    def test_missing_seller_url(self):
        products = [_product("Product A", price=29.99, seller_url=None)]
        validated = validate_results(products)
        assert "no_seller_url" in validated[0]["warnings"]

    def test_no_criteria(self):
        products = [_product("Product A", price=10)]
        validated = validate_results(products, None)
        assert validated[0]["completeness"] == 0.0


# ---------------------------------------------------------------------------
# aggregate_sellers
# ---------------------------------------------------------------------------

class TestAggregateSellers:
    def test_same_model_id(self):
        products = [
            _product("Product A", model_id="MPN-123", price=29.99, seller_name="shop1.com", seller_url="https://shop1.com/p"),
            _product("Product A", model_id="MPN-123", price=34.99, seller_name="shop2.com", seller_url="https://shop2.com/p"),
        ]
        result = aggregate_sellers(products)
        assert len(result) == 1
        assert len(result[0].sellers) == 2
        # Sellers sorted by price
        assert result[0].sellers[0].price == 29.99
        assert result[0].sellers[1].price == 34.99

    def test_fuzzy_name_match(self):
        products = [
            _product("Samsung Galaxy S24 Ultra 256GB", price=999, seller_name="a.com", seller_url="https://a.com/p"),
            _product("Samsung Galaxy S24 Ultra 256GB Black", price=989, seller_name="b.com", seller_url="https://b.com/p"),
        ]
        result = aggregate_sellers(products)
        assert len(result) == 1
        assert len(result[0].sellers) == 2

    def test_no_duplicates(self):
        products = [
            _product("Product A", model_id="MPN-A", price=10, seller_name="s1.com", seller_url="https://s1.com/p"),
            _product("Product B", model_id="MPN-B", price=20, seller_name="s2.com", seller_url="https://s2.com/p"),
        ]
        result = aggregate_sellers(products)
        assert len(result) == 2

    def test_sellers_sorted_by_price(self):
        products = [
            _product("Product A", model_id="X1", price=50, seller_name="s1.com", seller_url="https://s1.com/p"),
            _product("Product A", model_id="X1", price=30, seller_name="s2.com", seller_url="https://s2.com/p"),
            _product("Product A", model_id="X1", price=40, seller_name="s3.com", seller_url="https://s3.com/p"),
        ]
        result = aggregate_sellers(products)
        assert len(result) == 1
        prices = [s.price for s in result[0].sellers]
        assert prices == [30, 40, 50]

    def test_dedup_same_domain_same_price(self):
        products = [
            _product("Product A", model_id="X1", price=50, seller_name="s.com", seller_url="https://s.com/p1"),
            _product("Product A", model_id="X1", price=50, seller_name="s.com", seller_url="https://s.com/p2"),
        ]
        result = aggregate_sellers(products)
        assert len(result) == 1
        assert len(result[0].sellers) == 1

    def test_empty_list(self):
        assert aggregate_sellers([]) == []

    def test_hash_model_ids_not_grouped(self):
        # 12-char hex strings (MD5-based) should not cause grouping
        products = [
            _product("Different Product A", model_id="abcdef123456", price=10, seller_name="s1.com", seller_url="https://s1.com/p"),
            _product("Totally Other Thing", model_id="abcdef123456", price=20, seller_name="s2.com", seller_url="https://s2.com/p"),
        ]
        result = aggregate_sellers(products)
        # Should NOT be grouped because model_id looks like a hash
        assert len(result) == 2

    def test_merges_image_and_brand(self):
        p1 = ProductResult(
            name="Product A",
            model_id="MPN-1",
            brand=None,
            image_url=None,
            sellers=[Seller(name="s1.com", price=10, url="https://s1.com/p")],
        )
        p2 = ProductResult(
            name="Product A",
            model_id="MPN-1",
            brand="BrandX",
            image_url="https://img.com/a.jpg",
            sellers=[Seller(name="s2.com", price=20, url="https://s2.com/p")],
        )
        result = aggregate_sellers([p1, p2])
        assert len(result) == 1
        assert result[0].brand == "BrandX"
        assert result[0].image_url == "https://img.com/a.jpg"


# ---------------------------------------------------------------------------
# format_results
# ---------------------------------------------------------------------------

class TestFormatResults:
    def test_single_product_format(self):
        products = [
            _product("Cheap", price=10, seller_url="https://a.com/p"),
            _product("Expensive", price=100, seller_url="https://b.com/p"),
        ]
        result = format_results(products, "single_product")
        assert result["total_count"] == 2
        assert result["displayed_count"] == 2
        assert result["source_count"] == 2
        assert result["format_type"] == "single_product"
        # Sorted by price: cheapest first
        assert result["products"][0]["best_price"] == 10
        assert result["products"][1]["best_price"] == 100

    def test_price_comparison_format(self):
        products = [
            _product("B", price=50),
            _product("A", price=10),
        ]
        result = format_results(products, "price_comparison")
        assert result["products"][0]["best_price"] == 10
        assert result["products"][1]["best_price"] == 50

    def test_caps_results(self):
        products = [_product(f"Product {i}", price=float(i)) for i in range(30)]
        result = format_results(products, "single_product")
        assert result["total_count"] == 30
        assert result["displayed_count"] == 20

    def test_product_with_no_price(self):
        products = [_product("No Price", price=None)]
        result = format_results(products, "single_product")
        assert result["products"][0]["best_price"] is None

    def test_source_count(self):
        products = [
            _product("A", price=10, seller_url="https://shop1.com/a"),
            _product("B", price=20, seller_url="https://shop2.com/b"),
            _product("C", price=30, seller_url="https://shop1.com/c"),
        ]
        result = format_results(products, "single_product")
        assert result["source_count"] == 2
