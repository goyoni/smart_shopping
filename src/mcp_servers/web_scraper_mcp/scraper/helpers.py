"""Constants, utility functions, and pipeline event logging."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from opentelemetry import trace as otel_trace

from src.shared.logging import get_logger

logger = get_logger(__name__)

_MAX_PRODUCTS_PER_SITE = 50
_MAX_PAGES = 3
_MAX_SANE_PRICE = 1_000_000

# CSS selectors tried in order to find a "next page" link
_NEXT_PAGE_CANDIDATES: list[str] = [
    "a[aria-label*='next' i]",
    "a[rel='next']",
    "a.next",
    "a.pagination-next",
    "li.next > a",
    "a[class*='next']",
    "a[class*='Next']",
    "button[aria-label*='next' i]",
    "nav[aria-label*='pagination'] a:last-child",
    "[class*='pagination'] a:last-child",
]

# Tracking/analytics domains to ignore during response interception
_IGNORE_DOMAINS = {
    "google-analytics.com", "googletagmanager.com", "facebook.com",
    "doubleclick.net", "analytics.", "hotjar.com", "clarity.ms",
    "sentry.io", "newrelic.com", "segment.com", "mixpanel.com",
}

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "he,en;q=0.9",
}

_HTTP_TIMEOUT = 15.0

_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "[::1]"}
_BLOCKED_PREFIXES = ("10.", "172.16.", "172.17.", "172.18.", "172.19.",
                     "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
                     "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
                     "172.30.", "172.31.", "192.168.", "169.254.", "fe80::",
                     "fc00::", "fd00::")


def _pipeline_event(domain: str, step: str, detail: str, **attrs: object) -> None:
    """Log a pipeline step and record it as a span event on the active OTEL span."""
    msg = f"[{step}] {domain} — {detail}"
    logger.info(msg)
    span = otel_trace.get_current_span()
    if span and span.is_recording():
        span.add_event(f"pipeline.{step}", {"domain": domain, "detail": detail, **attrs})


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
    cleaned = re.sub(r"[^\d.,]", "", text.strip())
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rindex(",") > cleaned.rindex("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts[-1]) == 2:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value > _MAX_SANE_PRICE:
        return None
    return value


def extract_specs_from_text(
    text: str,
    criteria: dict[str, dict] | None = None,
) -> dict[str, str]:
    """Extract product specification values from free text using dynamic regex."""
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


def _detect_page_type(url: str) -> str:
    """Classify a URL into a page type for strategy caching."""
    parsed = urlparse(url)
    path = parsed.path.lower()
    query = parsed.query.lower()

    if any(x in path for x in ("/product/", "/item/", "/model", "/dp/", "/p/")):
        return "product"
    if any(x in query for x in ("productid=", "modelid=", "pid=", "itemid=")):
        return "product"
    if any(x in path for x in ("/search", "/find", "/results")):
        return "search"
    if any(x in query for x in ("q=", "query=", "search=", "keyword=")):
        return "search"
    if any(x in path for x in ("/catalog", "/category", "/cat/")):
        return "catalog"
    return "page"


async def _find_next_page_url(page: object, base_url: str) -> str | None:
    """Detect a 'next page' link on the current page."""
    for selector in _NEXT_PAGE_CANDIDATES:
        try:
            el = await page.query_selector(selector)
            if el:
                href = await el.get_attribute("href")
                if href:
                    return urljoin(base_url, href)
        except Exception:
            continue
    return None


def _is_safe_url(url: str) -> bool:
    """Reject URLs targeting private/internal networks (SSRF protection)."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host or parsed.scheme not in ("http", "https"):
        return False
    if host in _BLOCKED_HOSTS:
        return False
    if any(host.startswith(p) for p in _BLOCKED_PREFIXES):
        return False
    return True
