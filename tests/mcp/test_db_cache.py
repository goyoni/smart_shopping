"""Tests for strategy DB cache module."""

from __future__ import annotations

import pytest

from src.mcp_servers.web_scraper_mcp.db_cache import (
    get_cached_strategy,
    save_strategy,
    update_success_rate,
)
from src.mcp_servers.web_scraper_mcp.strategy import ScrapingStrategy


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
