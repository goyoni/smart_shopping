"""Google search via Playwright for product discovery."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote_plus

from playwright.async_api import Browser, Page

from src.shared.browser import get_page

logger = logging.getLogger(__name__)

_GOOGLE_DOMAINS: dict[str, str] = {
    "il": "google.co.il",
    "uk": "google.co.uk",
    "de": "google.de",
    "fr": "google.fr",
    "us": "google.com",
}

_LANG_CODES: dict[str, str] = {
    "he": "iw",  # Google uses 'iw' for Hebrew
    "en": "en",
    "ar": "ar",
    "de": "de",
    "fr": "fr",
}

_BUY_ONLINE_SUFFIXES: dict[str, str] = {
    "he": "קנייה אונליין",
    "ar": "شراء عبر الإنترنت",
    "en": "buy online",
}


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str


def build_search_url(query: str, language: str = "en", market: str = "us") -> str:
    """Build Google search URL with market-specific domain and language params.

    Augments query with 'buy online' in the appropriate language to bias
    toward shopping results.
    """
    domain = _GOOGLE_DOMAINS.get(market, "google.com")
    hl = _LANG_CODES.get(language, language)
    suffix = _BUY_ONLINE_SUFFIXES.get(language, "buy online")
    augmented_query = f"{query} {suffix}"
    encoded = quote_plus(augmented_query)
    return f"https://www.{domain}/search?q={encoded}&hl={hl}"


async def extract_search_results(page: Page) -> list[SearchResult]:
    """Extract URLs, titles, and snippets from a loaded Google results page."""
    results: list[SearchResult] = []

    # Use generic selectors for Google search result links
    links = await page.query_selector_all("div#search a[href^='http']")

    for link in links:
        href = await link.get_attribute("href")
        if not href:
            continue

        # Skip Google internal links
        if "google." in href and "/search?" in href:
            continue

        # Try to find title within or near the link
        title_el = await link.query_selector("h3")
        title = await title_el.inner_text() if title_el else ""

        # Skip links without titles (usually not main results)
        if not title:
            continue

        # Try to find snippet from parent container
        snippet = ""
        parent = await link.evaluate_handle("el => el.closest('div[data-sokoban-container]') || el.parentElement?.parentElement")
        if parent:
            try:
                snippet_el = await parent.as_element().query_selector("div[data-sncf], span[style*='-webkit-line-clamp']")
                if snippet_el:
                    snippet = await snippet_el.inner_text()
            except Exception:
                pass

        results.append(SearchResult(url=href, title=title, snippet=snippet))

    return results


def _is_captcha_page(page_content: str) -> bool:
    """Check if the page content indicates a CAPTCHA challenge."""
    captcha_signals = [
        "unusual traffic",
        "not a robot",
        "captcha",
        "recaptcha",
        "/sorry/",
    ]
    content_lower = page_content.lower()
    return any(signal in content_lower for signal in captcha_signals)


async def search_products(
    browser: Browser,
    query: str,
    language: str = "en",
    market: str = "us",
) -> list[SearchResult]:
    """Search Google for products and return results.

    Orchestrates: navigate → wait for results → check for CAPTCHA → extract.
    Returns empty list on CAPTCHA or timeout.
    """
    url = build_search_url(query, language, market)
    locale_map = {"he": "he-IL", "ar": "ar-SA", "en": "en-US"}
    locale = locale_map.get(language, "en-US")

    async with get_page(browser, locale=locale) as page:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            logger.warning("Timeout navigating to Google search for '%s'", query)
            return []

        # Wait for search results to appear
        try:
            await page.wait_for_selector("div#search", timeout=10000)
        except Exception:
            logger.warning("Search results did not load for '%s'", query)
            return []

        # Check for CAPTCHA
        content = await page.content()
        if _is_captcha_page(content):
            logger.warning("CAPTCHA detected for query '%s'", query)
            return []

        results = await extract_search_results(page)
        logger.info("Found %d search results for '%s'", len(results), query)
        return results
