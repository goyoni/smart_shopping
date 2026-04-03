"""Core scraping orchestration with adaptive strategy pipeline.

The scrape_page() function is the main entry point.  It builds a pipeline
of (access_method, extraction_methods) attempts, executes them in order,
classifies failures, and escalates to more expensive methods when needed.
"""

from __future__ import annotations

import json as _json
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession as CurlSession
from opentelemetry import trace as otel_trace
from playwright.async_api import Browser, Response

from src.mcp_servers.web_scraper_mcp.db_cache import (
    get_cached_strategy,
    is_domain_blocked,
    mark_validation_failure,
    save_strategy,
    update_failure,
    update_success_rate,
)
from src.mcp_servers.web_scraper_mcp.diagnostics import (
    ExtractionResult,
    FailureType,
    classify_http_failure,
    classify_playwright_failure,
)
from src.mcp_servers.web_scraper_mcp.extractors import (
    extract_all_from_page,
    extract_all_from_soup,
    extract_from_api_responses,
    extract_with_strategy,
    validate_results,
)
from src.mcp_servers.web_scraper_mcp.strategy import ScrapingStrategy, discover_strategy
from src.shared.browser import get_page
from src.shared.logging import get_logger
from src.shared.market_config import get_default_currency_for_domain, get_garbage_names
from src.shared.models import ProductResult, Seller

logger = get_logger(__name__)


def _pipeline_event(domain: str, step: str, detail: str, **attrs: object) -> None:
    """Log a pipeline step and record it as a span event on the active OTEL span."""
    msg = f"[{step}] {domain} — {detail}"
    logger.info(msg)
    span = otel_trace.get_current_span()
    if span and span.is_recording():
        span.add_event(f"pipeline.{step}", {"domain": domain, "detail": detail, **attrs})


_MAX_PRODUCTS_PER_SITE = 50
_MAX_PAGES = 3

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

# ---------------------------------------------------------------------------
# HTTP configuration
# ---------------------------------------------------------------------------

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

_MAX_SANE_PRICE = 1_000_000


# ===================================================================
# Public API
# ===================================================================


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


# ===================================================================
# Pipeline orchestrator
# ===================================================================


_ACCESS_METHODS = ("httpx", "curl_cffi", "playwright")


def _build_pipeline(
    cached: ScrapingStrategy | None,
) -> list[tuple[str, ...]]:
    """Build an ordered pipeline of access methods to try.

    If we have a cached strategy with a known access_method, put that
    first.  Always include all methods as fallbacks.
    """
    if cached and cached.access_method:
        preferred = cached.access_method
        others = [m for m in _ACCESS_METHODS if m != preferred]
        return [(preferred,)] + [(m,) for m in others]

    return [(m,) for m in _ACCESS_METHODS]


async def scrape_page(
    browser: Browser,
    url: str,
    product_query: str = "",
    *,
    locale: str = "en-US",
    criteria: dict[str, dict] | None = None,
) -> list[ProductResult]:
    """Scrape a product page using an adaptive pipeline.

    Pipeline:
    1. Build attempt order from cache (or default: httpx → curl_cffi → playwright)
    2. For each access method:
       a. Fetch page content
       b. Run all extraction methods
       c. Validate results
       d. On success: cache winning strategy, return
       e. On failure: classify, escalate to next method
    3. Return best results or empty list
    """
    domain = extract_domain(url)
    page_type = _detect_page_type(url)

    if not _is_safe_url(url):
        return []

    # Check if domain is currently blocked (CAPTCHA/WAF with active TTL)
    if await is_domain_blocked(domain, page_type):
        logger.info("Skipping blocked domain %s/%s", domain, page_type)
        return []

    # Load cached strategy
    cached = await get_cached_strategy(domain, page_type)
    pipeline = _build_pipeline(cached)

    last_failure: FailureType | None = None

    order = [m for (m,) in pipeline]
    cached_method = cached.discovery_method if cached else None
    _pipeline_event(domain, "pipeline", f"order={order} cached={cached_method}", page_type=page_type)

    for (access_method,) in pipeline:
        _pipeline_event(domain, access_method, "attempting")

        if access_method in ("httpx", "curl_cffi"):
            result = await _attempt_http(
                url, product_query, domain, page_type,
                access_method, cached,
            )
        else:
            result = await _attempt_playwright(
                browser, url, product_query, domain, page_type,
                locale, criteria, cached,
            )

        if result.success:
            validated = validate_results(result.products, product_query, domain)
            if validated:
                await _cache_success(domain, page_type, result, url)
                return _post_process(validated, product_query, domain)
            else:
                last_failure = FailureType.LOW_QUALITY
                await mark_validation_failure(domain, page_type)
                _pipeline_event(domain, access_method, f"validation failed via {result.extraction_method}")
                continue

        last_failure = result.failure_type
        _pipeline_event(
            domain, access_method,
            f"failed: {last_failure.value if last_failure else 'unknown'} ({result.failure_detail})",
        )

        # Don't waste time on methods that can't help
        if last_failure == FailureType.CLOUDFLARE_CAPTCHA:
            logger.warning("Cloudflare CAPTCHA on %s — cannot bypass, aborting", domain)
            break
        if last_failure == FailureType.NAVIGATION_FAILED and access_method == "playwright":
            break  # If even the browser can't connect, nothing will

    # Record the failure for domain tracking
    if last_failure:
        await update_failure(domain, last_failure.value, page_type)
        logger.warning(
            "All access methods failed for %s (last: %s)",
            domain, last_failure.value,
        )
    return []


