"""Shared data models used across the application."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from src.shared.config import settings


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


class SearchRequest(BaseModel):
    query: str
    session_id: str | None = None
    language: str = settings.default_language
    market: str | None = None


class SearchResponse(BaseModel):
    session_id: str
    status: SearchStatus
    results: list[ProductResult] = Field(default_factory=list)
    status_message: str = ""


class ShoppingListItem(BaseModel):
    product: ProductResult
    quantity: int = 1
    notes: str | None = None
