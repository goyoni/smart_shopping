"""Unit tests for shared models."""

from __future__ import annotations

from src.shared.config import settings
from src.shared.models import (
    ProductResult,
    SearchRequest,
    SearchResponse,
    SearchStatus,
    Seller,
    ShoppingListItem,
)


def test_seller_model():
    seller = Seller(name="Test Store", price=299.99, currency="USD", url="https://test.com")
    assert seller.name == "Test Store"
    assert seller.price == 299.99


def test_product_result_model():
    product = ProductResult(
        name="Test Fridge",
        model_id="FR-100",
        brand="TestBrand",
        product_type="refrigerator",
        category="kitchen appliances",
        criteria={"noise_level": "35dB", "energy_rating": "A++"},
        sellers=[Seller(name="Store A", price=500)],
    )
    assert product.name == "Test Fridge"
    assert product.model_id == "FR-100"
    assert product.brand == "TestBrand"
    assert product.product_type == "refrigerator"
    assert product.category == "kitchen appliances"
    assert len(product.sellers) == 1
    assert product.criteria["noise_level"] == "35dB"


def test_product_result_defaults():
    product = ProductResult(name="Minimal Product")
    assert product.model_id is None
    assert product.brand is None
    assert product.product_type is None
    assert product.category is None
    assert product.criteria == {}
    assert product.sellers == []
    assert product.image_url is None


def test_search_request_defaults():
    req = SearchRequest(query="test")
    assert req.language == settings.default_language
    assert req.session_id is None
    assert req.market is None


def test_search_request_with_market():
    req = SearchRequest(query="test", market="il")
    assert req.market == "il"


def test_search_response_model():
    resp = SearchResponse(
        session_id="abc123",
        status=SearchStatus.COMPLETED,
        results=[],
        status_message="Done",
    )
    assert resp.status == SearchStatus.COMPLETED
    assert resp.results == []


def test_shopping_list_item():
    product = ProductResult(name="Fridge", sellers=[])
    item = ShoppingListItem(product=product, quantity=2, notes="Black color")
    assert item.quantity == 2
    assert item.notes == "Black color"
