"""Multi-model price comparison workflow.

Handles searching and scraping for multiple model IDs in parallel,
with cross-seller filling to find missing models at known sellers.
"""

from __future__ import annotations

import asyncio
import json

from opentelemetry import trace

from src.mcp_servers.results_processor_mcp.processor import (
    aggregate_sellers,
    find_cross_sellers,
    find_missing_models,
    format_results,
)
from src.mcp_servers.web_search_mcp.aggregator_db import update_aggregator_success
from src.mcp_servers.web_search_mcp.ecommerce_detector import (
    identify_ecommerce_sites,
    record_domain_success,
)
from src.mcp_servers.web_search_mcp.search import (
    discover_aggregators,
    get_aggregator_urls,
    search_on_site,
    search_products,
    search_products_via_browser,
)
from src.mcp_servers.web_scraper_mcp.scraper import scrape_page
from src.shared.logging import get_logger, get_tracer, operation_span
from src.shared.models import CrossSeller, ProductResult

from .query_utils import AgentState, StatusCallback, _build_locale, _MAX_SITES_TO_SCRAPE, extract_category

logger = get_logger(__name__)
_tracer = get_tracer(__name__)


def _is_useful_url(url: str, model_id: str) -> bool:
    """Reject homepage and generic category URLs that won't have product data."""
    from urllib.parse import urlparse as _urlparse

    parsed = _urlparse(url)
    path = (parsed.path or "/").rstrip("/")
    query = parsed.query or ""
    mid_lower = model_id.lower()

    # Always scrape if URL contains the model ID
    if mid_lower in url.lower():
        return True

    # Reject bare homepage
    if path in ("", "/", "/index.html", "/index.php"):
        return False

    # Reject generic category/landing pages (no query params, short path)
    if not query and path.count("/") <= 1:
        return False

    return True


async def _scrape_urls(
    browser: object,
    urls: list[str],
    model_id: str,
    locale: str,
    market: str,
    aggregator_domains: set[str] | None = None,
) -> list[ProductResult]:
    """Scrape a list of URLs and return products relevant to model_id."""
    from urllib.parse import urlparse as _urlparse

    products: list[ProductResult] = []
    mid_lower = model_id.lower()
    # Filter out homepage/generic URLs that waste time
    urls = [u for u in urls if _is_useful_url(u, model_id)]
    for url in urls:
        domain = (_urlparse(url).hostname or "").removeprefix("www.")
        try:
            with operation_span(
                _tracer, f"scrape_site:{domain}",
                input=json.dumps({"url": url, "model_id": model_id}),
            ) as site_op:
                scraped = await scrape_page(
                    browser, url, model_id, locale=locale,
                )
                # Filter to products matching the target model ID.
                _search_indicators = (
                    "/search", "/find", "/results",
                    "q=", "query=", "search=", "keyword=",
                    "keyphrase=",
                )
                _is_search_url = any(
                    ind in url.lower() for ind in _search_indicators
                )
                url_has_mid = (
                    mid_lower in url.lower() and not _is_search_url
                )
                is_detail_page = len(scraped) <= 5
                relevant: list[ProductResult] = []
                filtered_out: list[dict] = []
                for p in scraped:
                    name_match = mid_lower in p.name.lower()
                    mid_match = p.model_id and mid_lower in p.model_id.lower()
                    seller_url_match = any(
                        mid_lower in (s.url or "").lower()
                        for s in (p.sellers or [])
                    )
                    url_match = url_has_mid and is_detail_page
                    if name_match or mid_match or seller_url_match or url_match:
                        p.product_type = model_id
                        relevant.append(p)
                    else:
                        filtered_out.append({
                            "name": p.name[:100],
                            "model_id": p.model_id,
                            "reason": "model_id_mismatch",
                        })

                # On detail pages with URL match, keep only the main
                # product (highest price) to filter out upsells
                if url_has_mid and is_detail_page and len(relevant) > 1:
                    def _main_price(p: ProductResult) -> float:
                        for s in p.sellers or []:
                            if s.price is not None:
                                return s.price
                        return 0.0

                    relevant.sort(key=_main_price, reverse=True)
                    upsells = relevant[1:]
                    relevant = relevant[:1]
                    for p in upsells:
                        filtered_out.append({
                            "name": p.name[:100],
                            "model_id": p.model_id,
                            "reason": "upsell_addon",
                        })

                products.extend(relevant)

                # Track scrape outcomes for learning
                has_priced = any(
                    s.price is not None
                    for p in relevant for s in (p.sellers or [])
                )
                if aggregator_domains and domain in aggregator_domains:
                    asyncio.ensure_future(
                        update_aggregator_success(domain, market, success=has_priced)
                    )
                if has_priced:
                    asyncio.ensure_future(
                        record_domain_success(domain, market)
                    )

                site_op.set_attribute("output", json.dumps({
                    "url": url,
                    "model_id": model_id,
                    "scraped_count": len(scraped),
                    "relevant_count": len(relevant),
                    "filtered_count": len(filtered_out),
                    "relevant_products": [
                        {
                            "name": p.name[:100],
                            "model_id": p.model_id,
                            "price": p.sellers[0].price if p.sellers else None,
                            "currency": p.sellers[0].currency if p.sellers else None,
                            "seller_count": len(p.sellers),
                        }
                        for p in relevant
                    ],
                    "filtered_products": filtered_out[:10],
                }, ensure_ascii=False))
        except Exception:
            logger.warning("Failed to scrape %s for model %s", url, model_id)
            continue
    return products


