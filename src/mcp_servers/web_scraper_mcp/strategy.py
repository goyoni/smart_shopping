"""Scraping strategy model and adaptive discovery."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

import litellm
from opentelemetry import trace as otel_trace
from playwright.async_api import Page

from src.shared.config import settings
from src.shared.logging import get_logger, get_tracer, operation_span, set_span_token_counts

logger = get_logger(__name__)
_tracer = get_tracer(__name__)

def _get_scraper_llm_model() -> str:
    """Return the LLM model to use for scraper strategy discovery."""
    return settings.scraper_llm_model or settings.llm_model

# CSS selector candidates tried in order for product containers
_CONTAINER_CANDIDATES: list[str] = [
    "[data-product-id]",
    "[data-item-id]",
    ".product-card",
    ".product-item",
    ".product-tile",
    ".product-listing",
    ".product-box",
    ".search-result",
    ".s-result-item",
    ".product",
    "li[class*='product']",
    "div[class*='product']",
    "article[class*='product']",
    "div[class*='Product']",
    # Common e-commerce listing patterns
    "[class*='result-item']",
    "[class*='ResultItem']",
    "[class*='catalog-item']",
    "[class*='CatalogItem']",
    "[class*='grid-item']",
    "[class*='GridItem']",
    "[class*='listing-item']",
    "[class*='card'][class*='product']",
    # Generic — last resort, can match non-product items
    "div[class*='item'][class*='product']",
    # Price-comparison / seller-listing patterns
    "[class*='compare-item']",
    "[class*='compare'][class*='row']",
    "[class*='bid-row']",
    "[class*='bid'][class*='item']",
    "[class*='offer-row']",
    "[class*='offer'][class*='item']",
    "[class*='seller-row']",
    "[class*='seller'][class*='item']",
    "[class*='store-row']",
    "[class*='store'][class*='item']",
    "[data-product-price]",
    "[data-site-name]",
]

_NAME_CANDIDATES: list[str] = [
    "h2 a", "h3 a", "h2", "h3",
    "[class*='title'] a", "[class*='name'] a",
    "[class*='Title'] a", "[class*='Name'] a",
    "[class*='title']", "[class*='name']",
    "[class*='Title']", "[class*='Name']",
    "a[class*='product']",
    "a[class*='Product']",
    "a[class*='Model']",
    # Seller/store name patterns (price-comparison sites)
    "[class*='store-name']", "[class*='StoreName']",
    "[class*='seller-name']", "[class*='SellerName']",
    "[class*='shop-name']", "[class*='ShopName']",
    "[class*='merchant']", "[class*='Merchant']",
]

_PRICE_CANDIDATES: list[str] = [
    "[class*='price']",
    "[class*='Price']",
    "[data-price]",
    "[data-min-price]",
    "span[class*='amount']",
    "[class*='cost']",
    "[class*='Cost']",
]

_IMAGE_CANDIDATES: list[str] = [
    "img[src*='product']", "img[data-src]",
    "img[class*='product']", "img[class*='Product']",
    "img[loading]", "img",
]

_URL_CANDIDATES: list[str] = [
    "a[href*='/product']", "a[href*='/dp/']",
    "a[href*='/item']", "a[href*='/p/']",
    "a[href*='/model']", "a[href*='pid=']",
    "a[href]",
]


@dataclass
class ScrapingStrategy:
    product_container: str
    name_selector: str = ""
    price_selector: str = ""
    image_selector: str = ""
    url_selector: str = ""
    model_selector: str = ""
    brand_selector: str = ""
    mpn_selector: str = ""
    currency_hint: str = ""
    version: int = 1
    discovery_method: str = "css_candidates"
    access_method: str = ""
    extraction_method: str = ""
    # Failure tracking
    block_type: str = ""          # "captcha", "waf", "" (not blocked)
    blocked_at: str = ""          # ISO timestamp when block was detected
    last_failure_type: str = ""   # FailureType.value from last failure
    last_failure_at: str = ""     # ISO timestamp of last failure
    consecutive_failures: int = 0
    validation_failures: int = 0  # Consecutive validation-quality failures
    last_successful_url: str = "" # Probe URL for health checks
    criteria_selectors: dict[str, str] = field(default_factory=dict)
    # Data-attribute extraction: when set, read these attributes from the
    # container element itself instead of using sub-selector + inner_text.
    # E.g. name_attr="data-site-name" reads the seller name from the
    # container's data-site-name attribute.
    name_attr: str = ""
    price_attr: str = ""
    # Navigation: CSS selector for product links on listing/search pages.
    # Used to navigate from a listing page to the matching product page.
    product_link_selector: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> ScrapingStrategy:
        parsed = json.loads(data)
        valid_fields = {k: v for k, v in parsed.items() if k in cls.__dataclass_fields__}
        return cls(**valid_fields)


_SPEC_CONTAINER_CANDIDATES: list[str] = [
    "[class*='spec']",
    "[class*='Spec']",
    "[class*='attribute']",
    "[class*='Attribute']",
    "[class*='feature']",
    "[class*='Feature']",
    "[data-spec]",
    "[data-attribute]",
    "dl",
    "table[class*='spec']",
]


def _criterion_css_candidates(key: str) -> list[str]:
    """Generate CSS probe selectors for a specific criterion key."""
    short = key.split("_")[0]
    return [
        f"[class*='{key}']",
        f"[class*='{short}']",
        f"[data-spec='{key}']",
        f"[data-attribute='{key}']",
    ]


async def _discover_criteria_selectors(
    container: object,
    criteria: dict[str, dict] | None,
) -> dict[str, str]:
    """Probe a product container for per-criterion CSS selectors.

    Returns a dict mapping criterion keys to discovered CSS selectors.
    Only returns selectors that resolve to an element with short,
    non-empty text content.
    """
    if not criteria:
        return {}

    discovered: dict[str, str] = {}

    for key in criteria:
        if key == "price":
            continue

        candidates = _criterion_css_candidates(key)
        for selector in candidates:
            try:
                el = await container.query_selector(selector)
                if el:
                    text = (await el.inner_text()).strip()
                    if text and len(text) < 200:
                        discovered[key] = selector
                        break
            except Exception:
                continue

    return discovered


def _looks_like_price(text: str) -> bool:
    """Check if text looks like a price string."""
    if not text:
        return False
    # Must contain at least one digit
    if not any(c.isdigit() for c in text):
        return False
    # Should contain currency symbol or have numeric format
    currency_symbols = {"$", "₪", "€", "£", "¥"}
    has_currency = any(s in text for s in currency_symbols)
    has_decimal = "." in text or "," in text
    return has_currency or has_decimal or text.strip().replace(",", "").replace(".", "").isdigit()


def _detect_currency(text: str) -> str:
    """Detect currency from text containing price."""
    if "₪" in text or "NIS" in text.upper() or "ILS" in text.upper():
        return "ILS"
    if "€" in text or "EUR" in text.upper():
        return "EUR"
    if "£" in text or "GBP" in text.upper():
        return "GBP"
    if "$" in text or "USD" in text.upper():
        return "USD"
    return ""


async def _find_selector(container, candidates: list[str]) -> str:
    """Try selector candidates against a container, return first that matches."""
    for selector in candidates:
        try:
            el = await container.query_selector(selector)
            if el:
                return selector
        except Exception:
            continue
    return ""


# Data attributes commonly used by price-comparison and e-commerce sites
# to store product/seller data directly on DOM elements.
_DATA_ATTR_NAME_CANDIDATES: list[str] = [
    "data-site-name", "data-store-name", "data-seller-name",
    "data-shop-name", "data-merchant-name", "data-vendor-name",
]

_DATA_ATTR_PRICE_CANDIDATES: list[str] = [
    "data-product-price", "data-price", "data-min-price",
    "data-sale-price", "data-final-price", "data-amount",
]


async def _find_data_attr(container, candidates: list[str]) -> str:
    """Check if a container element has any of the given data attributes.

    Returns the attribute name (e.g. 'data-product-price') or empty string.
    """
    for attr in candidates:
        try:
            value = await container.get_attribute(attr)
            if value and value.strip():
                return attr
        except Exception:
            continue
    return ""


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
    for container_selector in _CONTAINER_CANDIDATES:
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

        # Probe up to 3 containers to find sub-selectors, since the first
        # container may be a non-product element (e.g. sponsored banner).
        name_sel = ""
        price_sel = ""
        image_sel = ""
        url_sel = ""
        name_data_attr = ""
        price_data_attr = ""
        for probe in containers[:3]:
            if not name_sel:
                name_sel = await _find_selector(probe, _NAME_CANDIDATES)
            if not price_sel:
                price_sel = await _find_selector(probe, _PRICE_CANDIDATES)
            if not image_sel:
                image_sel = await _find_selector(probe, _IMAGE_CANDIDATES)
            if not url_sel:
                url_sel = await _find_selector(probe, _URL_CANDIDATES)
            # Also probe for data attributes on the container element itself
            if not name_data_attr:
                name_data_attr = await _find_data_attr(probe, _DATA_ATTR_NAME_CANDIDATES)
            if not price_data_attr:
                price_data_attr = await _find_data_attr(probe, _DATA_ATTR_PRICE_CANDIDATES)

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

    # Fallback 3: LLM-based discovery — analyse the DOM and infer selectors
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

    logger.warning("[strategy] All discovery methods failed (css_candidates → single_product → price_pattern → llm)")
    if span and span.is_recording():
        span.set_attribute("strategy.summary", "ALL FAILED: css_candidates -> single_product -> price_pattern -> llm")
    return None


_SINGLE_PRODUCT_NAME_CANDIDATES: list[str] = [
    "h1[class*='product']", "h1[class*='Product']",
    "h1[class*='title']", "h1[class*='Title']",
    "h1[class*='name']", "h1[class*='Name']",
    "[class*='product-title']", "[class*='product-name']",
    "[class*='productTitle']", "[class*='productName']",
    "[class*='ProductTitle']", "[class*='ProductName']",
    "[data-product-name]",
    "h1",
]

_SINGLE_PRODUCT_PRICE_CANDIDATES: list[str] = [
    "[class*='product-price']", "[class*='productPrice']",
    "[class*='ProductPrice']", "[class*='product_price']",
    "[class*='price'][class*='current']",
    "[class*='sale-price']", "[class*='salePrice']",
    "[data-price]", "[data-product-price]",
    "[class*='price']", "[class*='Price']",
    "span[class*='amount']",
    "[class*='cost']", "[class*='Cost']",
]

# Selectors for the main product content area — used as container for
# single-product pages so the cached strategy can be reused across pages.
_SINGLE_PRODUCT_CONTAINER_CANDIDATES: list[str] = [
    "[class*='product-detail']", "[class*='productDetail']",
    "[class*='ProductDetail']", "[class*='product-info']",
    "[class*='productInfo']", "[class*='ProductInfo']",
    "[class*='product-page']", "[class*='productPage']",
    "[class*='product-summary']",
    "[class*='pdp-']",
    "[itemtype*='schema.org/Product']",
    "main", "article",
]


async def _discover_single_product(
    page: Page,
    criteria: dict[str, dict] | None = None,
) -> ScrapingStrategy | None:
    """Discover strategy for a single-product detail page.

    Unlike the listing-page approach, this does not require ≥2 containers.
    It looks for a product name (typically h1) and price on the page.
    The discovered strategy is cached per domain and reused for other
    product pages on the same site.
    """
    # Find a product name element, skipping elements inside nav/header/footer
    name_sel = ""
    for selector in _SINGLE_PRODUCT_NAME_CANDIDATES:
        try:
            els = await page.query_selector_all(selector)
            for el in els:
                # Reject elements inside nav, header, footer, or sidebar
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
                # Must be reasonable product name (not too short, not too long)
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
    for selector in _SINGLE_PRODUCT_PRICE_CANDIDATES:
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

    # Must find at least a price to be useful for price comparison
    if not price_sel:
        return None

    # Find a container that wraps both name and price
    container_sel = ""
    for selector in _SINGLE_PRODUCT_CONTAINER_CANDIDATES:
        try:
            container = await page.query_selector(selector)
            if container:
                # Verify the container contains both name and price
                has_name = await container.query_selector(name_sel)
                has_price = await container.query_selector(price_sel)
                if has_name and has_price:
                    container_sel = selector
                    break
        except Exception:
            continue

    if not container_sel:
        # Use body as container — less ideal but functional
        container_sel = "body"

    # Find image and URL selectors within the container scope
    probe = await page.query_selector(container_sel)
    image_sel = ""
    if probe:
        image_sel = await _find_selector(probe, _IMAGE_CANDIDATES)
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

        # Verify the selector works
        containers = await page.query_selector_all(container_selector)
        if len(containers) < 2:
            return None

        first = containers[0]
        name_sel = await _find_selector(first, _NAME_CANDIDATES)
        price_sel = await _find_selector(first, _PRICE_CANDIDATES)
        if not price_sel:
            price_sel = "span"  # Fallback: the span containing the price

        return ScrapingStrategy(
            product_container=container_selector,
            name_selector=name_sel or "a",
            price_selector=price_sel,
            discovery_method="price_pattern",
        )

    except Exception:
        logger.warning("Price pattern discovery failed")
        return None


# ===================================================================
# LLM-based strategy discovery
# ===================================================================

_DOM_SNAPSHOT_JS = """() => {
    const EXCLUDED_TAGS = new Set([
        'script', 'style', 'noscript', 'svg', 'path', 'meta', 'link',
        'br', 'hr', 'iframe', 'video', 'audio', 'canvas', 'map',
    ]);
    const EXCLUDED_REGIONS = new Set(['nav', 'header', 'footer']);

    function isVisible(el) {
        if (!el.offsetParent && el.tagName !== 'BODY' && el.tagName !== 'HTML')
            return false;
        const s = getComputedStyle(el);
        return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
    }

    function inExcludedRegion(el) {
        let node = el;
        while (node) {
            const tag = (node.tagName || '').toLowerCase();
            const cls = (node.className || '').toString().toLowerCase();
            if (EXCLUDED_REGIONS.has(tag)) return true;
            if (cls.includes('nav') || cls.includes('menu') ||
                cls.includes('sidebar') || cls.includes('footer') ||
                cls.includes('header') || cls.includes('cookie') ||
                cls.includes('banner') || cls.includes('modal'))
                return true;
            node = node.parentElement;
        }
        return false;
    }

    function describeEl(el, depth) {
        const tag = el.tagName.toLowerCase();
        if (EXCLUDED_TAGS.has(tag)) return null;
        if (depth > 6) return null;
        if (!isVisible(el)) return null;

        const cls = el.className
            ? (typeof el.className === 'string' ? el.className : '').split(/\\s+/).slice(0, 3).join(' ')
            : '';
        const dataAttrs = {};
        for (const attr of el.attributes) {
            if (attr.name.startsWith('data-') && attr.value.length < 60)
                dataAttrs[attr.name] = attr.value.substring(0, 40);
        }
        const text = el.childNodes.length === 1 && el.childNodes[0].nodeType === 3
            ? el.childNodes[0].textContent.trim().substring(0, 40)
            : '';

        const children = [];
        // Detect repeating siblings: if >3 children share the same tag+class, collapse
        const childGroups = {};
        for (const child of el.children) {
            const childTag = child.tagName.toLowerCase();
            if (EXCLUDED_TAGS.has(childTag)) continue;
            const childCls = child.className
                ? (typeof child.className === 'string' ? child.className : '').split(/\\s+/).slice(0, 2).join(' ')
                : '';
            const key = `${childTag}.${childCls}`;
            if (!childGroups[key]) childGroups[key] = [];
            childGroups[key].push(child);
        }

        for (const [key, group] of Object.entries(childGroups)) {
            if (group.length > 3) {
                // Show first 2 expanded, note the rest
                for (const child of group.slice(0, 2)) {
                    const desc = describeEl(child, depth + 1);
                    if (desc) children.push(desc);
                }
                children.push(`... ×${group.length - 2} more <${key}>`);
            } else {
                for (const child of group) {
                    const desc = describeEl(child, depth + 1);
                    if (desc) children.push(desc);
                }
            }
        }

        let repr = `<${tag}`;
        if (cls) repr += ` class="${cls}"`;
        for (const [k, v] of Object.entries(dataAttrs)) repr += ` ${k}="${v}"`;
        repr += '>';
        if (text) repr += text;

        if (children.length === 0 && !text) return null;

        const result = { repr, children: children.filter(Boolean) };
        return result;
    }

    // Find the main content area
    const main = document.querySelector('main, [role="main"], #content, .content, #main')
        || document.body;
    if (inExcludedRegion(main)) return JSON.stringify({ error: 'main is in excluded region' });

    const snapshot = describeEl(main, 0);

    // Flatten to a compact string representation
    function flatten(node, indent) {
        if (typeof node === 'string') return ' '.repeat(indent) + node;
        if (!node) return '';
        let lines = [' '.repeat(indent) + node.repr];
        for (const child of (node.children || [])) {
            const line = flatten(child, indent + 1);
            if (line) lines.push(line);
        }
        return lines.join('\\n');
    }

    const text = flatten(snapshot, 0);
    // Truncate to ~6000 chars to stay within token budget
    return text.substring(0, 6000);
}"""


_LLM_SYSTEM_PROMPT = """\
You are a web scraping expert. Given a simplified DOM snapshot of a product listing or search results page, \
identify the CSS selectors needed to extract product data.

