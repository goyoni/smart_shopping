"""Scraping strategy model and adaptive discovery."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from playwright.async_api import Page

from src.shared.logging import get_logger

logger = get_logger(__name__)

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
    "div[class*='item']",
    "div[class*='Item']",
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

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> ScrapingStrategy:
        parsed = json.loads(data)
        valid_fields = {k: v for k, v in parsed.items() if k in cls.__dataclass_fields__}
        return cls(**valid_fields)


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


async def discover_strategy(page: Page, product_query: str = "") -> ScrapingStrategy | None:
    """Discover scraping strategy by trying CSS selector candidates.

    Requires at least 2 matching containers to consider a strategy valid.
    Falls back to price-pattern discovery if CSS candidates fail.
    """
    # Try each container candidate
    for container_selector in _CONTAINER_CANDIDATES:
        try:
            containers = await page.query_selector_all(container_selector)
        except Exception:
            continue

        if len(containers) < 2:
            continue

        logger.info(
            "Found %d containers with selector '%s'",
            len(containers), container_selector,
        )

        # Use first container to discover sub-selectors
        first = containers[0]
        name_sel = await _find_selector(first, _NAME_CANDIDATES)
        price_sel = await _find_selector(first, _PRICE_CANDIDATES)
        image_sel = await _find_selector(first, _IMAGE_CANDIDATES)
        url_sel = await _find_selector(first, _URL_CANDIDATES)

        # Must find at least name selector
        if not name_sel:
            continue

        # Detect currency from price if available
        currency_hint = ""
        if price_sel:
            try:
                price_el = await first.query_selector(price_sel)
                if price_el:
                    price_text = await price_el.inner_text()
                    currency_hint = _detect_currency(price_text)
            except Exception:
                pass

        return ScrapingStrategy(
            product_container=container_selector,
            name_selector=name_sel,
            price_selector=price_sel,
            image_selector=image_sel,
            url_selector=url_sel,
            currency_hint=currency_hint,
            discovery_method="css_candidates",
        )

    # Fallback: price-pattern based discovery
    strategy = await _discover_by_price_pattern(page)
    if strategy:
        return strategy

    logger.warning("Could not discover scraping strategy for page")
    return None


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
