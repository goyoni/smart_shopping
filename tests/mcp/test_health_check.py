"""Tests for proactive strategy health checker."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.backend.db.engine import async_session
from src.backend.db.models import ScrapingInstruction
from src.mcp_servers.web_scraper_mcp.db_cache import save_strategy, update_failure, update_success_rate
from src.mcp_servers.web_scraper_mcp.health_check import (
    _DEGRADED_RATE,
    _STALE_DAYS,
    check_all_strategies,
    check_single_strategy,
    get_stale_strategies,
)
from src.mcp_servers.web_scraper_mcp.strategy import ScrapingStrategy
from src.shared.models import ProductResult


# ---------------------------------------------------------------------------
# get_stale_strategies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_strategies_empty_when_all_healthy():
    """No stale strategies when everything is fresh and healthy."""
    await save_strategy("test-hc-healthy.com", ScrapingStrategy(
        product_container=".card",
        last_successful_url="https://test-hc-healthy.com/p/1",
    ))
    stale = await get_stale_strategies()
    domains = [s["domain"] for s in stale]
    assert "test-hc-healthy.com" not in domains


@pytest.mark.asyncio
async def test_stale_strategies_finds_degraded():
    """Domains with low success rate are flagged as degraded."""
    await save_strategy("test-hc-degraded.com", ScrapingStrategy(
        product_container=".card",
        last_successful_url="https://test-hc-degraded.com/p/1",
    ))
    # Drop success rate below _DEGRADED_RATE (0.7)
    for _ in range(5):
        await update_success_rate("test-hc-degraded.com", success=False)

    stale = await get_stale_strategies()
    entry = next((s for s in stale if s["domain"] == "test-hc-degraded.com"), None)
    assert entry is not None
    assert entry["reason"] == "degraded"


@pytest.mark.asyncio
async def test_stale_strategies_finds_old():
    """Domains not updated in > _STALE_DAYS are flagged as stale."""
    await save_strategy("test-hc-old.com", ScrapingStrategy(
        product_container=".card",
        last_successful_url="https://test-hc-old.com/p/1",
    ))
    # Manually backdate updated_at
    async with async_session() as session:
        from sqlalchemy import select
        stmt = select(ScrapingInstruction).where(ScrapingInstruction.domain == "test-hc-old.com")
        result = await session.execute(stmt)
        record = result.scalar_one()
        record.updated_at = datetime.now(timezone.utc) - timedelta(days=_STALE_DAYS + 1)
        await session.commit()

    stale = await get_stale_strategies()
    entry = next((s for s in stale if s["domain"] == "test-hc-old.com"), None)
    assert entry is not None
    assert entry["reason"] == "stale"


@pytest.mark.asyncio
async def test_stale_strategies_skips_blocked():
    """Blocked domains are skipped (they use their own TTL)."""
    await save_strategy("test-hc-blocked.com", ScrapingStrategy(
        product_container=".card",
        last_successful_url="https://test-hc-blocked.com/p/1",
    ))
    await update_failure("test-hc-blocked.com", "cloudflare_captcha")

    stale = await get_stale_strategies()
    domains = [s["domain"] for s in stale]
    assert "test-hc-blocked.com" not in domains


@pytest.mark.asyncio
async def test_stale_strategies_skips_no_probe_url():
    """Domains without last_successful_url are skipped."""
    await save_strategy("test-hc-noprobe.com", ScrapingStrategy(
        product_container=".card",
    ))
    # Drop success rate to trigger degraded check
    for _ in range(5):
        await update_success_rate("test-hc-noprobe.com", success=False)

    stale = await get_stale_strategies()
    domains = [s["domain"] for s in stale]
    assert "test-hc-noprobe.com" not in domains


# ---------------------------------------------------------------------------
# check_single_strategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_single_ok():
    """Successful probe returns status=ok."""
    mock_browser = AsyncMock()
    products = [ProductResult(name="Test Product", price=99.0, currency="USD", url="https://example.com/p")]

    with patch("src.mcp_servers.web_scraper_mcp.health_check.scrape_page", return_value=products):
        result = await check_single_strategy(mock_browser, "example.com", "default", "https://example.com/p")

    assert result["status"] == "ok"
    assert result["products_found"] == 1


@pytest.mark.asyncio
async def test_check_single_failed():
    """Probe returning 0 products returns status=failed."""
    mock_browser = AsyncMock()

    with patch("src.mcp_servers.web_scraper_mcp.health_check.scrape_page", return_value=[]):
        result = await check_single_strategy(mock_browser, "example.com", "default", "https://example.com/p")

    assert result["status"] == "failed"
    assert result["products_found"] == 0


@pytest.mark.asyncio
async def test_check_single_error():
    """Probe raising exception returns status=error."""
    mock_browser = AsyncMock()

    with patch("src.mcp_servers.web_scraper_mcp.health_check.scrape_page", side_effect=RuntimeError("timeout")):
        result = await check_single_strategy(mock_browser, "example.com", "default", "https://example.com/p")

    assert result["status"] == "error"
    assert "timeout" in result["detail"]


# ---------------------------------------------------------------------------
# check_all_strategies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_all_no_stale():
    """Returns empty list when no strategies are stale."""
    mock_browser = AsyncMock()

    with patch("src.mcp_servers.web_scraper_mcp.health_check.get_stale_strategies", return_value=[]):
        results = await check_all_strategies(mock_browser)

    assert results == []


@pytest.mark.asyncio
async def test_check_all_runs_checks():
    """Runs check_single_strategy for each stale entry."""
    mock_browser = AsyncMock()
    stale = [
        {"domain": "a.com", "page_type": "default", "success_rate": 0.3, "last_successful_url": "https://a.com/p", "reason": "degraded"},
        {"domain": "b.com", "page_type": "default", "success_rate": 0.9, "last_successful_url": "https://b.com/p", "reason": "stale"},
    ]
    products = [ProductResult(name="P", price=10.0, currency="USD", url="https://x.com")]

    with patch("src.mcp_servers.web_scraper_mcp.health_check.get_stale_strategies", return_value=stale), \
         patch("src.mcp_servers.web_scraper_mcp.health_check.scrape_page", return_value=products):
        results = await check_all_strategies(mock_browser)

    assert len(results) == 2
    assert all(r["status"] == "ok" for r in results)
