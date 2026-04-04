"""Shared helper functions for strategy discovery."""

from __future__ import annotations


def _looks_like_price(text: str) -> bool:
    """Check if text looks like a price string."""
    if not text:
        return False
    if not any(c.isdigit() for c in text):
        return False
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
    """Probe a product container for per-criterion CSS selectors."""
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
