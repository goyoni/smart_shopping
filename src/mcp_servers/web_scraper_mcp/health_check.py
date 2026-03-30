"""Proactive strategy health checker.

Queries all cached strategies and tests stale or degraded ones
by re-scraping their last known URL. Updates strategies that have
broken and re-discovers new ones when possible.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.backend.db.engine import async_session
from src.backend.db.models import ScrapingInstruction
from src.mcp_servers.web_scraper_mcp.db_cache import save_strategy
from src.mcp_servers.web_scraper_mcp.scraper import extract_domain, scrape_page
from src.mcp_servers.web_scraper_mcp.strategy import ScrapingStrategy
from src.shared.logging import get_logger

logger = get_logger(__name__)

_STALE_DAYS = 7
_DEGRADED_RATE = 0.7


async def get_stale_strategies() -> list[dict]:
    """Find strategies that need health checking.

    Returns strategies where:
    - success_rate < 0.7 (degraded but not yet invalid)
    - updated_at > 7 days ago (haven't been tested recently)
    - have a last_successful_url to probe
    """
    async with async_session() as session:
        stmt = select(ScrapingInstruction)
        result = await session.execute(stmt)
        records = result.scalars().all()

    now = datetime.now(timezone.utc)
    stale: list[dict] = []

    for record in records:
        strategy = ScrapingStrategy.from_json(record.strategy_json)

        # Skip domains without a probe URL
        if not strategy.last_successful_url:
            continue

        # Skip blocked domains (they'll be retried after TTL expires)
        if strategy.block_type:
            continue

        is_degraded = record.success_rate < _DEGRADED_RATE
        updated = record.updated_at
        if updated and updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        is_stale = updated is not None and (now - updated) > timedelta(days=_STALE_DAYS)

        if is_degraded or is_stale:
            stale.append({
                "domain": record.domain,
                "page_type": record.page_type,
                "success_rate": record.success_rate,
                "last_successful_url": strategy.last_successful_url,
                "reason": "degraded" if is_degraded else "stale",
            })

    return stale


async def check_single_strategy(
    browser: object,
    domain: str,
    page_type: str,
    probe_url: str,
) -> dict:
    """Test a single domain's strategy by scraping its probe URL.

    Returns a result dict with status and details.
    """
    logger.info("Health check: probing %s (%s)", domain, probe_url)

    try:
        products = await scrape_page(browser, probe_url)
    except Exception as exc:
        logger.warning("Health check failed for %s: %s", domain, exc)
        return {
            "domain": domain,
            "page_type": page_type,
            "status": "error",
            "detail": str(exc),
            "products_found": 0,
        }

    if products:
        logger.info("Health check passed for %s: %d products", domain, len(products))
        return {
            "domain": domain,
            "page_type": page_type,
            "status": "ok",
            "detail": f"{len(products)} products extracted",
            "products_found": len(products),
        }

    logger.warning("Health check: %s returned 0 products", domain)
    return {
        "domain": domain,
        "page_type": page_type,
        "status": "failed",
        "detail": "0 products from probe URL",
        "products_found": 0,
    }


async def check_all_strategies(browser: object) -> list[dict]:
    """Run health checks on all stale/degraded strategies.

    Returns a list of check results.
    """
    stale = await get_stale_strategies()
    if not stale:
        logger.info("Health check: no stale strategies found")
        return []

    logger.info("Health check: %d stale strategies to check", len(stale))
    results: list[dict] = []

    for entry in stale:
        result = await check_single_strategy(
            browser,
            entry["domain"],
            entry["page_type"],
            entry["last_successful_url"],
        )
        results.append(result)

    ok = sum(1 for r in results if r["status"] == "ok")
    failed = sum(1 for r in results if r["status"] == "failed")
    errors = sum(1 for r in results if r["status"] == "error")
    logger.info("Health check complete: %d ok, %d failed, %d errors", ok, failed, errors)

    return results
