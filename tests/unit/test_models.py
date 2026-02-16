"""Unit tests for shared models."""

from __future__ import annotations

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
        model="FR-100",
        criteria={"noise_level": "35dB", "energy_rating": "A++"},
        sellers=[Seller(name="Store A", price=500)],
    )
    assert product.name == "Test Fridge"
    assert len(product.sellers) == 1
    assert product.criteria["noise_level"] == "35dB"


def test_search_request_defaults():
    req = SearchRequest(query="test")
    assert req.language == "en"
    assert req.session_id is None


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
