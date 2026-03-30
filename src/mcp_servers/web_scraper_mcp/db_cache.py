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
_CAPTCHA_BLOCK_HOURS = 24
_WAF_BLOCK_HOURS = 1
_MAX_CONSECUTIVE_FAILURES = 5
_VALIDATION_FAILURE_THRESHOLD = 3


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


async def update_failure(
    domain: str,
    failure_type: str,
    page_type: str = "default",
) -> None:
    """Record a pipeline failure with block detection.

    When consecutive failures exceed the threshold and the failure is a
    CAPTCHA or WAF block, the domain is marked as blocked.
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
            return

        strategy = ScrapingStrategy.from_json(record.strategy_json)
        strategy.consecutive_failures += 1
        strategy.last_failure_type = failure_type
        now_iso = datetime.now(timezone.utc).isoformat()
        strategy.last_failure_at = now_iso

        # Detect blocks based on failure type
        if failure_type == "cloudflare_captcha":
            strategy.block_type = "captcha"
            strategy.blocked_at = now_iso
            logger.warning("Domain %s marked as CAPTCHA-blocked", domain)
        elif failure_type in ("waf_blocked", "http_blocked") and strategy.consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            strategy.block_type = "waf"
            strategy.blocked_at = now_iso
            logger.warning("Domain %s marked as WAF-blocked after %d failures", domain, strategy.consecutive_failures)

        record.strategy_json = strategy.to_json()
        record.success_rate = _EMA_ALPHA * 0.0 + (1 - _EMA_ALPHA) * record.success_rate
        record.updated_at = datetime.now(timezone.utc)
        await session.commit()


async def is_domain_blocked(
    domain: str, page_type: str = "default",
) -> bool:
    """Check if a domain is currently blocked.

    CAPTCHA blocks last 24 hours. WAF blocks last 1 hour.
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
            return False

        strategy = ScrapingStrategy.from_json(record.strategy_json)
        if not strategy.block_type or not strategy.blocked_at:
            return False

        try:
            blocked_at = datetime.fromisoformat(strategy.blocked_at)
            if blocked_at.tzinfo is None:
                blocked_at = blocked_at.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return False

        now = datetime.now(timezone.utc)
        if strategy.block_type == "captcha":
            if now - blocked_at < timedelta(hours=_CAPTCHA_BLOCK_HOURS):
                return True
        elif strategy.block_type == "waf":
            if now - blocked_at < timedelta(hours=_WAF_BLOCK_HOURS):
                return True

        # Block expired — clear it
        strategy.block_type = ""
        strategy.blocked_at = ""
        strategy.consecutive_failures = 0
        record.strategy_json = strategy.to_json()
        record.updated_at = datetime.now(timezone.utc)
        await session.commit()
        logger.info("Block expired for %s/%s, cleared", domain, page_type)
        return False


async def mark_validation_failure(
    domain: str, page_type: str = "default",
) -> bool:
    """Increment validation failure counter. Returns True if threshold exceeded."""
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
            return False

        strategy = ScrapingStrategy.from_json(record.strategy_json)
        strategy.validation_failures += 1
        exceeded = strategy.validation_failures >= _VALIDATION_FAILURE_THRESHOLD

        if exceeded:
            logger.warning(
                "Validation failures for %s/%s reached %d, strategy marked stale",
                domain, page_type, strategy.validation_failures,
            )
            record.success_rate = 0.0  # Force re-discovery next time

        record.strategy_json = strategy.to_json()
        record.updated_at = datetime.now(timezone.utc)
        await session.commit()
        return exceeded


async def get_domain_health() -> list[dict]:
    """Return health summary for all tracked domains.

    Each entry includes domain, page_type, status (healthy/degraded/blocked),
    success_rate, failure metadata, and strategy info.
    """
    async with async_session() as session:
        stmt = select(ScrapingInstruction)
        result = await session.execute(stmt)
        records = result.scalars().all()

        health: list[dict] = []
        now = datetime.now(timezone.utc)

        for record in records:
            strategy = ScrapingStrategy.from_json(record.strategy_json)

            # Determine status
            if strategy.block_type:
                try:
                    blocked_at = datetime.fromisoformat(strategy.blocked_at)
                    if blocked_at.tzinfo is None:
                        blocked_at = blocked_at.replace(tzinfo=timezone.utc)
                    block_hours = _CAPTCHA_BLOCK_HOURS if strategy.block_type == "captcha" else _WAF_BLOCK_HOURS
                    if now - blocked_at < timedelta(hours=block_hours):
                        status = "blocked"
                    else:
                        status = "degraded"  # Block expired but hasn't recovered
                except (ValueError, TypeError):
                    status = "blocked"
            elif record.success_rate < _MIN_SUCCESS_RATE:
                status = "degraded"
            else:
                status = "healthy"

            health.append({
                "domain": record.domain,
                "page_type": record.page_type,
                "status": status,
                "success_rate": round(record.success_rate, 3),
                "access_method": strategy.access_method,
                "extraction_method": strategy.extraction_method,
                "block_type": strategy.block_type or None,
                "blocked_at": strategy.blocked_at or None,
                "last_failure_type": strategy.last_failure_type or None,
                "last_failure_at": strategy.last_failure_at or None,
                "consecutive_failures": strategy.consecutive_failures,
                "validation_failures": strategy.validation_failures,
                "last_successful_url": strategy.last_successful_url or None,
                "updated_at": record.updated_at.isoformat() if record.updated_at else None,
            })

        return health
