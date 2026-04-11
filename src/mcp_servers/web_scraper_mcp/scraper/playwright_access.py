"""Playwright-based access method."""

from __future__ import annotations

import json as _json

from playwright.async_api import Browser, Response

from src.mcp_servers.web_scraper_mcp.db_cache import (
    save_strategy,
    update_success_rate,
)
from src.mcp_servers.web_scraper_mcp.diagnostics import (
    ExtractionResult,
    FailureType,
    classify_playwright_failure,
)
from src.mcp_servers.web_scraper_mcp.extractors import (
    extract_all_from_page,
    extract_from_api_responses,
    extract_with_strategy,
)
from src.mcp_servers.web_scraper_mcp.strategy import (
    ScrapingStrategy,
    discover_strategy,
    find_product_url,
)
from src.shared.browser import get_page

from src.mcp_servers.web_scraper_mcp.site_search import discover_site_search

from .helpers import _IGNORE_DOMAINS, _pipeline_event


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
    market: str = "us",
    cached_listing: ScrapingStrategy | None = None,
) -> ExtractionResult:
    """Fetch page via Playwright, navigate to product if needed, extract.

    Tries direct access first.  If the site blocks us (Cloudflare, WAF),
    retries through the residential proxy when one is configured.
    """
    result = await _do_playwright_attempt(
        browser, url, product_query, domain, page_type,
        locale, criteria, cached, cached_product, market,
        cached_listing, use_proxy=False,
    )
    if result.success or not result.is_blocked:
        return result

    # Blocked — retry with proxy if available
    from src.shared.proxy import get_proxy_for_market
    if not get_proxy_for_market(market):
        return result

    _pipeline_event(domain, "playwright", "blocked without proxy, retrying with proxy")
    return await _do_playwright_attempt(
        browser, url, product_query, domain, page_type,
        locale, criteria, cached, cached_product, market,
        cached_listing, use_proxy=True,
    )


async def _do_playwright_attempt(
    browser: Browser,
    url: str,
    product_query: str,
    domain: str,
    page_type: str,
    locale: str,
    criteria: dict[str, dict] | None,
    cached: ScrapingStrategy | None,
    cached_product: ScrapingStrategy | None,
    market: str,
    cached_listing: ScrapingStrategy | None,
    use_proxy: bool,
) -> ExtractionResult:
    """Single Playwright attempt (with or without proxy)."""
    async with get_page(browser, locale=locale, market=market, use_proxy=use_proxy) as page:
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
        nav_error = await _playwright_navigate(page, url, domain, page_type, use_proxy=use_proxy)
        if nav_error:
            return nav_error

        from src.shared.browser import dismiss_consent
        await dismiss_consent(page, domain)
        await _wait_for_content(page)

        is_listing = page_type in ("search", "catalog")
        result_page_type = page_type
        products: list = []
        winning_method = ""

        if is_listing:
            # --- Step 2a: If we have a cached listing strategy, try extracting
            # directly from the listing page (skip product navigation). ---
            if cached_listing and cached_listing.product_container:
                _pipeline_event(domain, "playwright",
                    f"trying cached listing strategy: container='{cached_listing.product_container[:60]}'")
                products = await extract_with_strategy(page, cached_listing, url, criteria=criteria)
                if products:
                    winning_method = "css_strategy"
                    result_page_type = "search"
                    await update_success_rate(domain, success=True, page_type="search")
                    _pipeline_event(domain, "playwright",
                        f"cached listing strategy hit: {len(products)} products", product_count=len(products))
                else:
                    await update_success_rate(domain, success=False, page_type="search")
                    _pipeline_event(domain, "playwright", "cached listing strategy miss")

            # --- Step 2b: Try navigating to a product page ---
            if not products:
                _pipeline_event(domain, "playwright", "listing page: finding product link...")
                nav_selector = (cached.product_link_selector if cached else "") or ""
                product_url = await find_product_url(
                    page, product_query, url, cached_selector=nav_selector,
                )
                if product_url:
                    _pipeline_event(domain, "playwright", f"navigating to product: {product_url[:120]}")
                    captured_responses.clear()
                    nav_error = await _playwright_navigate(page, product_url, domain, "product", use_proxy=use_proxy)
                    if nav_error:
                        _pipeline_event(domain, "playwright",
                            "product page navigation failed, will discover listing strategy")
                    else:
                        await _wait_for_content(page)
                        # Extract from the product page
                        _pipeline_event(domain, "playwright", "extracting from product page")
                        products, winning_method = await _extract_from_page(
                            page, product_url, domain, product_query,
                            captured_responses, criteria,
                            cached_product, "product",
                        )
                        if products:
                            result_page_type = "product"
                        if nav_selector and products:
                            await update_success_rate(domain, success=True, page_type=page_type)

            # --- Step 2c: Listing fallback — discover strategy on the listing page ---
            if not products:
                _pipeline_event(domain, "playwright",
                    "no products from product page, discovering listing strategy")
                # Navigate back to the listing page if we left it
                if page.url != url:
                    back_error = await _playwright_navigate(page, url, domain, page_type, use_proxy=use_proxy)
                    if back_error:
                        _pipeline_event(domain, "playwright", "failed to navigate back to listing")
                    else:
                        await _wait_for_content(page)

                products, winning_method = await _extract_from_page(
                    page, url, domain, product_query,
                    captured_responses, criteria,
                    None, "search",
                )
                if products:
                    result_page_type = "search"
        else:
            # --- Non-listing: extract directly ---
            _pipeline_event(domain, "playwright", "extracting product data")
            products, winning_method = await _extract_from_page(
                page, url, domain, product_query,
                captured_responses, criteria,
                cached, page_type,
            )

        # Discover site search strategy from any successfully loaded page
        try:
            await discover_site_search(page, domain)
        except Exception:
            pass  # Non-critical

        if products:
            _pipeline_event(domain, "playwright",
                f"SUCCESS: {len(products)} products via {winning_method}", product_count=len(products))
            return ExtractionResult(
                products=products,
                access_method="playwright",
                extraction_method=winning_method,
                domain=domain,
                page_type=result_page_type,
            )

        # Classify why we got nothing
        try:
            title = await page.title()
            body_length = len(await page.content())
        except Exception:
            title = ""
            body_length = 0

        failure = classify_playwright_failure(title, body_length)
        _pipeline_event(domain, "playwright",
            f"FAILED: {failure.value if failure else 'unknown'} (title='{title[:80]}', body={body_length})")
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
    *,
    use_proxy: bool = False,
) -> ExtractionResult | None:
    """Navigate to a URL, handle Cloudflare challenges. Returns error or None."""
    timeout = 40000 if use_proxy else 20000
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
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
) -> tuple[list, str]:
    """Extract product data from a (presumably product) page.

    Tries: cached strategy -> all extraction methods -> late API intercept.
    Returns (products, winning_method).
    """
    from src.shared.models import ProductResult

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