# ===================================================================
# HTTP access attempts (httpx / curl_cffi)
# ===================================================================


async def _attempt_http(
    url: str,
    product_query: str,
    domain: str,
    page_type: str,
    access_method: str,
    cached: ScrapingStrategy | None,
) -> ExtractionResult:
    """Try to fetch and extract products via HTTP (no browser)."""
    html: str | None = None
    status_code: int | None = None
    error: Exception | None = None

    if access_method == "httpx":
        try:
            async with httpx.AsyncClient(
                headers=_HTTP_HEADERS,
                follow_redirects=True,
                timeout=_HTTP_TIMEOUT,
            ) as client:
                resp = await client.get(url)
                status_code = resp.status_code
                if status_code == 200:
                    html = resp.text
        except Exception as exc:
            error = exc

    elif access_method == "curl_cffi":
        try:
            async with CurlSession() as session:
                resp_cf = await session.get(
                    url,
                    impersonate="chrome120",
                    headers=_HTTP_HEADERS,
                    timeout=_HTTP_TIMEOUT,
                    allow_redirects=True,
                )
                status_code = resp_cf.status_code
                if status_code == 200:
                    html = resp_cf.text
        except Exception as exc:
            error = exc

    # Classify failure
    if html is None or len(html) < 1000:
        failure = classify_http_failure(status_code, html or "", error)
        err_name = type(error).__name__ if error else None
        _pipeline_event(
            domain, access_method,
            f"HTTP failed: status={status_code} body={len(html or '')} err={err_name} → {failure.value if failure else 'unknown'}",
            status_code=status_code or 0,
        )
        return ExtractionResult(
            access_method=access_method,
            failure_type=failure,
            failure_detail=f"status={status_code}" if status_code else str(error),
            domain=domain,
            page_type=page_type,
        )

    # Run all extraction methods on the HTML
    _pipeline_event(domain, access_method, f"HTTP 200, body={len(html)} chars, extracting...", body_length=len(html))
    soup = BeautifulSoup(html, "lxml")
    extraction_results = extract_all_from_soup(soup, url, domain, product_query)

    if extraction_results:
        method_name, products = extraction_results[0]
        _pipeline_event(domain, access_method, f"extracted {len(products)} products via {method_name}", product_count=len(products))
        return ExtractionResult(
            products=products,
            access_method=access_method,
            extraction_method=method_name,
            domain=domain,
            page_type=page_type,
        )

    # HTML was fetched but no products extracted — likely a JS SPA
    _pipeline_event(domain, access_method, "200 OK but 0 products in static HTML (JS SPA?)")
    return ExtractionResult(
        access_method=access_method,
        failure_type=FailureType.JS_SPA_NO_DATA,
        failure_detail="200 OK but no extractable products in static HTML",
        domain=domain,
        page_type=page_type,
    )


# ===================================================================
# Playwright access attempt
# ===================================================================


