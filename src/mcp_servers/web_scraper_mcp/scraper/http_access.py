"""HTTP-based access methods (httpx and curl_cffi)."""

from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession as CurlSession

from src.mcp_servers.web_scraper_mcp.diagnostics import (
    ExtractionResult,
    FailureType,
    classify_http_failure,
    looks_like_block_page,
)
from src.mcp_servers.web_scraper_mcp.extractors import extract_all_from_soup
from src.mcp_servers.web_scraper_mcp.extractors.llm_soup_extract import extract_products_via_llm_soup
from src.mcp_servers.web_scraper_mcp.strategy import ScrapingStrategy
from src.shared.proxy import get_proxy_for_market

from .helpers import _HTTP_HEADERS, _HTTP_TIMEOUT, _MAX_PAGES, _pipeline_event, get_http_headers
from .pagination import build_page_url, deduplicate_products, detect_url_pagination


async def _attempt_http(
    url: str,
    product_query: str,
    domain: str,
    page_type: str,
    access_method: str,
    cached: ScrapingStrategy | None,
    market: str = "us",
    max_pages: int = _MAX_PAGES,
) -> ExtractionResult:
    """Try to fetch and extract products via HTTP (no browser).

    Tries direct access first.  If the site blocks us (403, Cloudflare,
    WAF), retries through the residential proxy when one is configured.
    After getting first-page results on listing pages, follows URL-param
    pagination for up to max_pages additional pages.
    """
    headers = get_http_headers(market)

    # 1. Try direct (no proxy)
    result = await _do_http_attempt(
        url, product_query, domain, page_type,
        access_method, headers, proxy=None,
    )
    if result.success or not result.is_blocked:
        # Pagination for listing pages with URL-param pagination
        if result.success and page_type in ("search", "catalog") and max_pages > 0:
            result = await _paginate_http(
                result, url, product_query, domain, page_type,
                access_method, headers, proxy=None, max_pages=max_pages,
            )
        return result

    # 2. Blocked — retry with proxy if available
    proxy = get_proxy_for_market(market)
    if not proxy:
        return result

    _pipeline_event(domain, access_method, "blocked without proxy, retrying with proxy")
    result = await _do_http_attempt(
        url, product_query, domain, page_type,
        access_method, headers, proxy=proxy,
    )
    if result.success and page_type in ("search", "catalog") and max_pages > 0:
        result = await _paginate_http(
            result, url, product_query, domain, page_type,
            access_method, headers, proxy=proxy, max_pages=max_pages,
        )
    return result


async def _do_http_attempt(
    url: str,
    product_query: str,
    domain: str,
    page_type: str,
    access_method: str,
    headers: dict,
    proxy: str | None,
) -> ExtractionResult:
    """Single HTTP fetch + extract attempt (with or without proxy)."""
    html: str | None = None
    status_code: int | None = None
    error: Exception | None = None

    if access_method == "httpx":
        try:
            async with httpx.AsyncClient(
                headers=headers,
                follow_redirects=True,
                timeout=_HTTP_TIMEOUT,
                proxy=proxy,
                verify=proxy is None,
            ) as client:
                resp = await client.get(url)
                status_code = resp.status_code
                if status_code == 200:
                    html = resp.text
        except Exception as exc:
            error = exc

    elif access_method == "curl_cffi":
        try:
            async with CurlSession(proxy=proxy, verify=proxy is None) as session:
                resp_cf = await session.get(
                    url,
                    impersonate="chrome120",
                    headers=headers,
                    timeout=_HTTP_TIMEOUT,
                    allow_redirects=True,
                )
                status_code = resp_cf.status_code
                if status_code == 200:
                    html = resp_cf.text
        except Exception as exc:
            error = exc

    proxy_label = "proxy" if proxy else "direct"

    # Classify failure
    if html is None or len(html) < 1000:
        failure = classify_http_failure(status_code, html or "", error)
        err_name = type(error).__name__ if error else None
        _pipeline_event(
            domain, access_method,
            f"HTTP failed ({proxy_label}): status={status_code} body={len(html or '')} err={err_name} → {failure.value if failure else 'unknown'}",
            status_code=status_code or 0,
        )
        return ExtractionResult(
            access_method=access_method,
            failure_type=failure,
            failure_detail=f"status={status_code}" if status_code else str(error),
            domain=domain,
            page_type=page_type,
        )

    # Check for block page even on 200 with body >= 1000
    block_type = looks_like_block_page(html)
    if block_type:
        _pipeline_event(domain, access_method,
            f"block page detected ({proxy_label}): body={len(html)} → {block_type.value}")
        return ExtractionResult(
            access_method=access_method,
            failure_type=block_type,
            failure_detail=f"Block page (body={len(html)})",
            domain=domain,
            page_type=page_type,
        )

    # Run all extraction methods on the HTML
    _pipeline_event(domain, access_method, f"HTTP 200 ({proxy_label}), body={len(html)} chars, extracting...", body_length=len(html))
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

    # HTML was fetched but no structured products — try LLM as last resort.
    # Only on search/listing pages with enough content to be worth analyzing.
    if page_type in ("search", "catalog") and len(html) >= 10_000 and product_query:
        _pipeline_event(domain, access_method, "0 products from structured extractors, trying LLM on HTML")
        llm_products = await extract_products_via_llm_soup(soup, url, domain, product_query)
        if llm_products:
            _pipeline_event(domain, access_method, f"LLM extracted {len(llm_products)} products from HTML", product_count=len(llm_products))
            return ExtractionResult(
                products=llm_products,
                access_method=access_method,
                extraction_method="llm_soup",
                domain=domain,
                page_type=page_type,
            )

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
    market: str = "us",
    max_pages: int = _MAX_PAGES,
) -> ExtractionResult:
    """Two-step HTTP: fetch listing page, find product link, fetch product page."""
    # Step 1: Fetch listing HTML
    html = await _fetch_html(url, access_method, market=market)
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
        return await _attempt_http(
            url, product_query, domain, page_type,
            access_method, cached, market=market,
        )

    _pipeline_event(domain, access_method, f"found product link: {product_url[:120]}")

    # Step 3: Fetch product page and extract
    return await _attempt_http(
        product_url, product_query, domain, "product",
        access_method, cached_product, market=market,
    )


