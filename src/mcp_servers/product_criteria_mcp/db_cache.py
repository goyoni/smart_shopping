"""Criteria persistence using the ProductCriteriaCache DB table."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.backend.db.engine import async_session
from src.backend.db.models import ProductCriteriaCache
from src.shared.logging import get_logger

logger = get_logger(__name__)

_CACHE_TTL_DAYS = 7
_MCP_VERSION = "1"


def _make_cache_key(category: str) -> str:
    """Generate a deterministic cache key from category + MCP version."""
    raw = f"{category}:{_MCP_VERSION}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def get_cached(category: str) -> dict | None:
    """Retrieve cached criteria for a normalized category.

    Returns None if not found or expired (TTL = 7 days).
    """
    cache_key = _make_cache_key(category)

    async with async_session() as session:
        stmt = select(ProductCriteriaCache).where(
            ProductCriteriaCache.category == category
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
            logger.info("Cached criteria for '%s' expired (TTL)", category)
            return None

        # Verify cache key matches current version
        if record.cache_key != cache_key:
            logger.info("Cached criteria for '%s' has stale version", category)
            return None

        try:
            return json.loads(record.criteria_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Corrupt criteria cache for '%s'", category)
            return None


async def save_cached(category: str, criteria: dict) -> None:
    """Save or update cached criteria for a category."""
    cache_key = _make_cache_key(category)
    criteria_json = json.dumps(criteria, ensure_ascii=False)

    async with async_session() as session:
        stmt = select(ProductCriteriaCache).where(
            ProductCriteriaCache.category == category
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()

        if record:
            record.criteria_json = criteria_json
            record.cache_key = cache_key
            record.created_at = datetime.now(timezone.utc)
        else:
            record = ProductCriteriaCache(
                category=category,
                criteria_json=criteria_json,
                cache_key=cache_key,
            )
            session.add(record)

        await session.commit()
        logger.info("Saved criteria cache for '%s'", category)
