"""Shared data models used across the application."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, Field

from src.shared.config import settings


@dataclass
class CriterionSpec:
    """Specification for a single product criterion."""

    display_name: str
    unit: str = ""
    importance: str = "medium"
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "display_name": self.display_name,
            "unit": self.unit,
            "importance": self.importance,
            "description": self.description,
        }


@dataclass
class QueryAttribute:
    """A user-intent attribute extracted from a search query."""

    criterion_key: str
    direction: str  # "low" or "high"
    display_label: str


class SearchStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class Seller(BaseModel):
    name: str
    price: float | None = None
    currency: str = "USD"
    url: str | None = None
    phone: str | None = None
    email: str | None = None
    rating: float | None = None


class ProductResult(BaseModel):
    name: str
    model_id: str | None = None
    brand: str | None = None
    product_type: str | None = None
    category: str | None = None
    criteria: dict[str, str | float | bool] = Field(default_factory=dict)
    sellers: list[Seller] = Field(default_factory=list)
    image_url: str | None = None


class CrossSeller(BaseModel):
    """A seller that carries multiple products from a multi-model search."""

    name: str
    domain: str
    url: str | None = None
    phone: str | None = None
    email: str | None = None
    products: list[str] = Field(default_factory=list)
    prices: dict[str, float] = Field(default_factory=dict)
    total_price: float | None = None
    currency: str = "USD"


class SearchRequest(BaseModel):
    query: str
    session_id: str | None = None
    language: str = settings.default_language
    market: str | None = None


class SearchResponse(BaseModel):
    session_id: str
    status: SearchStatus
    query: str = ""
    results: list[ProductResult] = Field(default_factory=list)
    cross_sellers: list[CrossSeller] = Field(default_factory=list)
    status_message: str = ""


class ShoppingListItem(BaseModel):
    id: int | None = None
    product: ProductResult
    quantity: int = 1
    notes: str | None = None


class ShoppingListResponse(BaseModel):
    items: list[ShoppingListItem] = Field(default_factory=list)


class AddToShoppingListRequest(BaseModel):
    product: ProductResult
    quantity: int = 1
    notes: str | None = None


class SearchHistoryItem(BaseModel):
    session_id: str
    query: str
    status: str
    result_count: int
    created_at: str


class SearchHistoryResponse(BaseModel):
    items: list[SearchHistoryItem] = Field(default_factory=list)
