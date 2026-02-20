"""Core scraping orchestration with adaptive strategy."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin, urlparse

from playwright.async_api import Browser

from src.mcp_servers.web_scraper_mcp.db_cache import (
    get_cached_strategy,
    save_strategy,
    update_success_rate,
)
from src.mcp_servers.web_scraper_mcp.strategy import ScrapingStrategy, discover_strategy
from src.shared.browser import get_page
from src.shared.logging import get_logger
from src.shared.models import ProductResult, Seller

logger = get_logger(__name__)

_MAX_PRODUCTS_PER_SITE = 50


def extract_specs_from_text(
    text: str,
    criteria: dict[str, dict] | None = None,
) -> dict[str, str]:
    """Extract product specification values from free text using dynamic regex.

    Patterns are built from the provided *criteria* dict (keyed by criterion
    name, values contain at least a ``unit`` field).  When *criteria* is
    ``None`` a default set is used that covers common product specs.
    """
    if not text:
        return {}

    from src.mcp_servers.web_scraper_mcp.spec_patterns import build_extraction_patterns

    patterns = build_extraction_patterns(criteria)
    specs: dict[str, str] = {}
    for pattern, key, group_idx in patterns:
        if key in specs:
            continue
        match = pattern.search(text)
        if match:
            value = match.group(group_idx).strip()
            if value:
                specs[key] = value

    return specs


def extract_domain(url: str) -> str:
    """Extract domain from URL, stripping 'www.' prefix."""
    parsed = urlparse(url)
    domain = parsed.hostname or ""
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def parse_price(text: str) -> float | None:
    """Extract numeric price from text containing currency symbols."""
    if not text:
        return None
    # Remove currency symbols and whitespace
    cleaned = re.sub(r"[^\d.,]", "", text.strip())
    if not cleaned:
        return None
    # Handle comma as thousands separator (1,299.99) or decimal (1.299,99)
    if "," in cleaned and "." in cleaned:
        if cleaned.rindex(",") > cleaned.rindex("."):
            # European format: 1.299,99
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # US format: 1,299.99
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # Could be thousands (1,299) or decimal (12,99)
        parts = cleaned.split(",")
        if len(parts[-1]) == 2:
            # Likely decimal: 12,99
            cleaned = cleaned.replace(",", ".")
        else:
            # Likely thousands: 1,299
            cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


async def scrape_page(
    browser: Browser,
    url: str,
    product_query: str = "",
    *,
    locale: str = "en-US",
    criteria: dict[str, dict] | None = None,
) -> list[ProductResult]:
    """Scrape a product listing page using cached or newly discovered strategy.

    1. Navigate to page
    2. Check for cached strategy
    3. If cached: use it; on failure re-discover
    4. If not cached: discover strategy
    5. Extract products
    """
    domain = extract_domain(url)

    async with get_page(browser, locale=locale) as page:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            logger.warning("Failed to navigate to %s", url)
            return []

        # Wait for content
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass  # Continue even if not fully idle

        # Try cached strategy first
        cached = await get_cached_strategy(domain)
        if cached:
            logger.info("Using cached strategy for %s", domain)
            products = await _extract_with_strategy(page, cached, url, criteria=criteria)
            if products:
                await update_success_rate(domain, success=True)
                return products
            else:
                logger.info("Cached strategy failed for %s, re-discovering", domain)
                await update_success_rate(domain, success=False)

        # Discover new strategy
        strategy = await discover_strategy(page, product_query, criteria=criteria)
        if not strategy:
            logger.warning("No strategy discovered for %s", domain)
            return []

        # Save strategy
        await save_strategy(domain, strategy)

        # Extract products
        products = await _extract_with_strategy(page, strategy, url, criteria=criteria)
        if not products:
            logger.warning("Strategy discovered but no products extracted from %s", url)

        return products


async def _extract_with_strategy(
    page: object,
    strategy: ScrapingStrategy,
    base_url: str,
    *,
    criteria: dict[str, dict] | None = None,
) -> list[ProductResult]:
    """Extract products from page using a scraping strategy."""
    from src.mcp_servers.web_scraper_mcp.spec_patterns import build_extraction_patterns

    domain = extract_domain(base_url)

    try:
        containers = await page.query_selector_all(strategy.product_container)
    except Exception:
        logger.warning("Failed to find containers with '%s'", strategy.product_container)
        return []

    # Build extraction patterns once for all products on this page
    extraction_patterns = build_extraction_patterns(criteria)

    products: list[ProductResult] = []
    for container in containers[:_MAX_PRODUCTS_PER_SITE]:
        product = await _extract_single_product(
            container, strategy, base_url, domain,
            extraction_patterns=extraction_patterns,
        )
        if product:
            products.append(product)

    logger.info("Extracted %d products from %s", len(products), domain)
    return products


async def _extract_single_product(
    container: object,
    strategy: ScrapingStrategy,
    base_url: str,
    domain: str,
    *,
    extraction_patterns: list[tuple[re.Pattern, str, int]] | None = None,
) -> ProductResult | None:
    """Extract a single product from a container element.

    Criteria extraction uses two phases:
    1. CSS selectors from ``strategy.criteria_selectors`` (highest priority)
    2. Text regex fallback using pre-compiled ``extraction_patterns``
    """
    # Extract name (required)
    name = ""
    if strategy.name_selector:
        try:
            name_el = await container.query_selector(strategy.name_selector)
            if name_el:
                name = (await name_el.inner_text()).strip()
        except Exception:
            pass

    if not name:
        return None

    # Extract price
    price: float | None = None
    currency = strategy.currency_hint or "USD"
    if strategy.price_selector:
        try:
            price_el = await container.query_selector(strategy.price_selector)
            if price_el:
                price_text = (await price_el.inner_text()).strip()
                price = parse_price(price_text)
                detected_currency = _detect_currency_from_text(price_text)
                if detected_currency:
                    currency = detected_currency
        except Exception:
            pass

    # Extract product URL
    product_url: str | None = None
    if strategy.url_selector:
        try:
            url_el = await container.query_selector(strategy.url_selector)
            if url_el:
                href = await url_el.get_attribute("href")
                if href:
                    product_url = urljoin(base_url, href)
        except Exception:
            pass

    # Extract image URL
    image_url: str | None = None
    if strategy.image_selector:
        try:
            img_el = await container.query_selector(strategy.image_selector)
            if img_el:
                src = await img_el.get_attribute("src") or await img_el.get_attribute("data-src")
                if src:
                    image_url = urljoin(base_url, src)
        except Exception:
            pass

    # Extract brand
    brand: str | None = None
    if strategy.brand_selector:
        try:
            brand_el = await container.query_selector(strategy.brand_selector)
            if brand_el:
                brand = (await brand_el.inner_text()).strip()
        except Exception:
            pass

    # Extract MPN / model_id
    model_id: str | None = None
    if strategy.mpn_selector:
        try:
            mpn_el = await container.query_selector(strategy.mpn_selector)
            if mpn_el:
                model_id = (await mpn_el.inner_text()).strip()
        except Exception:
            pass

    # Fallback model_id from hash
    if not model_id:
        key = f"{brand or ''}{name}".lower().strip()
        if key:
            model_id = hashlib.md5(key.encode()).hexdigest()[:12]

    # --- Criteria extraction: CSS selectors first, then text regex fallback ---
    criteria_data: dict[str, str] = {}

    # Phase 1: CSS selectors from cached strategy
    for key, selector in strategy.criteria_selectors.items():
        try:
            el = await container.query_selector(selector)
            if el:
                text = (await el.inner_text()).strip()
                if text and len(text) < 200:
                    criteria_data[key] = text
        except Exception:
            continue

    # Phase 2: Text regex fallback for keys not yet found
    if extraction_patterns:
        try:
            full_text = (await container.inner_text()).strip()
        except Exception:
            full_text = ""
        if full_text:
            for pattern, key, group_idx in extraction_patterns:
                if key in criteria_data:
                    continue
                match = pattern.search(full_text)
                if match:
                    value = match.group(group_idx).strip()
                    if value:
                        criteria_data[key] = value

    seller = Seller(
        name=domain,
        price=price,
        currency=currency,
        url=product_url,
    )

    return ProductResult(
        name=name,
        model_id=model_id,
        brand=brand,
        image_url=image_url,
        criteria=criteria_data,
        sellers=[seller],
    )


def _detect_currency_from_text(text: str) -> str:
    """Detect currency from price text."""
    if "₪" in text:
        return "ILS"
    if "€" in text:
        return "EUR"
    if "£" in text:
        return "GBP"
    if "$" in text:
        return "USD"
    return ""
