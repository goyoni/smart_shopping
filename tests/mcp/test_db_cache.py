"""Tests for strategy DB cache module."""

from __future__ import annotations

import pytest

from sqlalchemy import and_, select

from src.backend.db.engine import async_session
from src.backend.db.models import ScrapingInstruction
from src.mcp_servers.web_scraper_mcp.db_cache import (
    get_cached_strategy,
    is_domain_blocked,
    mark_validation_failure,
    save_strategy,
    update_failure,
    update_success_rate,
)
from src.mcp_servers.web_scraper_mcp.strategy import ScrapingStrategy


async def _get_raw_strategy(domain: str, page_type: str = "default") -> ScrapingStrategy | None:
    """Read strategy directly from DB, bypassing success rate / TTL checks."""
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
        return ScrapingStrategy.from_json(record.strategy_json)


@pytest.mark.asyncio
async def test_save_and_get_roundtrip():
    strategy = ScrapingStrategy(
        product_container=".product-card",
        name_selector="h2 a",
        price_selector=".price",
        currency_hint="USD",
    )
    await save_strategy("test-roundtrip.com", strategy)

    cached = await get_cached_strategy("test-roundtrip.com")
    assert cached is not None
    assert cached.product_container == ".product-card"
    assert cached.name_selector == "h2 a"
    assert cached.currency_hint == "USD"


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown():
    result = await get_cached_strategy("nonexistent-domain-12345.com")
    assert result is None


@pytest.mark.asyncio
async def test_save_upsert():
    strategy1 = ScrapingStrategy(
        product_container=".old-card",
        name_selector="h2",
    )
    await save_strategy("test-upsert.com", strategy1)

    strategy2 = ScrapingStrategy(
        product_container=".new-card",
        name_selector="h3",
    )
    await save_strategy("test-upsert.com", strategy2)

    cached = await get_cached_strategy("test-upsert.com")
    assert cached is not None
    assert cached.product_container == ".new-card"
    assert cached.name_selector == "h3"


@pytest.mark.asyncio
async def test_success_rate_decay():
    strategy = ScrapingStrategy(
        product_container=".card",
        name_selector="h2",
    )
    await save_strategy("test-decay.com", strategy)

    # Fail multiple times
    await update_success_rate("test-decay.com", success=False)
    await update_success_rate("test-decay.com", success=False)
    await update_success_rate("test-decay.com", success=False)

    # After 3 failures with alpha=0.3:
    # rate = 0.3*0 + 0.7*1.0 = 0.7
    # rate = 0.3*0 + 0.7*0.7 = 0.49
    # rate = 0.3*0 + 0.7*0.49 = 0.343
    # Still above 0.5 threshold after 2 fails, but below after ~3
    cached = await get_cached_strategy("test-decay.com")
    # After 3 consecutive failures the rate should be below 0.5
    # so get_cached_strategy should return None
    assert cached is None


@pytest.mark.asyncio
async def test_success_rate_recovery():
    strategy = ScrapingStrategy(
        product_container=".card",
        name_selector="h2",
    )
    await save_strategy("test-recovery.com", strategy)

    # One failure
    await update_success_rate("test-recovery.com", success=False)
    # rate = 0.7

    # One success
    await update_success_rate("test-recovery.com", success=True)
    # rate = 0.3*1 + 0.7*0.7 = 0.79

    cached = await get_cached_strategy("test-recovery.com")
    assert cached is not None  # Should still be accessible


# ---------------------------------------------------------------------------
# Domain block tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_captcha_blocks_domain():
    """CAPTCHA failure immediately blocks the domain."""
    strategy = ScrapingStrategy(product_container=".card")
    await save_strategy("test-captcha-block.com", strategy)

    await update_failure("test-captcha-block.com", "cloudflare_captcha")

    assert await is_domain_blocked("test-captcha-block.com") is True


@pytest.mark.asyncio
async def test_waf_blocks_after_threshold():
    """WAF failures only block after consecutive failure threshold."""
    strategy = ScrapingStrategy(product_container=".card")
    await save_strategy("test-waf-block.com", strategy)

    # Below threshold — not blocked
    for _ in range(4):
        await update_failure("test-waf-block.com", "waf_blocked")
    assert await is_domain_blocked("test-waf-block.com") is False

    # Hit threshold (5th failure)
    await update_failure("test-waf-block.com", "waf_blocked")
    assert await is_domain_blocked("test-waf-block.com") is True


@pytest.mark.asyncio
async def test_unblocked_domain():
    """Domain without block info is not blocked."""
    strategy = ScrapingStrategy(product_container=".card")
    await save_strategy("test-unblocked.com", strategy)

    assert await is_domain_blocked("test-unblocked.com") is False


@pytest.mark.asyncio
async def test_unknown_domain_not_blocked():
    """Non-existent domain is not blocked."""
    assert await is_domain_blocked("never-seen-before-12345.com") is False


@pytest.mark.asyncio
async def test_failure_tracking_increments():
    """Each failure increments consecutive_failures."""
    strategy = ScrapingStrategy(product_container=".card")
    await save_strategy("test-fail-count.com", strategy)

    await update_failure("test-fail-count.com", "http_blocked")
    await update_failure("test-fail-count.com", "http_blocked")

    # Use raw read (bypasses success rate check which may filter it out)
    cached = await _get_raw_strategy("test-fail-count.com")
    assert cached is not None
    assert cached.consecutive_failures == 2
    assert cached.last_failure_type == "http_blocked"


# ---------------------------------------------------------------------------
# Validation failure tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_failure_below_threshold():
    """Below threshold, mark_validation_failure returns False."""
    strategy = ScrapingStrategy(product_container=".card")
    await save_strategy("test-val-ok.com", strategy)

    result = await mark_validation_failure("test-val-ok.com")
    assert result is False


@pytest.mark.asyncio
async def test_validation_failure_exceeds_threshold():
    """At threshold, mark_validation_failure returns True and zeros success rate."""
    strategy = ScrapingStrategy(product_container=".card")
    await save_strategy("test-val-stale.com", strategy)

    await mark_validation_failure("test-val-stale.com")
    await mark_validation_failure("test-val-stale.com")
    result = await mark_validation_failure("test-val-stale.com")

    assert result is True
    # Strategy should be invalidated (success_rate = 0)
    cached = await get_cached_strategy("test-val-stale.com")
    assert cached is None  # Below success rate threshold
