"""CSS strategy-based product extraction from Playwright pages."""

from __future__ import annotations

import hashlib
from urllib.parse import urljoin

from src.shared.logging import get_logger
from src.shared.market_config import get_default_currency_for_domain, get_garbage_names
from src.shared.models import ProductResult, Seller

from .helpers import (
    _MAX_PRODUCTS_PER_SITE,
    _detect_currency_from_text,
    _extract_domain,
    _extract_price_from_text,
    _extraction_event,
    _parse_price,
)

logger = get_logger(__name__)


async def extract_with_strategy(
    page: object,
    strategy: object,
    base_url: str,
    *,
    criteria: dict[str, dict] | None = None,
) -> list[ProductResult]:
    """Extract products from page using a scraping strategy."""
    from src.mcp_servers.web_scraper_mcp.spec_patterns import build_extraction_patterns

    domain = _extract_domain(base_url)

    try:
        containers = await page.query_selector_all(strategy.product_container)
    except Exception:
        logger.warning("Failed to find containers with '%s'", strategy.product_container)
        _extraction_event("css_strategy_extract",
            f"Container selector '{strategy.product_container}' failed (invalid CSS or page changed)")
        return []

    _extraction_event("css_strategy_extract",
        f"Found {len(containers)} containers via '{strategy.product_container}', extracting with name='{strategy.name_selector or strategy.name_attr}' price='{strategy.price_selector or strategy.price_attr}'",
        container_count=len(containers),
    )

    extraction_patterns = build_extraction_patterns(criteria)

    products: list[ProductResult] = []
    for container in containers[:_MAX_PRODUCTS_PER_SITE]:
        product = await _extract_single_product(
            container, strategy, base_url, domain,
            extraction_patterns=extraction_patterns,
        )
        if product:
            products.append(product)

    priced = sum(1 for p in products if any(s.price is not None and s.price > 0 for s in p.sellers))
    _extraction_event("css_strategy_extract",
        f"Extracted {len(products)} products ({priced} with prices) from {len(containers)} containers on {domain}",
        product_count=len(products),
        priced_count=priced,
    )
    logger.info("Extracted %d products from %s", len(products), domain)
    return products


async def _extract_single_product(
    container: object,
    strategy: object,
    base_url: str,
    domain: str,
    *,
    extraction_patterns: list | None = None,
) -> ProductResult | None:
    """Extract a single product from a container element."""
    name = ""
    if strategy.name_attr:
        try:
            name = (await container.get_attribute(strategy.name_attr) or "").strip()
        except Exception:
            pass
    if not name and strategy.name_selector:
        try:
            name_el = await container.query_selector(strategy.name_selector)
            if name_el:
                name = (await name_el.inner_text()).strip()
        except Exception:
            pass

    if not name:
        return None
    if len(name) < 8 or name in get_garbage_names():
        return None

    price: float | None = None
    currency = strategy.currency_hint or "USD"
    if strategy.price_attr:
        try:
            price_raw = (await container.get_attribute(strategy.price_attr) or "").strip()
            if price_raw:
                price = _parse_price(price_raw)
                if price is not None:
                    currency = get_default_currency_for_domain(domain)
        except Exception:
            pass
    if price is None and strategy.price_selector:
        try:
            price_el = await container.query_selector(strategy.price_selector)
            if price_el:
                price_text = (await price_el.inner_text()).strip()
                price = _parse_price(price_text)
                detected = _detect_currency_from_text(price_text)
                if detected:
                    currency = detected
        except Exception:
            pass

    if price is None:
        try:
            container_text = (await container.inner_text()).strip()
            price, currency = _extract_price_from_text(container_text, currency)
        except Exception:
            pass

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

    brand: str | None = None
    if strategy.brand_selector:
        try:
            brand_el = await container.query_selector(strategy.brand_selector)
            if brand_el:
                brand = (await brand_el.inner_text()).strip()
        except Exception:
            pass

    model_id: str | None = None
    if strategy.mpn_selector:
        try:
            mpn_el = await container.query_selector(strategy.mpn_selector)
            if mpn_el:
                model_id = (await mpn_el.inner_text()).strip()
        except Exception:
            pass

    if not model_id:
        key = f"{brand or ''}{name}".lower().strip()
        if key:
            model_id = hashlib.md5(key.encode()).hexdigest()[:12]

    criteria_data: dict[str, str] = {}
    for key, selector in (strategy.criteria_selectors or {}).items():
        try:
            el = await container.query_selector(selector)
            if el:
                text = (await el.inner_text()).strip()
                if text and len(text) < 200:
                    criteria_data[key] = text
        except Exception:
            continue

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

    seller = Seller(name=domain, price=price, currency=currency, url=product_url)
    return ProductResult(
        name=name, model_id=model_id, brand=brand,
        image_url=image_url, criteria=criteria_data, sellers=[seller],
    )