async def _fetch_html(url: str, access_method: str, *, market: str = "us") -> str | None:
    """Fetch raw HTML via httpx or curl_cffi.

    Tries direct first, falls back to proxy on non-200 responses.
    """
    headers = get_http_headers(market)

    # Try direct first
    html = await _do_fetch_html(url, access_method, headers, proxy=None)
    if html:
        return html

    # Direct failed — try proxy if available
    proxy = get_proxy_for_market(market)
    if proxy:
        return await _do_fetch_html(url, access_method, headers, proxy=proxy)
    return None


async def _do_fetch_html(
    url: str, access_method: str, headers: dict, proxy: str | None,
) -> str | None:
    """Single HTML fetch attempt (with or without proxy)."""
    if access_method == "httpx":
        try:
            async with httpx.AsyncClient(
                headers=headers, follow_redirects=True, timeout=_HTTP_TIMEOUT,
                proxy=proxy, verify=proxy is None,
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.text
        except Exception:
            return None
    elif access_method == "curl_cffi":
        try:
            async with CurlSession(proxy=proxy, verify=proxy is None) as session:
                resp = await session.get(
                    url, impersonate="chrome120", headers=headers,
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


async def _paginate_http(
    first_result: ExtractionResult,
    url: str,
    product_query: str,
    domain: str,
    page_type: str,
    access_method: str,
    headers: dict,
    proxy: str | None,
    max_pages: int,
) -> ExtractionResult:
    """Follow URL-parameter pagination for HTTP-based extraction.

    Only works when the URL has a detectable page/offset parameter.
    """
    pagination = detect_url_pagination(url)
    if not pagination:
        return first_result

    param_name, current_page = pagination
    all_products = list(first_result.products)

    _pipeline_event(domain, f"{access_method}:pagination",
        f"detected URL param '{param_name}={current_page}', fetching up to {max_pages} more pages")

    for page_num in range(current_page + 1, current_page + max_pages + 1):
        next_url = build_page_url(url, param_name, page_num)
        html = await _do_fetch_html(next_url, access_method, headers, proxy=proxy)
        if not html or len(html) < 1000:
            _pipeline_event(domain, f"{access_method}:pagination",
                f"page {page_num}: fetch failed or too short, stopping")
            break

        block_type = looks_like_block_page(html)
        if block_type:
            _pipeline_event(domain, f"{access_method}:pagination",
                f"page {page_num}: block detected, stopping")
            break

        soup = BeautifulSoup(html, "lxml")
        extraction_results = extract_all_from_soup(soup, next_url, domain, product_query)
        if not extraction_results:
            _pipeline_event(domain, f"{access_method}:pagination",
                f"page {page_num}: 0 products, stopping")
            break

        _, products = extraction_results[0]
        all_products.extend(products)
        _pipeline_event(domain, f"{access_method}:pagination",
            f"page {page_num}: {len(products)} products (total: {len(all_products)})",
            product_count=len(products))

    result_products = deduplicate_products(all_products)
    if len(result_products) < len(all_products):
        _pipeline_event(domain, f"{access_method}:pagination",
            f"deduplicated {len(all_products)} -> {len(result_products)} products")

    return ExtractionResult(
        products=result_products,
        access_method=first_result.access_method,
        extraction_method=first_result.extraction_method,
        domain=first_result.domain,
        page_type=first_result.page_type,
    )
