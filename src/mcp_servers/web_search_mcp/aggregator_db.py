"""Aggregator site persistence and discovery."""

from __future__ import annotations

import json
from urllib.parse import quote_plus

from sqlalchemy import select

from src.backend.db.engine import async_session
from src.backend.db.models import AggregatorSite
from src.shared.logging import get_logger
from src.shared.market_config import get_default_aggregators

logger = get_logger(__name__)

_EMA_ALPHA = 0.3


async def _seed_defaults() -> None:
    """Insert default aggregator entries if the table is empty."""
    async with async_session() as session:
        count_result = await session.execute(select(AggregatorSite.id).limit(1))
        if count_result.scalar_one_or_none() is not None:
            return  # Already seeded

        defaults = get_default_aggregators()
        for entry in defaults:
            record = AggregatorSite(
                domain=entry["domain"],
                url_template=entry["url_template"],
                market=entry["market"],
                categories_json=json.dumps(entry["categories"]),
                source="default",
            )
            session.add(record)
        await session.commit()
        logger.info("Seeded %d default aggregator sites", len(defaults))


async def get_aggregators(
    market: str,
    category: str | None = None,
) -> list[dict[str, str]]:
    """Return aggregator URLs for a market, optionally filtered by category.

    Returns list of ``{"domain": ..., "url_template": ...}`` dicts.
    Aggregators with empty categories list match all categories.
    """
    await _seed_defaults()

    async with async_session() as session:
        stmt = (
            select(AggregatorSite)
            .where(
                AggregatorSite.market == market,
                AggregatorSite.success_rate >= 0.3,
            )
            .order_by(AggregatorSite.success_rate.desc())
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()

    aggregators: list[dict[str, str]] = []
    for row in rows:
        cats = json.loads(row.categories_json) if row.categories_json else []
        # Empty categories list means "all categories"
        if cats and category and category not in cats:
            continue
        aggregators.append({
            "domain": row.domain,
            "url_template": row.url_template,
        })

    return aggregators


def build_aggregator_url(url_template: str, query: str) -> str:
    """Replace ``{query}`` placeholder with URL-encoded query."""
    return url_template.replace("{query}", quote_plus(query))


async def save_aggregator(
    domain: str,
    url_template: str,
    market: str,
    categories: list[str] | None = None,
    source: str = "llm",
) -> None:
    """Add or update an aggregator site."""
    async with async_session() as session:
        stmt = select(AggregatorSite).where(
            AggregatorSite.domain == domain,
            AggregatorSite.market == market,
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()

        cats_json = json.dumps(categories or [])

        if record:
            record.url_template = url_template
            record.categories_json = cats_json
            record.source = source
        else:
            record = AggregatorSite(
                domain=domain,
                url_template=url_template,
                market=market,
                categories_json=cats_json,
                source=source,
            )
            session.add(record)

        await session.commit()
        logger.info("Saved aggregator %s for market=%s", domain, market)


async def update_aggregator_success(domain: str, market: str, success: bool) -> None:
    """Update success rate for an aggregator using exponential moving average."""
    async with async_session() as session:
        stmt = select(AggregatorSite).where(
            AggregatorSite.domain == domain,
            AggregatorSite.market == market,
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            return

        new_value = 1.0 if success else 0.0
        record.success_rate = _EMA_ALPHA * new_value + (1 - _EMA_ALPHA) * record.success_rate
        await session.commit()
