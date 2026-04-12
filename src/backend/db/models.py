"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SearchHistory(Base):
    __tablename__ = "search_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    query: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    results_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    cross_sellers_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(String(10), default="en")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ScrapingInstruction(Base):
    __tablename__ = "scraping_instructions"
    __table_args__ = (
        UniqueConstraint("domain", "page_type", name="uq_domain_page_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    page_type: Mapped[str] = mapped_column(String(50), default="default")
    strategy_json: Mapped[str] = mapped_column(Text)
    success_rate: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ShoppingListItem(Base):
    __tablename__ = "shopping_list"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_json: Mapped[str] = mapped_column(Text)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ProductCriteriaCache(Base):
    __tablename__ = "product_criteria_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    criteria_json: Mapped[str] = mapped_column(Text)
    cache_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class KnownEcommerceDomain(Base):
    """An ecommerce domain discovered via search results or scraping outcomes."""

    __tablename__ = "known_ecommerce_domains"
    __table_args__ = (
        UniqueConstraint("domain", "market", name="uq_ecom_domain_market"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    market: Mapped[str] = mapped_column(String(10), index=True)
    source: Mapped[str] = mapped_column(String(20), default="seed")
    hit_count: Mapped[int] = mapped_column(Integer, default=1)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class AggregatorSite(Base):
    """A marketplace or price-comparison site for a specific market/category."""

    __tablename__ = "aggregator_sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    url_template: Mapped[str] = mapped_column(Text)
    market: Mapped[str] = mapped_column(String(10), index=True)
    categories_json: Mapped[str] = mapped_column(Text, default="[]")
    source: Mapped[str] = mapped_column(String(20), default="default")
    success_rate: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class SiteSearchStrategy(Base):
    """Per-domain search mechanism discovered via LLM analysis.

    Stores how to search for products on a specific e-commerce site,
    e.g. the search URL template or search input CSS selector.
    """

    __tablename__ = "site_search_strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    strategy_type: Mapped[str] = mapped_column(String(20))  # "url_template" or "search_input"
    search_url_template: Mapped[str] = mapped_column(Text, default="")  # e.g. https://site.com/search?q={query}
    search_input_selector: Mapped[str] = mapped_column(Text, default="")  # CSS selector for search <input>
    submit_selector: Mapped[str] = mapped_column(Text, default="")  # CSS selector for submit button (optional)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ConsentSelector(Base):
    """A cached CSS selector for dismissing cookie/consent banners on a domain."""

    __tablename__ = "consent_selectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    selector: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="universal")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SellerContactCache(Base):
    """Cached contact info (phone, email, WhatsApp) for a seller domain."""

    __tablename__ = "seller_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    whatsapp_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
