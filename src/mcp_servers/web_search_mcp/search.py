"""Web search via DuckDuckGo HTML for product discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote_plus, unquote

import httpx
from opentelemetry import trace

from src.shared.logging import get_logger, get_tracer

logger = get_logger(__name__)
_tracer = get_tracer(__name__)

_SEARCH_URL = "https://html.duckduckgo.com/html/"

_BUY_ONLINE_SUFFIXES: dict[str, str] = {
    "he": "קנייה אונליין",
    "ar": "شراء عبر الإنترنت",
    "en": "buy online",
}

_REGION_CODES: dict[str, str] = {
    "il": "il-he",
    "uk": "uk-en",
    "de": "de-de",
    "fr": "fr-fr",
    "us": "us-en",
}

_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

_REQUEST_TIMEOUT = 20.0


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str


def build_search_url(query: str, language: str = "en", market: str = "us") -> str:
    """Build the augmented search query URL.

    Augments query with 'buy online' in the appropriate language to bias
    toward shopping results.  Returns the DuckDuckGo HTML endpoint URL.
    """
    suffix = _BUY_ONLINE_SUFFIXES.get(language, "buy online")
    augmented_query = f"{query} {suffix}"
    encoded = quote_plus(augmented_query)
    kl = _REGION_CODES.get(market, "us-en")
    return f"{_SEARCH_URL}?q={encoded}&kl={kl}"


def _extract_ddg_url(raw_url: str) -> str:
    """Extract the actual destination URL from a DuckDuckGo redirect link.

    DDG wraps results in ``//duckduckgo.com/l/?uddg=<encoded_url>&…``.
    """
    if "uddg=" in raw_url:
        match = re.search(r"uddg=([^&]+)", raw_url)
        if match:
            return unquote(match.group(1))
    # Direct URL (no redirect wrapper)
    if raw_url.startswith("//"):
        return "https:" + raw_url
    return raw_url


def extract_search_results(html: str) -> list[SearchResult]:
    """Extract URLs, titles, and snippets from DuckDuckGo HTML search results."""
    results: list[SearchResult] = []
    seen_urls: set[str] = set()

    # DuckDuckGo uses <a class="result__a" href="...">TITLE</a>
    link_pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )

    # Snippets are in <a class="result__snippet" ...>TEXT</a>
    snippet_pattern = re.compile(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )

    link_matches = link_pattern.findall(html)
    snippet_matches = snippet_pattern.findall(html)

    for i, (raw_url, raw_title) in enumerate(link_matches):
        url = _extract_ddg_url(raw_url)
        title = re.sub(r"<[^>]+>", "", raw_title).strip()
        if not title or not url:
            continue

        # Skip DuckDuckGo-internal links
        if "duckduckgo.com" in url and "/l/?" not in raw_url:
            continue

        if url in seen_urls:
            continue
        seen_urls.add(url)

        snippet = ""
        if i < len(snippet_matches):
            snippet = re.sub(r"<[^>]+>", "", snippet_matches[i]).strip()

        results.append(SearchResult(url=url, title=title, snippet=snippet))

    return results


async def search_products(
    query: str,
    language: str = "en",
    market: str = "us",
    *,
    _max_attempts: int = 2,
) -> list[SearchResult]:
    """Search for products and return results.

    Uses DuckDuckGo's HTML endpoint which returns server-rendered HTML
    (no JavaScript required).  Retries once on transient failures.
    Returns empty list on HTTP errors or network issues.
    """
    url = build_search_url(query, language, market)
    span = trace.get_current_span()
    span.set_attribute("search_url", url)

    logger.info("Starting search for '%s' (language=%s, market=%s)", query, language, market)
    span.add_event("search_started", {"query": query, "language": language, "market": market, "url": url})

    for attempt in range(1, _max_attempts + 1):
        try:
            async with httpx.AsyncClient(
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
                headers=_REQUEST_HEADERS,
            ) as client:
                response = await client.get(url)
        except Exception:
            logger.warning(
                "HTTP request failed for '%s' (attempt %d/%d)",
                query, attempt, _max_attempts,
            )
            if attempt == _max_attempts:
                span.set_attribute("exit_reason", "request_failed")
                span.add_event("search_completed", {"exit_reason": "request_failed"})
                return []
            continue

        span.set_attribute("http_status", response.status_code)

        if response.status_code != 200:
            logger.warning(
                "Search returned HTTP %d for '%s' (attempt %d/%d)",
                response.status_code, query, attempt, _max_attempts,
            )
            if attempt == _max_attempts:
                span.set_attribute("exit_reason", f"http_{response.status_code}")
                span.add_event("search_completed", {"exit_reason": f"http_{response.status_code}"})
                return []
            continue

        html = response.text
        results = extract_search_results(html)
        if not results:
            span.set_attribute("exit_reason", "no_results_extracted")
            span.add_event("search_completed", {"result_count": 0, "exit_reason": "no_results_extracted"})
            logger.warning("No results extracted from HTML for '%s'", query)
            logger.warning("Response content (first 500 chars): %s", html[:500])
        else:
            span.add_event("search_completed", {"result_count": len(results)})
            logger.info("Found %d search results for '%s'", len(results), query)
        return results

    return []