async def _attempt_playwright(
    browser: Browser,
    url: str,
    product_query: str,
    domain: str,
    page_type: str,
    locale: str,
    criteria: dict[str, dict] | None,
    cached: ScrapingStrategy | None,
) -> ExtractionResult:
    """Try to fetch and extract products via Playwright browser."""
    async with get_page(browser, locale=locale) as page:
        # Set up API response interception
        captured_responses: list[dict] = []

        async def _on_response(response: Response) -> None:
            try:
                resp_url = response.url
                if any(d in resp_url for d in _IGNORE_DOMAINS):
                    return
                ct = response.headers.get("content-type", "")
                if response.status != 200:
                    return
                is_json_ct = "json" in ct
                is_api_url = any(s in resp_url for s in ("/api/", "/ajax/", "/graphql", "format=json", ".json"))
                if not is_json_ct and not is_api_url:
                    return
                cl = response.headers.get("content-length", "")
                if cl and int(cl) > 500_000:
                    return
                body = await response.body()
                if len(body) > 500_000:
                    return
                data = _json.loads(body)
                captured_responses.append({"url": resp_url, "data": data})
            except Exception:
                pass

        page.on("response", _on_response)

        # Navigate
        _pipeline_event(domain, "playwright", f"navigating to {url[:120]}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception as exc:
            _pipeline_event(domain, "playwright", f"navigation failed: {str(exc)[:200]}")
            return ExtractionResult(
                access_method="playwright",
                failure_type=FailureType.NAVIGATION_FAILED,
                failure_detail=str(exc),
                domain=domain,
                page_type=page_type,
            )

        # Detect and handle Cloudflare challenges
        try:
            title = await page.title()
            title_lower = title.lower()
            if "just a moment" in title_lower:
                _pipeline_event(domain, "playwright", "JS challenge detected, waiting for resolution")
                try:
                    await page.wait_for_function(
                        "document.title.toLowerCase().indexOf('just a moment') === -1",
                        timeout=15000,
                    )
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    return ExtractionResult(
                        access_method="playwright",
                        failure_type=FailureType.CLOUDFLARE_JS,
                        failure_detail="JS challenge did not resolve within timeout",
                        domain=domain,
                        page_type=page_type,
                    )
            elif "attention required" in title_lower:
                return ExtractionResult(
                    access_method="playwright",
                    failure_type=FailureType.CLOUDFLARE_CAPTCHA,
                    failure_detail="Cloudflare CAPTCHA block",
                    domain=domain,
                    page_type=page_type,
                )
        except Exception:
            pass

        # Wait for content
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        try:
            await page.wait_for_selector(
                "[class*='price'], [class*='Price'], [data-price], "
                "[class*='product'], [class*='Product']",
                timeout=5000,
            )
        except Exception:
            pass

        # --- Extraction pipeline ---
        _pipeline_event(domain, "playwright", "page loaded, starting extraction")
        products: list[ProductResult] = []
        winning_method = ""

        # Try cached strategy first (fast path)
        if cached and cached.extraction_method in ("css_strategy", "api_intercept", ""):
            container_preview = cached.product_container[:60] if cached.product_container else ""
            _pipeline_event(domain, "playwright", f"trying cached strategy: method={cached.discovery_method} container='{container_preview}'")
            if cached.discovery_method == "api_intercept":
                products = extract_from_api_responses(
                    captured_responses, url, domain, product_query,
                )
                if products:
                    winning_method = "api_intercept"
            elif cached.product_container:
                products = await extract_with_strategy(page, cached, url, criteria=criteria)
                if products:
                    winning_method = "css_strategy"

            if products:
                _pipeline_event(domain, "playwright", f"cached strategy hit: {len(products)} products via {winning_method}", product_count=len(products))
                await update_success_rate(domain, success=True, page_type=page_type)
            else:
                _pipeline_event(domain, "playwright", "cached strategy miss, trying all methods")
                await update_success_rate(domain, success=False, page_type=page_type)
        else:
            _pipeline_event(domain, "playwright", "no usable cached strategy, trying all methods")

        # If cached strategy didn't work, try all extraction methods
        if not products:
            all_results = await extract_all_from_page(
                page, url, domain, product_query,
                captured_responses, criteria,
            )
            if all_results:
                winning_method, products = all_results[0]
                _pipeline_event(domain, "playwright", f"extract_all found {len(products)} products via {winning_method}", product_count=len(products))

                # Save newly discovered strategy
                if winning_method == "css_strategy":
                    strategy = await discover_strategy(page, product_query, criteria=criteria)
                    if strategy:
                        _pipeline_event(domain, "playwright", f"saving strategy: method={strategy.discovery_method} container='{strategy.product_container[:60]}'")
                        strategy.access_method = "playwright"
                        strategy.extraction_method = "css_strategy"
                        strategy.last_successful_url = url
                        await save_strategy(domain, strategy, page_type)
                elif winning_method == "api_intercept":
                    api_strategy = ScrapingStrategy(
                        product_container="",
                        discovery_method="api_intercept",
                        access_method="playwright",
                        extraction_method="api_intercept",
                        last_successful_url=url,
                    )
                    await save_strategy(domain, api_strategy, page_type)
            else:
                _pipeline_event(domain, "playwright", f"extract_all returned 0 products (api_responses={len(captured_responses)})")

        if products:
            _pipeline_event(domain, "playwright", f"SUCCESS: {len(products)} products via {winning_method}", product_count=len(products))
            return ExtractionResult(
                products=products,
                access_method="playwright",
                extraction_method=winning_method,
                domain=domain,
                page_type=page_type,
            )

        # Classify why we got nothing
        try:
            title = await page.title()
            body_length = len(await page.content())
        except Exception:
            title = ""
            body_length = 0

        failure = classify_playwright_failure(title, body_length)
        _pipeline_event(domain, "playwright", f"FAILED: {failure.value if failure else 'unknown'} (title='{title[:80]}', body={body_length})")
        return ExtractionResult(
            access_method="playwright",
            failure_type=failure,
            failure_detail=f"title='{title[:80]}', body={body_length}",
            domain=domain,
            page_type=page_type,
        )


# ===================================================================
# Post-processing
# ===================================================================


def _post_process(
    products: list[ProductResult],
    product_query: str,
    domain: str,
) -> list[ProductResult]:
    """Merge comparison sellers and apply pagination limits."""
    if products and product_query:
        products = _merge_comparison_sellers(products, product_query, domain)

    # If CSS extracted unrelated products, check if first result matches query
    if product_query and products:
        query_lower = product_query.lower()
        has_match = any(
            query_lower in p.name.lower() or
            (p.model_id and query_lower in p.model_id.lower())
            for p in products
        )
        if not has_match:
            # Keep products anyway — they may be relevant by other criteria
            pass

    return products[:_MAX_PRODUCTS_PER_SITE]


async def _cache_success(
    domain: str,
    page_type: str,
    result: ExtractionResult,
    url: str = "",
) -> None:
    """Cache the winning access+extraction method for future use."""
    # For HTTP-based extractions, save a lightweight strategy marker
    if result.access_method in ("httpx", "curl_cffi"):
        strategy = ScrapingStrategy(
            product_container="",
            discovery_method=result.extraction_method,
            access_method=result.access_method,
            extraction_method=result.extraction_method,
            last_successful_url=url,
            # Reset failure counters on success
            consecutive_failures=0,
            validation_failures=0,
            block_type="",
            blocked_at="",
        )
        await save_strategy(domain, strategy, page_type)
        logger.info(
            "Cached strategy for %s: %s/%s",
            domain, result.access_method, result.extraction_method,
        )


# ===================================================================
# Helpers
# ===================================================================


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


def _merge_comparison_sellers(
    products: list[ProductResult],
    product_query: str,
    domain: str,
) -> list[ProductResult]:
    """Merge multiple single-seller products into one multi-seller product.

    On price-comparison pages each extracted "product" is really one seller
    row for the same product.
    """
    if len(products) < 2:
        return products

    if not all(len(p.sellers) == 1 and p.sellers[0].price is not None for p in products):
        return products

    query_lower = product_query.lower()
    has_query_match = any(
        query_lower in p.name.lower() or
        (p.model_id and query_lower in p.model_id.lower())
        for p in products
    )
    if has_query_match:
        return products

    names = [p.name for p in products]
    unique_names = set(names)
    all_same = len(unique_names) == 1
    all_unique = len(unique_names) == len(names)

    if not (all_same or all_unique):
        return products

    sellers: list[Seller] = []
    for p in products:
        seller = p.sellers[0]
        if all_unique:
            seller.name = p.name
        sellers.append(seller)

    merged = ProductResult(
        name=product_query,
        model_id=product_query,
        brand=products[0].brand,
        image_url=products[0].image_url,
        sellers=sellers,
    )

    logger.info(
        "Merged %d comparison rows into 1 product with %d sellers for %s",
        len(products), len(sellers), domain,
    )
    return [merged]
