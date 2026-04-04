"""CSS-based and price-pattern strategy discovery."""

from __future__ import annotations

from opentelemetry import trace as otel_trace
from playwright.async_api import Page

from src.shared.logging import get_logger

from .candidates import (
    CONTAINER_CANDIDATES,
    DATA_ATTR_NAME_CANDIDATES,
    DATA_ATTR_PRICE_CANDIDATES,
    IMAGE_CANDIDATES,
    NAME_CANDIDATES,
    PRICE_CANDIDATES,
    SINGLE_PRODUCT_CONTAINER_CANDIDATES,
    SINGLE_PRODUCT_NAME_CANDIDATES,
    SINGLE_PRODUCT_PRICE_CANDIDATES,
    URL_CANDIDATES,
)
from .helpers import (
    _detect_currency,
    _discover_criteria_selectors,
    _find_data_attr,
    _find_selector,
    _looks_like_price,
)
from .llm_discovery import _discover_via_llm
from .model import ScrapingStrategy

logger = get_logger(__name__)


async def discover_strategy(
    page: Page,
    product_query: str = "",
    criteria: dict[str, dict] | None = None,
) -> ScrapingStrategy | None:
    """Discover scraping strategy by trying CSS selector candidates.

    Requires at least 2 matching containers to consider a strategy valid.
    Falls back to price-pattern discovery if CSS candidates fail.
    """
    span = otel_trace.get_current_span()

    def _strategy_event(step: str, detail: str, **attrs: object) -> None:
        if span and span.is_recording():
            span.add_event(f"strategy.{step}", {"detail": detail, **attrs})

    _strategy_event("start", f"Beginning strategy discovery for query='{product_query[:80]}'")

    # Try each container candidate
    css_tried = 0
    for container_selector in CONTAINER_CANDIDATES:
        try:
            containers = await page.query_selector_all(container_selector)
        except Exception:
            continue

        if len(containers) < 2:
            continue

        css_tried += 1
        logger.info(
            "Found %d containers with selector '%s'",
            len(containers), container_selector,
        )

        # Probe up to 3 containers to find sub-selectors
        name_sel = ""
        price_sel = ""
        image_sel = ""
        url_sel = ""
        name_data_attr = ""
        price_data_attr = ""
        for probe in containers[:3]:
            if not name_sel:
                name_sel = await _find_selector(probe, NAME_CANDIDATES)
            if not price_sel:
                price_sel = await _find_selector(probe, PRICE_CANDIDATES)
            if not image_sel:
                image_sel = await _find_selector(probe, IMAGE_CANDIDATES)
            if not url_sel:
                url_sel = await _find_selector(probe, URL_CANDIDATES)
            if not name_data_attr:
                name_data_attr = await _find_data_attr(probe, DATA_ATTR_NAME_CANDIDATES)
            if not price_data_attr:
                price_data_attr = await _find_data_attr(probe, DATA_ATTR_PRICE_CANDIDATES)

        # Must find at least name selector OR name data attribute
        if not name_sel and not name_data_attr:
            _strategy_event("css_candidates", f"'{container_selector}' has {len(containers)} containers but no name selector found", selector=container_selector)
            continue

        # Use the first container that has a name for currency/criteria probing
        probe_container = containers[0]
        for c in containers[:3]:
            try:
                el = await c.query_selector(name_sel)
                if el:
                    probe_container = c
                    break
            except Exception:
                continue

        # Detect currency from price if available
        currency_hint = ""
        if price_sel:
            try:
                price_el = await probe_container.query_selector(price_sel)
                if price_el:
                    price_text = await price_el.inner_text()
                    currency_hint = _detect_currency(price_text)
            except Exception:
                pass

        # Discover per-criterion CSS selectors
        criteria_sels = await _discover_criteria_selectors(probe_container, criteria)

        _strategy_event("css_candidates",
            f"SUCCESS: container='{container_selector}' name='{name_sel or name_data_attr}' price='{price_sel or price_data_attr}' ({len(containers)} elements)",
            discovery_method="css_candidates",
            container_count=len(containers),
        )
        if span and span.is_recording():
            span.set_attribute("strategy.summary",
                f"css_candidates found {len(containers)} containers via '{container_selector}', name='{name_sel or name_data_attr}', price='{price_sel or price_data_attr}', currency={currency_hint}")

        return ScrapingStrategy(
            product_container=container_selector,
            name_selector=name_sel,
            price_selector=price_sel,
            image_selector=image_sel,
            url_selector=url_sel,
            currency_hint=currency_hint,
            discovery_method="css_candidates",
            criteria_selectors=criteria_sels,
            name_attr=name_data_attr,
            price_attr=price_data_attr,
        )

    _strategy_event("css_candidates", f"No CSS candidate worked (tried {css_tried} with >=2 containers)")

    # Fallback 1: single-product page discovery (product detail pages)
    logger.info("[strategy] CSS candidates failed, trying single-product fallback")
    _strategy_event("single_product", "Trying single-product page detection...")
    strategy = await _discover_single_product(page, criteria)
    if strategy:
        logger.info("[strategy] single-product discovery succeeded: container='%s'", strategy.product_container[:60])
        _strategy_event("single_product",
            f"SUCCESS: container='{strategy.product_container}' name='{strategy.name_selector}' price='{strategy.price_selector}'",
            discovery_method="single_product",
        )
        if span and span.is_recording():
            span.set_attribute("strategy.summary",
                f"single_product: container='{strategy.product_container}', name='{strategy.name_selector}', price='{strategy.price_selector}'")
        return strategy

    _strategy_event("single_product", "Failed: no h1+price combination found")

    # Fallback 2: price-pattern based discovery
    logger.info("[strategy] single-product failed, trying price-pattern fallback")
    _strategy_event("price_pattern", "Trying price-pattern JS walker...")
    strategy = await _discover_by_price_pattern(page)
    if strategy:
        logger.info("[strategy] price-pattern discovery succeeded: container='%s'", strategy.product_container[:60])
        _strategy_event("price_pattern",
            f"SUCCESS: container='{strategy.product_container}'",
            discovery_method="price_pattern",
        )
        if span and span.is_recording():
            span.set_attribute("strategy.summary",
                f"price_pattern: container='{strategy.product_container}'")
        return strategy

    _strategy_event("price_pattern", "Failed: no repeating price pattern found")

    # Fallback 3: LLM-based discovery
    logger.info("[strategy] price-pattern failed, trying LLM fallback")
    _strategy_event("llm", "Trying LLM-based DOM analysis...")
    strategy = await _discover_via_llm(page, product_query, criteria)
    if strategy:
        logger.info("[strategy] LLM discovery succeeded: container='%s'", strategy.product_container[:60])
        _strategy_event("llm",
            f"SUCCESS: container='{strategy.product_container}' name='{strategy.name_selector}' price='{strategy.price_selector}'",
            discovery_method="llm",
        )
        if span and span.is_recording():
            span.set_attribute("strategy.summary",
                f"llm: container='{strategy.product_container}', name='{strategy.name_selector}', price='{strategy.price_selector}'")
        return strategy

    _strategy_event("llm", "Failed: LLM could not identify product selectors")

    logger.warning("[strategy] All discovery methods failed (css_candidates -> single_product -> price_pattern -> llm)")
    if span and span.is_recording():
        span.set_attribute("strategy.summary", "ALL FAILED: css_candidates -> single_product -> price_pattern -> llm")
    return None