Rules:
- The "container" is the repeating element that wraps each individual product card/row.
- Prefer class-based selectors. Use the most specific class that uniquely identifies product items.
- For compound classes, use the CSS format: tag.class1.class2
- Selectors must be valid CSS. Do not use XPath.
- If you cannot identify a selector, use an empty string.

Return ONLY a valid JSON object (no markdown, no explanation):
{"container": "...", "name": "...", "price": "...", "image": "...", "url": "...", "currency": "..."}

Where:
- container: CSS selector for the repeating product wrapper element
- name: CSS selector (relative to container) for the product name/title
- price: CSS selector (relative to container) for the price
- image: CSS selector (relative to container) for the product image
- url: CSS selector (relative to container) for the link to the product page
- currency: the currency code detected (e.g. "EUR", "USD", "ILS") or empty string"""


async def _build_dom_snapshot(page: Page) -> str | None:
    """Build a compact DOM snapshot of the main content area."""
    try:
        snapshot = await page.evaluate(_DOM_SNAPSHOT_JS)
        if snapshot and len(snapshot) > 100:
            return snapshot
    except Exception:
        logger.debug("DOM snapshot extraction failed")
    return None


async def _discover_via_llm(
    page: Page,
    product_query: str = "",
    criteria: dict[str, dict] | None = None,
) -> ScrapingStrategy | None:
    """Use an LLM to analyse the rendered DOM and infer CSS selectors.

    This is the last-resort fallback when CSS-candidate and price-pattern
    discovery both fail. The result is validated against the live page before
    being accepted.
    """
    model = _get_scraper_llm_model()
    api_key = settings.llm_api_key
    is_local = model.startswith("ollama/")
    if not api_key and not is_local:
        logger.debug("LLM strategy discovery skipped — no API key configured")
        return None

    snapshot = await _build_dom_snapshot(page)
    if not snapshot:
        return None

    logger.info("Attempting LLM strategy discovery (model=%s, snapshot=%d chars)", model, len(snapshot))

    with operation_span(
        _tracer, "strategy_llm_discovery",
        input=product_query,
    ) as span:
        span.set_attribute("llm.model", model)
        span.set_attribute("snapshot_length", len(snapshot))

        user_prompt = f"Product query: {product_query}\n\nDOM snapshot:\n{snapshot}"
        span.set_attribute("llm.system_prompt", _LLM_SYSTEM_PROMPT)
        span.set_attribute("llm.user_prompt", user_prompt[:2000])

        try:
            llm_kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.0,
            }
            if api_key:
                llm_kwargs["api_key"] = api_key
            response = await litellm.acompletion(**llm_kwargs)
            raw = (response.choices[0].message.content or "").strip()
            usage = response.get("usage") or {}
            set_span_token_counts(
                span,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            )
        except Exception as exc:
            logger.warning("LLM strategy discovery call failed", exc_info=True)
            span.set_attribute("summary", f"LLM call failed: {type(exc).__name__}: {str(exc)[:200]}")
            return None

        span.set_attribute("llm.raw_response", raw[:1000])

        # Parse the JSON response
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            selectors = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("LLM returned invalid JSON: %s", raw[:200])
            span.set_attribute("summary", f"LLM returned invalid JSON: {raw[:200]}")
            return None

        container_sel = selectors.get("container", "")
        name_sel = selectors.get("name", "")
        price_sel = selectors.get("price", "")
        image_sel = selectors.get("image", "")
        url_sel = selectors.get("url", "")
        currency_hint = selectors.get("currency", "")

        span.set_attribute("llm.parsed_selectors", json.dumps(selectors))

        if not container_sel:
            logger.warning("LLM returned empty container selector")
            span.set_attribute("summary", "LLM returned empty container selector")
            return None

        # --- Validate against the live page ---
        try:
            containers = await page.query_selector_all(container_sel)
        except Exception:
            logger.warning("LLM container selector '%s' is invalid CSS", container_sel)
            span.set_attribute("summary", f"LLM container '{container_sel}' is invalid CSS")
            return None

        span.set_attribute("container_match_count", len(containers))

        if len(containers) < 2:
            logger.warning(
                "LLM container selector '%s' matched %d elements (need ≥2)",
                container_sel, len(containers),
            )
            span.set_attribute("summary", f"LLM container '{container_sel}' matched only {len(containers)} elements (need >=2)")
            return None

        # Verify at least one container has a name element
        has_name = False
        for probe in containers[:3]:
            try:
                el = await probe.query_selector(name_sel) if name_sel else None
                if el:
                    text = (await el.inner_text()).strip()
                    if text and 2 <= len(text) <= 300:
                        has_name = True
                        break
            except Exception:
                continue

        if not has_name:
            for fallback in ["a", "h2", "h3", "[class*='name']", "[class*='title']"]:
                for probe in containers[:3]:
                    try:
                        el = await probe.query_selector(fallback)
                        if el:
                            text = (await el.inner_text()).strip()
                            if text and 2 <= len(text) <= 300:
                                name_sel = fallback
                                has_name = True
                                break
                    except Exception:
                        continue
                if has_name:
                    break

        if not has_name:
            logger.warning("LLM strategy: no valid name element found in containers")
            span.set_attribute("summary", f"LLM container '{container_sel}' has {len(containers)} elements but no valid name text found")
            return None

        # Detect currency from first price element if not provided by LLM
        if not currency_hint and price_sel:
            for probe in containers[:3]:
                try:
                    price_el = await probe.query_selector(price_sel)
                    if price_el:
                        price_text = await price_el.inner_text()
                        currency_hint = _detect_currency(price_text)
                        if currency_hint:
                            break
                except Exception:
                    continue

        criteria_sels = await _discover_criteria_selectors(containers[0], criteria)

        logger.info(
            "LLM discovered strategy: container='%s' name='%s' price='%s' (%d containers)",
            container_sel, name_sel, price_sel, len(containers),
        )
        span.set_attribute("summary",
            f"LLM SUCCESS: container='{container_sel}' ({len(containers)} elements), name='{name_sel}', price='{price_sel}', currency={currency_hint}")

        return ScrapingStrategy(
            product_container=container_sel,
            name_selector=name_sel,
            price_selector=price_sel,
            image_selector=image_sel,
            url_selector=url_sel,
            currency_hint=currency_hint,
            discovery_method="llm",
            criteria_selectors=criteria_sels,
        )


# ===================================================================
# Listing → product page navigation
# ===================================================================

# CSS selectors tried in order to find product links on listing pages
_PRODUCT_LINK_CANDIDATES: list[str] = [
    "a[href*='/product/']",
    "a[href*='/dp/']",
    "a[href*='/item/']",
    "a[href*='/p/']",
    "a[href*='/model/']",
    "a[href*='pid=']",
    "a[href*='productid=']",
    ".product-card a",
    ".product-item a",
    "[class*='product'] a[href]",
    "[class*='Product'] a[href]",
    "[data-product-id] a[href]",
    "h2 a[href]",
    "h3 a[href]",
    "[class*='title'] a[href]",
    "[class*='name'] a[href]",
]


async def find_product_url(
    page: Page,
    product_query: str,
    base_url: str,
    cached_selector: str = "",
) -> str | None:
    """Find a product link on a listing/search page that matches the query.

    Tries cached selector first, then CSS candidates, then LLM fallback.
    Returns the URL of the best matching product, or None.
    """
    from urllib.parse import urljoin

    span = otel_trace.get_current_span()
    query_lower = product_query.lower()
    query_tokens = [t for t in query_lower.split() if len(t) >= 3]

    # Try cached selector first
    if cached_selector:
        url = await _match_product_link(page, cached_selector, query_lower, query_tokens, base_url)
        if url:
            logger.info("[nav] found product via cached selector '%s': %s", cached_selector, url[:100])
            if span and span.is_recording():
                span.add_event("nav.found", {"method": "cached_selector", "selector": cached_selector, "url": url[:200]})
            return url

    # Try CSS candidates
    for selector in _PRODUCT_LINK_CANDIDATES:
        url = await _match_product_link(page, selector, query_lower, query_tokens, base_url)
        if url:
            logger.info("[nav] found product via css candidate '%s': %s", selector, url[:100])
            if span and span.is_recording():
                span.add_event("nav.found", {"method": "css_candidate", "selector": selector, "url": url[:200]})
            return url

    # LLM fallback
    url, selector = await _find_product_url_via_llm(page, product_query, base_url)
    if url:
        logger.info("[nav] found product via LLM (selector='%s'): %s", selector, url[:100])
        if span and span.is_recording():
            span.add_event("nav.found", {"method": "llm", "selector": selector, "url": url[:200]})
        return url

    logger.warning("[nav] could not find product link for '%s' on %s", product_query, base_url[:80])
    if span and span.is_recording():
        span.add_event("nav.not_found", {"query": product_query, "base_url": base_url[:200]})
    return None


async def _match_product_link(
    page: Page,
    selector: str,
    query_lower: str,
    query_tokens: list[str],
    base_url: str,
) -> str | None:
    """Try to find a link matching the query using the given selector.

    Returns the href of the best matching link, or None.
    """
    from urllib.parse import urljoin

    try:
        links = await page.query_selector_all(selector)
    except Exception:
        return None

    if not links:
        return None

    best_url: str | None = None
    best_score = 0

    for link in links[:30]:  # Don't scan too many
        try:
            href = await link.get_attribute("href")
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue

            text = (await link.inner_text()).strip().lower()
            if not text:
                # Try parent text for links inside product cards
                try:
                    parent = await link.evaluate_handle("el => el.closest('[class*=\"product\"], [class*=\"Product\"], li, article')")
                    if parent:
                        text = (await parent.inner_text() if hasattr(parent, 'inner_text') else "").strip().lower()
                except Exception:
                    pass
            if not text:
                continue

            # Score: how well does this link match the query?
            score = 0
            if query_lower in text:
                score += 10  # Full query match in text
            if query_lower in href.lower():
                score += 5  # Query in URL
            for tok in query_tokens:
                if tok in text:
                    score += 2
                if tok in href.lower():
                    score += 1

            if score > best_score:
                best_score = score
                full_url = urljoin(base_url, href)
                best_url = full_url
        except Exception:
            continue

    if best_score >= 2:
        return best_url
    return None


_LLM_NAV_SYSTEM = """\
You are a web scraping expert. Given a DOM snapshot of a search/listing page, \
identify the CSS selector for product links that lead to individual product pages.

