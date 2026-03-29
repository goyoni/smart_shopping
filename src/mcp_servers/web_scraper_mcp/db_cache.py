"""Strategy persistence using the ScrapingInstruction DB table."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select

from src.backend.db.engine import async_session
from src.backend.db.models import ScrapingInstruction
from src.mcp_servers.web_scraper_mcp.strategy import ScrapingStrategy
from src.shared.logging import get_logger

logger = get_logger(__name__)

_CACHE_TTL_DAYS = 30
_MIN_SUCCESS_RATE = 0.5
_EMA_ALPHA = 0.3


async def get_cached_strategy(
    domain: str, page_type: str = "default",
) -> ScrapingStrategy | None:
    """Load cached strategy for a domain + page_type.

    Returns None if:
    - No record exists
    - Record is older than TTL (30 days)
    - Success rate is below threshold (0.5)
    """
    async with async_session() as session:
        stmt = select(ScrapingInstruction).where(
            and_(
                ScrapingInstruction.domain == domain,
                ScrapingInstruction.page_type == page_type,
            )
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()

        if not record:
            return None

        # Check TTL
        now = datetime.now(timezone.utc)
        created = record.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if now - created > timedelta(days=_CACHE_TTL_DAYS):
            logger.info("Cached strategy for %s/%s expired (TTL)", domain, page_type)
            return None

        # Check success rate
        if record.success_rate < _MIN_SUCCESS_RATE:
            logger.info(
                "Cached strategy for %s/%s has low success rate (%.2f)",
                domain, page_type, record.success_rate,
            )
            return None

        return ScrapingStrategy.from_json(record.strategy_json)


async def save_strategy(
    domain: str, strategy: ScrapingStrategy, page_type: str = "default",
) -> None:
    """Save or update (upsert) a strategy for a domain + page_type."""
    async with async_session() as session:
        stmt = select(ScrapingInstruction).where(
            and_(
                ScrapingInstruction.domain == domain,
                ScrapingInstruction.page_type == page_type,
            )
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()

        if record:
            record.strategy_json = strategy.to_json()
            record.success_rate = 1.0
            record.updated_at = datetime.now(timezone.utc)
        else:
            record = ScrapingInstruction(
                domain=domain,
                page_type=page_type,
                strategy_json=strategy.to_json(),
                success_rate=1.0,
            )
            session.add(record)

        await session.commit()
        logger.info("Saved strategy for %s/%s", domain, page_type)


async def update_success_rate(
    domain: str, success: bool, page_type: str = "default",
) -> None:
    """Update success rate using exponential moving average (alpha=0.3)."""
    async with async_session() as session:
        stmt = select(ScrapingInstruction).where(
            and_(
                ScrapingInstruction.domain == domain,
                ScrapingInstruction.page_type == page_type,
            )
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()

        if not record:
            return

        new_value = 1.0 if success else 0.0
        record.success_rate = _EMA_ALPHA * new_value + (1 - _EMA_ALPHA) * record.success_rate
        record.updated_at = datetime.now(timezone.utc)
        await session.commit()
        logger.info("Updated success rate for %s/%s: %.2f", domain, page_type, record.success_rate)