async def _discover_single_product(
    page: Page,
    criteria: dict[str, dict] | None = None,
) -> ScrapingStrategy | None:
    """Discover strategy for a single-product detail page."""
    # Find a product name element, skipping elements inside nav/header/footer
    name_sel = ""
    for selector in SINGLE_PRODUCT_NAME_CANDIDATES:
        try:
            els = await page.query_selector_all(selector)
            for el in els:
                in_excluded = await page.evaluate(
                    """(el) => {
                        let node = el;
                        while (node) {
                            const tag = node.tagName?.toLowerCase() || '';
                            const cls = (node.className || '').toLowerCase();
                            if (['nav', 'header', 'footer'].includes(tag)) return true;
                            if (cls.includes('nav') || cls.includes('menu') ||
                                cls.includes('sidebar') || cls.includes('breadcrumb') ||
                                cls.includes('header') || cls.includes('footer')) return true;
                            node = node.parentElement;
                        }
                        return false;
                    }""",
                    el,
                )
                if in_excluded:
                    continue
                text = (await el.inner_text()).strip()
                if text and 3 <= len(text) <= 300:
                    name_sel = selector
                    break
            if name_sel:
                break
        except Exception:
            continue

    if not name_sel:
        return None

    # Find a price element
    price_sel = ""
    currency_hint = ""
    for selector in SINGLE_PRODUCT_PRICE_CANDIDATES:
        try:
            el = await page.query_selector(selector)
            if el:
                text = (await el.inner_text()).strip()
                if _looks_like_price(text):
                    price_sel = selector
                    currency_hint = _detect_currency(text)
                    break
        except Exception:
            continue

    if not price_sel:
        return None

    # Find a container that wraps both name and price
    container_sel = ""
    for selector in SINGLE_PRODUCT_CONTAINER_CANDIDATES:
        try:
            container = await page.query_selector(selector)
            if container:
                has_name = await container.query_selector(name_sel)
                has_price = await container.query_selector(price_sel)
                if has_name and has_price:
                    container_sel = selector
                    break
        except Exception:
            continue

    if not container_sel:
        container_sel = "body"

    probe = await page.query_selector(container_sel)
    image_sel = ""
    if probe:
        image_sel = await _find_selector(probe, IMAGE_CANDIDATES)
        criteria_sels = await _discover_criteria_selectors(probe, criteria)
    else:
        criteria_sels = {}

    logger.info(
        "Discovered single-product strategy: container='%s' name='%s' price='%s'",
        container_sel, name_sel, price_sel,
    )

    return ScrapingStrategy(
        product_container=container_sel,
        name_selector=name_sel,
        price_selector=price_sel,
        image_selector=image_sel,
        currency_hint=currency_hint,
        discovery_method="single_product",
        criteria_selectors=criteria_sels,
    )


