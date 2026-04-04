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
from src.mcp_servers.web_scraper_mcp.strategy import ScrapingStrategy
from src.shared.logging import get_logger
from src.shared.models import ProductResult

from .helpers import _detect_page_type, _is_safe_url, _pipeline_event, extract_domain
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
    criteria: dict[str, dict] | None = None,
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
    """
    domain = extract_domain(url)
    page_type = _detect_page_type(url)

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
