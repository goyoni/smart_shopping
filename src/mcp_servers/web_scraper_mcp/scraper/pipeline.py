"""Main scraping pipeline orchestrator."""

from __future__ import annotations

from playwright.async_api import Browser

from src.mcp_servers.web_scraper_mcp.db_cache import (
    get_cached_strategy,
    is_domain_blocked,
    mark_validation_failure,
    update_failure,
    update_success_rate,
)
from src.mcp_servers.web_scraper_mcp.diagnostics import FailureType
from src.mcp_servers.web_scraper_mcp.extractors import validate_results
from src.mcp_servers.web_scraper_mcp.seller_contact import enrich_sellers
from src.mcp_servers.web_scraper_mcp.strategy import ScrapingStrategy
from src.shared.logging import get_logger
from src.shared.models import ProductResult

from .helpers import _MAX_PAGES, _detect_page_type, _is_safe_url, _pipeline_event, extract_domain
from .http_access import _attempt_http, _attempt_http_listing_then_product
from .playwright_access import _attempt_playwright
from .post_process import _cache_success, _post_process

logger = get_logger(__name__)

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
    market: str = "us",
    criteria: dict[str, dict] | None = None,
    page_type_hint: str = "",
    max_pages: int = _MAX_PAGES,
) -> list[ProductResult]:
    """Scrape a product page using an adaptive pipeline.

    Flow:
    1. Fetch the page (httpx -> curl_cffi -> playwright)
    2. Detect page type: product detail vs listing/search
    3. If product page -> extract product data directly (step 5)
    4. If listing page -> find matching product link, navigate to it
    5. Extract product data from the product page
       (JSON-LD / microdata / CSS strategy -> LLM discovery fallback)
    6. Validate and return

    Args:
        page_type_hint: Override auto-detection of page type. Callers like
            ``search_within_site`` that *know* the URL is a search results
            page can pass ``"search"`` to ensure the listing-then-product
            flow is used.
    """
    domain = extract_domain(url)
    page_type = page_type_hint or _detect_page_type(url)

    if not _is_safe_url(url):
        return []

    if await is_domain_blocked(domain, page_type):
        logger.info("Skipping blocked domain %s/%s", domain, page_type)
        return []

    cached = await get_cached_strategy(domain, page_type)
    cached_product = (
        await get_cached_strategy(domain, "product")
        if page_type != "product" else cached
    )
    # For listing pages, also load any cached listing-specific strategy
    # so Playwright can extract directly from the listing without
    # navigating to individual product pages.
    cached_listing = (
        await get_cached_strategy(domain, "search")
        if page_type in ("search", "catalog") and page_type != "search"
        else (cached if page_type == "search" else None)
    )
    # Fall back to the "product" strategy when no page-type-specific
    # strategy exists.  Most product pages on the same domain share
    # the same selectors regardless of how _detect_page_type classifies
    # the URL.
    if cached is None and cached_product is not None:
        cached = cached_product
    pipeline = _build_pipeline(cached)

    last_failure: FailureType | None = None
    skip_http = False  # Set when HTTP confirms JS SPA — curl_cffi won't help either

    order = [m for (m,) in pipeline]
    cached_method = cached.discovery_method if cached else None
    cached_access = cached.access_method if cached else None
    _pipeline_event(domain, "pipeline",
        f"order={order} cached_strategy={cached_method} cached_access={cached_access} page_type={page_type}. "
        f"Reasoning: {'cached access method ' + cached_access + ' tried first' if cached_access else 'no cached strategy, trying all methods in default order'}",
        page_type=page_type)

    is_listing = page_type in ("search", "catalog")

    for (access_method,) in pipeline:
        if access_method in ("httpx", "curl_cffi") and skip_http:
            _pipeline_event(domain, access_method, "skipped (JS SPA confirmed by prior HTTP method)")
            continue

        _pipeline_event(domain, access_method, "attempting")

        if access_method in ("httpx", "curl_cffi"):
            if is_listing:
                result = await _attempt_http_listing_then_product(
                    url, product_query, domain, page_type,
                    access_method, cached, cached_product,
                    market=market, max_pages=max_pages,
                )
            else:
                result = await _attempt_http(
                    url, product_query, domain, "product",
                    access_method, cached,
                    market=market,
                )
        else:
            result = await _attempt_playwright(
                browser, url, product_query, domain, page_type,
                locale, criteria, cached, cached_product,
                market=market, cached_listing=cached_listing,
                max_pages=max_pages,
            )

        if result.success:
            validated = validate_results(result.products, product_query, domain)
            if validated:
                await _cache_success(domain, result.page_type, result, url)
                processed = _post_process(validated, product_query, domain)
                # Enrich sellers missing contact info (Phase B)
                if processed:
                    try:
                        processed = await enrich_sellers(
                            processed, browser, market=market,
                        )
                    except Exception as exc:
                        _pipeline_event(domain, "contact",
                            f"seller enrichment failed: {exc}")
                return processed
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

        # If HTTP got 200 but found no products, it's a JS SPA —
        # skip remaining HTTP methods and jump to Playwright.
        if last_failure == FailureType.JS_SPA_NO_DATA and access_method in ("httpx", "curl_cffi"):
            skip_http = True

        if last_failure == FailureType.CLOUDFLARE_CAPTCHA:
            logger.warning("Cloudflare CAPTCHA on %s — cannot bypass, aborting", domain)
            break
        if last_failure == FailureType.NAVIGATION_FAILED and access_method == "playwright":
            break

    if last_failure:
        await update_failure(domain, last_failure.value, page_type)
        logger.warning(
            "All access methods failed for %s (last: %s)",
            domain, last_failure.value,
        )
    return []