async def _discover_by_price_pattern(page: Page) -> ScrapingStrategy | None:
    """JS fallback: find repeating elements containing currency symbols."""
    try:
        result = await page.evaluate("""() => {
            const currencyPattern = /[$₪€£¥]\\s*[\\d,.]+|[\\d,.]+\\s*[$₪€£¥]/;
            const priceElements = [];

            const walker = document.createTreeWalker(
                document.body, NodeFilter.SHOW_TEXT, null
            );

            while (walker.nextNode()) {
                const text = walker.currentNode.textContent.trim();
                if (currencyPattern.test(text)) {
                    const parent = walker.currentNode.parentElement;
                    if (parent) {
                        priceElements.push({
                            tag: parent.tagName.toLowerCase(),
                            className: parent.className || '',
                            parentTag: parent.parentElement?.tagName.toLowerCase() || '',
                            parentClass: parent.parentElement?.className || ''
                        });
                    }
                }
            }

            // Group by parent tag+class to find repeating pattern
            const groups = {};
            for (const el of priceElements) {
                const key = `${el.parentTag}.${el.parentClass}`;
                groups[key] = (groups[key] || 0) + 1;
            }

            // Find the most common repeating group (min 2)
            let bestKey = null;
            let bestCount = 0;
            for (const [key, count] of Object.entries(groups)) {
                if (count >= 2 && count > bestCount) {
                    bestKey = key;
                    bestCount = count;
                }
            }

            if (!bestKey) return null;

            const [tag, cls] = bestKey.split('.');
            return { tag, className: cls, count: bestCount };
        }""")

        if not result:
            return None

        tag = result.get("tag", "div")
        class_name = result.get("className", "")

        if class_name:
            first_class = class_name.split()[0]
            container_selector = f"{tag}.{first_class}"
        else:
            container_selector = tag

        containers = await page.query_selector_all(container_selector)
        if len(containers) < 2:
            return None

        first = containers[0]
        name_sel = await _find_selector(first, NAME_CANDIDATES)
        price_sel = await _find_selector(first, PRICE_CANDIDATES)
        if not price_sel:
            price_sel = "span"

        return ScrapingStrategy(
            product_container=container_selector,
            name_selector=name_sel or "a",
            price_selector=price_sel,
            discovery_method="price_pattern",
        )

    except Exception:
        logger.warning("Price pattern discovery failed")
        return None