async def process_multi_model(
    state: AgentState,
    model_ids: list[str],
    language: str,
    market: str,
    status_callback: StatusCallback | None,
) -> AgentState:
    """Search for multiple model IDs in parallel for price comparison.

    Flow (per price_search.md):
    1. For each model, search via web + market aggregators in parallel
    2. Scrape all found URLs for prices and seller info
    3. Build seller->model->price map, find missing models per seller
    4. For sellers with missing models, search that seller's site directly
    5. Re-aggregate and return results with cross-seller bundles
    """
    locale = _build_locale(language, market)

    async def _add_status(message: str) -> None:
        state.status_messages.append(message)
        if status_callback:
            await status_callback(state.session_id, message)

    # Try to detect category from query context for aggregator filtering
    category = extract_category(state.query)

    # Open browser once for both search and scrape phases
    from src.shared.browser import get_browser

    async with get_browser() as browser:

        # ------------------------------------------------------------------
        # Phase 1: Search for each model (web search + aggregators)
        # ------------------------------------------------------------------
        with operation_span(
            _tracer, "search_models",
            input=json.dumps(model_ids),
            model_count=len(model_ids),
        ) as search_op:
            async def search_single_model(model_id: str) -> tuple[list[str], set[str]]:
                """Return (URLs to scrape, aggregator domains) for a model ID."""
                await _add_status(f"Searching for {model_id}...")
                with operation_span(
                    _tracer, f"search_model:{model_id}",
                    input=json.dumps({"model_id": model_id, "market": market}),
                ) as model_op:
                    # Dual-source: browser (Google) + HTTP (DDG) in parallel
                    browser_task = search_products_via_browser(
                        browser, model_id, language, market,
                    )
                    ddg_task = search_products(model_id, language, market)
                    browser_results, ddg_results = await asyncio.gather(
                        browser_task, ddg_task, return_exceptions=True,
                    )

                    # Merge results: browser first, then DDG, dedup by URL
                    results = []
                    seen_urls: set[str] = set()
                    for source in (browser_results, ddg_results):
                        if isinstance(source, Exception):
                            logger.warning("Search source failed for %s: %s", model_id, source)
                            continue
                        for r in source:
                            if r.url not in seen_urls:
                                seen_urls.add(r.url)
                                results.append(r)

                    model_op.set_attribute("browser_result_count",
                        len(browser_results) if not isinstance(browser_results, Exception) else 0)
                    model_op.set_attribute("ddg_result_count",
                        len(ddg_results) if not isinstance(ddg_results, Exception) else 0)
                    model_op.set_attribute("merged_result_count", len(results))

                ecom_data = [
                    {"url": r.url, "title": r.title, "snippet": r.snippet}
                    for r in results
                ]
                ecom_signals = await identify_ecommerce_sites(ecom_data, market=market)

                urls = [s.url for s in ecom_signals[:_MAX_SITES_TO_SCRAPE]]

                # Add aggregator direct URLs from DB (filtered by market + category)
                aggregator_entries = await get_aggregator_urls(model_id, market, category)
                seen_domains = {s.domain for s in ecom_signals[:_MAX_SITES_TO_SCRAPE]}
                added_aggregators = []
                for entry in aggregator_entries:
                    if entry["domain"] not in seen_domains:
                        urls.append(entry["url"])
                        seen_domains.add(entry["domain"])
                        added_aggregators.append(entry["domain"])

                ecom_domains = [s.domain for s in ecom_signals[:_MAX_SITES_TO_SCRAPE]]
                model_op.set_attribute("summary",
                    f"Model '{model_id}': {len(results)} search results -> {len(ecom_signals)} ecommerce sites "
                    f"({', '.join(ecom_domains)})"
                    + (f" + {len(added_aggregators)} aggregators ({', '.join(added_aggregators)})" if added_aggregators else "")
                    + f" = {len(urls)} URLs to scrape")

                model_op.set_attribute("output", json.dumps({
                    "model_id": model_id,
                    "search_result_count": len(results),
                    "ecommerce_sites": [
                        {"domain": s.domain, "url": s.url, "confidence": s.confidence,
                         "signals": s.signals}
                        for s in ecom_signals[:_MAX_SITES_TO_SCRAPE]
                    ],
                    "aggregator_urls": [e["domain"] for e in aggregator_entries],
                    "total_urls_to_scrape": len(urls),
                }, ensure_ascii=False))

                agg_domains = set(e["domain"] for e in aggregator_entries)
                return urls, agg_domains

        # Discover new aggregators for this market+category (async, non-blocking)
        if category:
            asyncio.ensure_future(discover_aggregators(market, category))

        # Stagger searches to avoid rate-limiting by DuckDuckGo
        async def _staggered_search(idx: int, mid: str) -> tuple[list[str], set[str]]:
            if idx > 0:
                await asyncio.sleep(idx * 2.5)
            return await search_single_model(mid)

        search_tasks = [_staggered_search(i, mid) for i, mid in enumerate(model_ids)]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        # Separate URLs and aggregator domains
        urls_per_model: list[list[str] | Exception] = []
        all_aggregator_domains: set[str] = set()
        for result in search_results:
            if isinstance(result, Exception):
                urls_per_model.append(result)
            else:
                urls, agg_domains = result
                urls_per_model.append(urls)
                all_aggregator_domains.update(agg_domains)

        search_op.set_attribute("output", json.dumps({
            "models_searched": len(model_ids),
            "urls_found": sum(
                len(u) for u in urls_per_model if not isinstance(u, Exception)
            ),
        }))

        # ------------------------------------------------------------------
        # Phase 2: Scrape all URLs in parallel per model
        # ------------------------------------------------------------------
        await _add_status("Scraping product pages...")
        all_products: list[ProductResult] = []

        with operation_span(
            _tracer, "scrape_models",
            input=json.dumps(model_ids),
        ) as scrape_op:
            scrape_tasks = []
            scrape_model_ids = []
            for mid, urls_result in zip(model_ids, urls_per_model):
                if isinstance(urls_result, Exception):
                    logger.warning("Search failed for %s: %s", mid, urls_result)
                    continue
                if urls_result:
                    scrape_tasks.append(_scrape_urls(browser, urls_result, mid, locale, market, all_aggregator_domains))
                    scrape_model_ids.append(mid)

            if scrape_tasks:
                scrape_results = await asyncio.gather(*scrape_tasks, return_exceptions=True)
                for mid, result in zip(scrape_model_ids, scrape_results):
                    if isinstance(result, Exception):
                        logger.warning("Scrape failed for %s: %s", mid, result)
                        continue
                    all_products.extend(result)

            scrape_op.set_attribute("output", json.dumps({
                "product_count": len(all_products),
            }))

        # Aggregate sellers (merge same product from different sites)
        if all_products:
            all_products = aggregate_sellers(all_products)

        # ------------------------------------------------------------------
        # Phase 3: Fill missing models per seller (cross-seller filling)
        # ------------------------------------------------------------------
        if len(model_ids) > 1 and all_products:
            missing = find_missing_models(all_products, model_ids)
            if missing:
                fill_count = sum(len(v) for v in missing.values())
                await _add_status(
                    f"Checking {len(missing)} sellers for {fill_count} missing models..."
                )

                with operation_span(
                    _tracer, "fill_missing_models",
                    input=json.dumps({
                        "sellers": len(missing),
                        "missing_count": fill_count,
                    }),
                ) as fill_op:
                    fill_op.set_attribute("summary",
                        f"Cross-seller filling: {len(missing)} sellers have gaps. "
                        + ", ".join(f"{d}: missing {ids}" for d, ids in list(missing.items())[:5]))
                    fill_tasks = []
                    fill_meta: list[tuple[str, str]] = []

                    for domain, missing_ids in missing.items():
                        for mid in missing_ids:
                            fill_tasks.append(
                                search_on_site(mid, domain, language, market)
                            )
                            fill_meta.append((domain, mid))

                    fill_results = await asyncio.gather(*fill_tasks, return_exceptions=True)

                    for (domain, mid), result in zip(fill_meta, fill_results):
                        if isinstance(result, Exception):
                            logger.warning("Site search failed for %s on %s: %s", mid, domain, result)
                            continue
                        if not result:
                            continue

                        urls_to_scrape = [r.url for r in result[:3]]
                        filled = await _scrape_urls(browser, urls_to_scrape, mid, locale, market)
                        all_products.extend(filled)

                    # Re-aggregate after filling
                    if all_products:
                        all_products = aggregate_sellers(all_products)

                    fill_op.set_attribute("output", json.dumps({
                        "product_count": len(all_products),
                    }))

    # ------------------------------------------------------------------
    # Compute cross-sellers and format
    # ------------------------------------------------------------------
    with operation_span(
        _tracer, "format_results",
        input=json.dumps({"product_count": len(all_products)}),
    ) as fmt_op:
        cross_sellers: list[CrossSeller] = []
        if all_products:
            cross_sellers = find_cross_sellers(all_products)

        if all_products:
            formatted = format_results(all_products, "price_comparison")
            all_products = [
                ProductResult(**item["product"])
                for item in formatted["products"]
            ]

        fmt_op.set_attribute("output", json.dumps({
            "product_count": len(all_products),
            "cross_seller_count": len(cross_sellers),
        }))

    state.results = all_products
    state.cross_sellers = cross_sellers

    cross_msg = ""
    if cross_sellers:
        cross_msg = f", {len(cross_sellers)} sellers carry multiple items"
    await _add_status(
        f"Found {len(all_products)} products for {len(model_ids)} models{cross_msg}"
    )
    return state
