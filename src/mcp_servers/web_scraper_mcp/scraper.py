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
from src.mcp_servers.web_scraper_mcp.strategy import (
    ScrapingStrategy,
    discover_strategy,
    find_product_url,
)
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

    Flow:
    1. Fetch the page (httpx → curl_cffi → playwright)
    2. Detect page type: product detail vs listing/search
    3. If product page → extract product data directly (step 5)
    4. If listing page → find matching product link, navigate to it
    5. Extract product data from the product page
       (JSON-LD / microdata / CSS strategy → LLM discovery fallback)
    6. Validate and return
    """
    domain = extract_domain(url)
    page_type = _detect_page_type(url)

    if not _is_safe_url(url):
        return []

    # Check if domain is currently blocked (CAPTCHA/WAF with active TTL)
    if await is_domain_blocked(domain, page_type):
        logger.info("Skipping blocked domain %s/%s", domain, page_type)
        return []

    # Load cached strategies for both search and product page types
    cached = await get_cached_strategy(domain, page_type)
    cached_product = (
        await get_cached_strategy(domain, "product")
        if page_type != "product" else cached
    )
    pipeline = _build_pipeline(cached)

    last_failure: FailureType | None = None

    order = [m for (m,) in pipeline]
    cached_method = cached.discovery_method if cached else None
    cached_access = cached.access_method if cached else None
    _pipeline_event(domain, "pipeline",
        f"order={order} cached_strategy={cached_method} cached_access={cached_access} page_type={page_type}. "
        f"Reasoning: {'cached access method ' + cached_access + ' tried first' if cached_access else 'no cached strategy, trying all methods in default order'}",
        page_type=page_type)

    is_listing = page_type in ("search", "catalog")

    for (access_method,) in pipeline:
        _pipeline_event(domain, access_method, "attempting")

        if access_method in ("httpx", "curl_cffi"):
            if is_listing:
                # For listing pages via HTTP: fetch listing, find product
                # link, then fetch the product page via a second request.
                result = await _attempt_http_listing_then_product(
                    url, product_query, domain, page_type,
                    access_method, cached, cached_product,
                )
            else:
                result = await _attempt_http(
                    url, product_query, domain, "product",
                    access_method, cached,
                )
        else:
            result = await _attempt_playwright(
                browser, url, product_query, domain, page_type,
                locale, criteria, cached, cached_product,
            )

        if result.success:
            validated = validate_results(result.products, product_query, domain)
            if validated:
                await _cache_success(domain, "product", result, url)
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


async def _attempt_http_listing_then_product(
    url: str,
    product_query: str,
    domain: str,
    page_type: str,
    access_method: str,
    cached: ScrapingStrategy | None,
    cached_product: ScrapingStrategy | None,
) -> ExtractionResult:
    """Two-step HTTP: fetch listing page, find product link, fetch product page."""
    # Step 1: Fetch listing HTML
    html = await _fetch_html(url, access_method)
    if not html or len(html) < 1000:
        _pipeline_event(domain, access_method, "listing fetch failed or too short")
        return ExtractionResult(
            access_method=access_method,
            failure_type=FailureType.JS_SPA_NO_DATA,
            failure_detail="listing HTML too short or empty",
            domain=domain,
            page_type=page_type,
        )

    # Step 2: Find product link in listing HTML
    product_url = _find_product_link_in_html(html, product_query, url, domain)
    if not product_url:
        _pipeline_event(domain, access_method, "no product link found in listing HTML")
        # Fall through — no product link found via static HTML.
        # Try extracting directly in case the listing has structured data.
        return await _attempt_http(
            url, product_query, domain, page_type,
            access_method, cached,
        )

    _pipeline_event(domain, access_method, f"found product link: {product_url[:120]}")

    # Step 3: Fetch product page and extract
    return await _attempt_http(
        product_url, product_query, domain, "product",
        access_method, cached_product,
    )


async def _fetch_html(url: str, access_method: str) -> str | None:
    """Fetch raw HTML via httpx or curl_cffi."""
    if access_method == "httpx":
        try:
            async with httpx.AsyncClient(
                headers=_HTTP_HEADERS, follow_redirects=True, timeout=_HTTP_TIMEOUT,
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.text
        except Exception:
            return None
    elif access_method == "curl_cffi":
        try:
            async with CurlSession() as session:
                resp = await session.get(
                    url, impersonate="chrome120", headers=_HTTP_HEADERS,
                    timeout=_HTTP_TIMEOUT, allow_redirects=True,
                )
                if resp.status_code == 200:
                    return resp.text
        except Exception:
            return None
    return None


def _find_product_link_in_html(
    html: str,
    product_query: str,
    base_url: str,
    domain: str,
) -> str | None:
    """Parse listing HTML and find the best product link matching the query."""
    soup = BeautifulSoup(html, "lxml")
    query_lower = product_query.lower()
    query_tokens = [t for t in query_lower.split() if len(t) >= 3]

    # Common product link patterns
    link_selectors = [
        "a[href*='/product/']", "a[href*='/dp/']",
        "a[href*='/item/']", "a[href*='/p/']",
        "a[href*='/model/']",
        ".product-card a", ".product-item a",
        "h2 a", "h3 a",
        "[class*='product'] a[href]",
        "[class*='title'] a[href]",
    ]

    best_url: str | None = None
    best_score = 0

    for selector in link_selectors:
        for link in soup.select(selector)[:30]:
            href = link.get("href", "")
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue

            text = link.get_text(strip=True).lower()
            if not text:
                # Try parent element text
                parent = link.find_parent(class_=re.compile(r"product|item|card", re.I))
                if parent:
                    text = parent.get_text(strip=True).lower()[:200]

            score = 0
            if query_lower in text:
                score += 10
            if query_lower in href.lower():
                score += 5
            for tok in query_tokens:
                if tok in text:
                    score += 2
                if tok in href.lower():
                    score += 1

            if score > best_score:
                best_score = score
                best_url = urljoin(base_url, href)

    if best_score >= 2:
        return best_url
    return None


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
    cached_product: ScrapingStrategy | None = None,
) -> ExtractionResult:
    """Fetch page via Playwright, navigate to product if needed, extract.

    Flow:
    1. Load the initial URL
    2. If listing/search page → find product link → navigate to it
    3. Extract product data from the (now single-product) page
    """
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

        # --- Step 1: Navigate to the initial URL ---
        _pipeline_event(domain, "playwright", f"navigating to {url[:120]}")
        nav_error = await _playwright_navigate(page, url, domain, page_type)
        if nav_error:
            return nav_error

        from src.shared.browser import dismiss_consent
        await dismiss_consent(page, domain)
        await _wait_for_content(page)

        is_listing = page_type in ("search", "catalog")

        # --- Step 2: If listing page, navigate to the product page ---
        current_url = url
        if is_listing:
            _pipeline_event(domain, "playwright", "listing page detected, finding product link...")
            nav_selector = (cached.product_link_selector if cached else "") or ""
            product_url = await find_product_url(
                page, product_query, url, cached_selector=nav_selector,
            )
            if product_url:
                _pipeline_event(domain, "playwright", f"navigating to product: {product_url[:120]}")
                # Clear captured responses for the product page
                captured_responses.clear()
                nav_error = await _playwright_navigate(page, product_url, domain, "product")
                if nav_error:
                    return nav_error
                await _wait_for_content(page)
                current_url = product_url
                # Cache the navigation selector for this domain
                if nav_selector:
                    # Already cached — update success
                    await update_success_rate(domain, success=True, page_type=page_type)
            else:
                _pipeline_event(domain, "playwright", "no product link found, extracting from listing as fallback")

        # --- Step 3: Extract product data from the page ---
        _pipeline_event(domain, "playwright", "extracting product data")
        products, winning_method = await _extract_from_page(
            page, current_url, domain, product_query,
            captured_responses, criteria,
            cached_product if is_listing else cached,
            page_type,
        )

        if products:
            _pipeline_event(domain, "playwright", f"SUCCESS: {len(products)} products via {winning_method}", product_count=len(products))
            return ExtractionResult(
                products=products,
                access_method="playwright",
                extraction_method=winning_method,
                domain=domain,
                page_type="product",
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


async def _playwright_navigate(
    page: object,
    url: str,
    domain: str,
    page_type: str,
) -> ExtractionResult | None:
    """Navigate to a URL, handle Cloudflare challenges. Returns error or None."""
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

    # Handle Cloudflare challenges
    try:
        title = await page.title()
        title_lower = title.lower()
        if "just a moment" in title_lower:
            _pipeline_event(domain, "playwright", "JS challenge detected, waiting")
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
                    failure_detail="JS challenge did not resolve",
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

    return None



async def _wait_for_content(page: object) -> None:
    """Wait for page content to be loaded."""
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass

    try:
        await page.wait_for_selector(
            "[class*='price'], [class*='Price'], [data-price], "
            "[class*='product'], [class*='Product']",
            timeout=8000,
        )
    except Exception:
        pass


async def _extract_from_page(
    page: object,
    url: str,
    domain: str,
    product_query: str,
    captured_responses: list[dict],
    criteria: dict[str, dict] | None,
    cached: ScrapingStrategy | None,
    page_type: str,
) -> tuple[list[ProductResult], str]:
    """Extract product data from a (presumably product) page.

    Tries: cached strategy → all extraction methods → late API intercept.
    Returns (products, winning_method).
    """
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
            # Prefer methods with priced results, then by count
            def _score(pair: tuple[str, list]) -> tuple[int, int]:
                _, prods = pair
                priced = sum(
                    1 for p in prods
                    if any(s.price is not None and s.price > 0 for s in p.sellers)
                )
                return (priced, len(prods))

            winning_method, products = max(all_results, key=_score)
            _pipeline_event(domain, "playwright", f"extract_all found {len(products)} products via {winning_method} (from {len(all_results)} methods)", product_count=len(products))

            # Save newly discovered strategy
            if winning_method == "css_strategy":
                strategy = await discover_strategy(page, product_query, criteria=criteria)
                if strategy:
                    strategy.access_method = "playwright"
                    strategy.extraction_method = "css_strategy"
                    strategy.last_successful_url = url
                    await save_strategy(domain, strategy, "product")
            elif winning_method == "api_intercept":
                api_strategy = ScrapingStrategy(
                    product_container="",
                    discovery_method="api_intercept",
                    access_method="playwright",
                    extraction_method="api_intercept",
                    last_successful_url=url,
                )
                await save_strategy(domain, api_strategy, "product")
        else:
            _pipeline_event(domain, "playwright", f"extract_all returned 0 products (api_responses={len(captured_responses)})")

    # Late API intercept: check in-page captured responses
    if len(products) <= 1:
        import asyncio as _asyncio
        _pipeline_event(domain, "playwright", f"low results ({len(products)}), checking in-page API captures...")
        await _asyncio.sleep(5)
        try:
            js_responses = await page.evaluate("window.__capturedApiResponses || []")
            if js_responses:
                _pipeline_event(domain, "playwright", f"found {len(js_responses)} in-page API responses")
                for resp in js_responses:
                    if resp not in captured_responses:
                        captured_responses.append(resp)
        except Exception:
            pass
        if captured_responses:
            api_products = extract_from_api_responses(
                captured_responses, url, domain, product_query,
            )
            if len(api_products) > len(products):
                products = api_products
                winning_method = "api_intercept"
                _pipeline_event(domain, "playwright", f"late API intercept found {len(products)} products", product_count=len(products))

    return products, winning_method


# ===================================================================
# Post-processing
# ===================================================================


def _post_process(
    products: list[ProductResult],
    product_query: str,
    domain: str,
) -> list[ProductResult]:
    """Merge comparison sellers and apply pagination limits."""
    initial_count = len(products)
    if products and product_query:
        products = _merge_comparison_sellers(products, product_query, domain)

    # If no product's name/model_id contains the query, the scraper likely
    # picked up unrelated items (e.g. promoted products, nav elements).
    # Drop them so they don't pollute results with wrong prices.
    if product_query and products:
        query_lower = product_query.lower()
        query_tokens = [t for t in query_lower.split() if len(t) >= 3]
        matched = [
            p for p in products
            if query_lower in p.name.lower()
            or (p.model_id and query_lower in p.model_id.lower())
            or any(
                tok in p.name.lower() or (p.model_id and tok in p.model_id.lower())
                for tok in query_tokens
            )
        ]
        if matched:
            filtered_count = len(products) - len(matched)
            if filtered_count > 0:
                _pipeline_event(domain, "post_process",
                    f"Relevance filter: {len(matched)}/{len(products)} match query '{product_query}', dropped {filtered_count} unrelated",
                    matched_count=len(matched), filtered_count=filtered_count)
            products = matched
        elif len(products) == 1:
            _pipeline_event(domain, "post_process",
                f"Single product kept despite no name match (likely different language/script)")
        else:
            _pipeline_event(domain, "post_process",
                f"All {len(products)} products dropped: none match query '{product_query}'")
            products = []

    final = products[:_MAX_PRODUCTS_PER_SITE]
    _pipeline_event(domain, "post_process",
        f"Post-processing: {initial_count} input -> {len(final)} output",
        input_count=initial_count, output_count=len(final))
    return final


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
        _pipeline_event(domain, "merge_sellers",
            f"Skip merge: not all {len(products)} products have exactly 1 priced seller")
        return products

    query_lower = product_query.lower()
    has_query_match = any(
        query_lower in p.name.lower() or
        (p.model_id and query_lower in p.model_id.lower())
        for p in products
    )
    if has_query_match:
        _pipeline_event(domain, "merge_sellers",
            f"Skip merge: query '{product_query}' found in product names (distinct products, not comparison rows)")
        return products

    names = [p.name for p in products]
    unique_names = set(names)
    all_same = len(unique_names) == 1
    all_unique = len(unique_names) == len(names)

    if not (all_same or all_unique):
        _pipeline_event(domain, "merge_sellers",
            f"Skip merge: names are neither all-same nor all-unique ({len(unique_names)} unique out of {len(names)})")
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

    pattern = "all-same-name" if all_same else "all-unique-names (seller rows)"
    _pipeline_event(domain, "merge_sellers",
        f"Merged {len(products)} comparison rows into 1 product with {len(sellers)} sellers (pattern: {pattern})",
        merged_count=len(products), seller_count=len(sellers))

    logger.info(
        "Merged %d comparison rows into 1 product with %d sellers for %s",
        len(products), len(sellers), domain,
    )
    return [merged]
