"""Tests for the Results Processor MCP server."""

from __future__ import annotations

import pytest

from src.mcp_servers.product_criteria_mcp.criteria import QueryAttribute
from src.mcp_servers.results_processor_mcp.processor import (
    aggregate_sellers,
    find_cross_sellers,
    find_missing_models,
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


# ---------------------------------------------------------------------------
# Attribute-weighted sorting
# ---------------------------------------------------------------------------

class TestAttributeWeightedSorting:
    def test_products_with_matching_criteria_rank_higher(self):
        """Products with data for user-requested criteria should rank above those without."""
        p_with_data = _product(
            "Quiet Fridge",
            price=500,
            criteria={"noise_level": "38 dB", "capacity": "350L"},
        )
        p_without_data = _product(
            "Basic Fridge",
            price=400,
            criteria={},
        )
        attrs = [
            QueryAttribute("noise_level", "low", "quiet"),
            QueryAttribute("capacity", "high", "large"),
        ]
        result = format_results(
            [p_without_data, p_with_data],
            "single_product",
            user_attributes=attrs,
        )
        # Product with matching criteria data should come first
        assert result["products"][0]["product"]["name"] == "Quiet Fridge"

    def test_falls_back_to_price_when_no_attributes(self):
        """Without user_attributes, default price sort applies."""
        products = [
            _product("Expensive", price=100),
            _product("Cheap", price=10),
        ]
        result = format_results(products, "single_product", user_attributes=None)
        assert result["products"][0]["best_price"] == 10

    def test_price_tiebreak_with_equal_criteria_match(self):
        """When criteria match count is equal, cheaper product comes first."""
        p1 = _product("A", price=500, criteria={"noise_level": "40 dB"})
        p2 = _product("B", price=300, criteria={"noise_level": "35 dB"})
        attrs = [QueryAttribute("noise_level", "low", "quiet")]
        result = format_results([p1, p2], "single_product", user_attributes=attrs)
        assert result["products"][0]["best_price"] == 300

    def test_premium_price_direction(self):
        """User wanting 'premium' should get higher-priced products first."""
        p_cheap = _product("Budget", price=100, criteria={})
        p_pricey = _product("Premium", price=1000, criteria={})
        attrs = [QueryAttribute("price", "high", "premium")]
        result = format_results(
            [p_cheap, p_pricey],
            "single_product",
            user_attributes=attrs,
        )
        assert result["products"][0]["product"]["name"] == "Premium"

    def test_empty_attributes_list_uses_price_sort(self):
        """An empty attributes list should behave like no attributes."""
        products = [
            _product("Expensive", price=100),
            _product("Cheap", price=10),
        ]
        result = format_results(products, "single_product", user_attributes=[])
        assert result["products"][0]["best_price"] == 10


# ---------------------------------------------------------------------------
# find_cross_sellers
# ---------------------------------------------------------------------------


def _multi_model_product(
    name: str,
    product_type: str,
    sellers: list[Seller],
) -> ProductResult:
    return ProductResult(
        name=name,
        model_id=name,
        product_type=product_type,
        sellers=sellers,
    )


class TestFindCrossSellers:
    def test_finds_seller_with_multiple_models(self):
        products = [
            _multi_model_product(
                "Bosch Oven HBG578",
                "HBG578",
                [
                    Seller(name="megashop.com", price=500, currency="USD", url="https://megashop.com/oven"),
                    Seller(name="other.com", price=550, currency="USD", url="https://other.com/oven"),
                ],
            ),
            _multi_model_product(
                "Bosch Dishwasher SMV4",
                "SMV4HAX21E",
                [
                    Seller(name="megashop.com", price=400, currency="USD", url="https://megashop.com/dw"),
                    Seller(name="another.com", price=450, currency="USD", url="https://another.com/dw"),
                ],
            ),
        ]
        cross = find_cross_sellers(products)
        assert len(cross) == 1
        assert cross[0].domain == "megashop.com"
        assert set(cross[0].products) == {"HBG578", "SMV4HAX21E"}
        assert cross[0].total_price == 900.0
        assert cross[0].prices["HBG578"] == 500
        assert cross[0].prices["SMV4HAX21E"] == 400

    def test_no_cross_sellers_when_no_overlap(self):
        products = [
            _multi_model_product(
                "Product A",
                "ModelA",
                [Seller(name="shopA.com", price=100, url="https://shopA.com/a")],
            ),
            _multi_model_product(
                "Product B",
                "ModelB",
                [Seller(name="shopB.com", price=200, url="https://shopB.com/b")],
            ),
        ]
        cross = find_cross_sellers(products)
        assert len(cross) == 0

    def test_no_cross_sellers_with_single_model(self):
        products = [
            _multi_model_product(
                "Product A",
                "ModelA",
                [
                    Seller(name="shop.com", price=100, url="https://shop.com/a"),
                    Seller(name="shop.com", price=110, url="https://shop.com/a2"),
                ],
            ),
        ]
        cross = find_cross_sellers(products)
        assert len(cross) == 0

    def test_sorted_by_product_count_then_price(self):
        products = [
            _multi_model_product(
                "A", "ModelA",
                [
                    Seller(name="big.com", price=100, url="https://big.com/a"),
                    Seller(name="small.com", price=90, url="https://small.com/a"),
                ],
            ),
            _multi_model_product(
                "B", "ModelB",
                [
                    Seller(name="big.com", price=200, url="https://big.com/b"),
                    Seller(name="small.com", price=180, url="https://small.com/b"),
                ],
            ),
            _multi_model_product(
                "C", "ModelC",
                [
                    Seller(name="big.com", price=300, url="https://big.com/c"),
                ],
            ),
        ]
        cross = find_cross_sellers(products)
        # big.com carries 3 models, small.com carries 2
        assert len(cross) == 2
        assert cross[0].domain == "big.com"
        assert len(cross[0].products) == 3
        assert cross[1].domain == "small.com"
        assert len(cross[1].products) == 2

    def test_picks_best_price_per_model(self):
        products = [
            _multi_model_product(
                "A", "ModelA",
                [
                    Seller(name="shop.com", price=150, url="https://shop.com/a1"),
                    Seller(name="shop.com", price=100, url="https://shop.com/a2"),
                ],
            ),
            _multi_model_product(
                "B", "ModelB",
                [
                    Seller(name="shop.com", price=200, url="https://shop.com/b"),
                ],
            ),
        ]
        cross = find_cross_sellers(products)
        assert len(cross) == 1
        assert cross[0].prices["ModelA"] == 100  # Best price
        assert cross[0].total_price == 300

    def test_total_price_none_when_missing_prices(self):
        products = [
            _multi_model_product(
                "A", "ModelA",
                [Seller(name="shop.com", price=100, url="https://shop.com/a")],
            ),
            _multi_model_product(
                "B", "ModelB",
                [Seller(name="shop.com", price=None, url="https://shop.com/b")],
            ),
        ]
        cross = find_cross_sellers(products)
        assert len(cross) == 1
        assert cross[0].total_price is None

    def test_contact_info_propagated(self):
        products = [
            _multi_model_product(
                "A", "ModelA",
                [Seller(name="shop.com", price=100, url="https://shop.com/a", phone="+1234567890")],
            ),
            _multi_model_product(
                "B", "ModelB",
                [Seller(name="shop.com", price=200, url="https://shop.com/b", email="sales@shop.com")],
            ),
        ]
        cross = find_cross_sellers(products)
        assert cross[0].phone == "+1234567890"
        assert cross[0].email == "sales@shop.com"


# ---------------------------------------------------------------------------
# find_missing_models
# ---------------------------------------------------------------------------


class TestFindMissingModels:
    def test_finds_missing_models(self):
        products = [
            _multi_model_product(
                "Fridge M1", "M1",
                [
                    Seller(name="seller1.com", price=2000, url="https://seller1.com/m1"),
                    Seller(name="seller2.com", price=2050, url="https://seller2.com/m1"),
                ],
            ),
            _multi_model_product(
                "Oven M2", "M2",
                [
                    Seller(name="seller1.com", price=1500, url="https://seller1.com/m2"),
                    Seller(name="seller3.com", price=1900, url="https://seller3.com/m2"),
                ],
            ),
        ]
        missing = find_missing_models(products, ["M1", "M2"])
        # seller2 has M1 but not M2
        assert "seller2.com" in missing
        assert missing["seller2.com"] == ["M2"]
        # seller3 has M2 but not M1
        assert "seller3.com" in missing
        assert missing["seller3.com"] == ["M1"]
        # seller1 has both — should not be in missing
        assert "seller1.com" not in missing

    def test_no_missing_when_all_complete(self):
        products = [
            _multi_model_product(
                "A", "M1",
                [Seller(name="shop.com", price=100, url="https://shop.com/a")],
            ),
            _multi_model_product(
                "B", "M2",
                [Seller(name="shop.com", price=200, url="https://shop.com/b")],
            ),
        ]
        missing = find_missing_models(products, ["M1", "M2"])
        assert missing == {}

    def test_empty_products(self):
        missing = find_missing_models([], ["M1", "M2"])
        assert missing == {}

    def test_case_insensitive(self):
        products = [
            _multi_model_product(
                "A", "m1",
                [Seller(name="shop.com", price=100, url="https://shop.com/a")],
            ),
        ]
        missing = find_missing_models(products, ["M1", "M2"])
        assert "shop.com" in missing
        assert missing["shop.com"] == ["M2"]