Return ONLY a valid JSON object (no markdown, no explanation):
{"product_link_selector": "CSS selector for product links", "best_match_text": "visible text of the link matching the query"}

Rules:
- The selector must target <a> elements with href attributes.
- Prefer selectors that target product title/name links.
- If you cannot identify product links, return {"product_link_selector": "", "best_match_text": ""}"""


async def _find_product_url_via_llm(
    page: Page,
    product_query: str,
    base_url: str,
) -> tuple[str | None, str]:
    """Use LLM to find a product link on a listing page.

    Returns (url, discovered_selector) or (None, "").
    """
    from urllib.parse import urljoin

    model = _get_scraper_llm_model()
    api_key = settings.llm_api_key
    is_local = model.startswith("ollama/")
    if not api_key and not is_local:
        return None, ""

    snapshot = await _build_dom_snapshot(page)
    if not snapshot:
        return None, ""

    logger.info("[nav] LLM navigation discovery for '%s' (model=%s)", product_query, model)

    with operation_span(
        _tracer, "nav_llm_discovery",
        input=product_query,
    ) as span:
        span.set_attribute("llm.model", model)
        span.set_attribute("base_url", base_url[:200])

        user_prompt = (
            f"Search query: {product_query}\n"
            f"Page URL: {base_url}\n\n"
            f"DOM snapshot:\n{snapshot}"
        )
        span.set_attribute("llm.system_prompt", _LLM_NAV_SYSTEM)
        span.set_attribute("llm.user_prompt", user_prompt[:2000])

        try:
            llm_kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": _LLM_NAV_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.0,
            }
            if api_key:
                llm_kwargs["api_key"] = api_key
            response = await litellm.acompletion(**llm_kwargs)
            raw = (response.choices[0].message.content or "").strip()
            usage = response.get("usage") or {}
            set_span_token_counts(
                span,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            )
        except Exception as exc:
            logger.warning("[nav] LLM navigation call failed", exc_info=True)
            span.set_attribute("summary", f"LLM call failed: {type(exc).__name__}")
            return None, ""

        span.set_attribute("llm.raw_response", raw[:500])

        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[nav] LLM returned invalid JSON: %s", raw[:200])
            span.set_attribute("summary", f"LLM returned invalid JSON: {raw[:200]}")
            return None, ""

        selector = (data.get("product_link_selector") or "").strip()
        if not selector:
            span.set_attribute("summary", "LLM returned empty selector")
            return None, ""

        span.set_attribute("discovered_selector", selector)

        # Use the discovered selector to find the matching link
        query_lower = product_query.lower()
        query_tokens = [t for t in query_lower.split() if len(t) >= 3]
        url = await _match_product_link(page, selector, query_lower, query_tokens, base_url)
        if url:
            span.set_attribute("summary", f"Found product URL via LLM selector '{selector}': {url[:150]}")
        else:
            span.set_attribute("summary", f"LLM selector '{selector}' found no matching product link")
        return url, selector
