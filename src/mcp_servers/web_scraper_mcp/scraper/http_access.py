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
)
from src.mcp_servers.web_scraper_mcp.extractors import extract_all_from_soup
from src.mcp_servers.web_scraper_mcp.strategy import ScrapingStrategy

from .helpers import _HTTP_HEADERS, _HTTP_TIMEOUT, _pipeline_event


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
