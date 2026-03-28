"""Main orchestrator agent.

Manages conversation state, routes to specialized MCP servers,
and orchestrates the full search workflow.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from opentelemetry import trace

from src.mcp_servers.product_criteria_mcp.criteria import (
    extract_query_attributes,
    get_criteria,
    normalize_category,
    research_criteria,
)
from src.shared.market_config import get_all_category_aliases
from src.mcp_servers.product_criteria_mcp.db_cache import get_cached, save_cached
from src.mcp_servers.product_criteria_mcp.llm_criteria import (
    discover_criteria_via_llm,
    extract_query_attributes_via_llm,
)
from src.mcp_servers.results_processor_mcp.processor import (
    aggregate_sellers,
    find_cross_sellers,
    find_missing_models,
    format_results,
    validate_results,
)
from src.mcp_servers.web_search_mcp.ecommerce_detector import identify_ecommerce_sites
from src.mcp_servers.web_search_mcp.search import (
    build_refined_query,
    discover_aggregators,
    get_aggregator_urls,
    search_on_site,
    search_products,
)
from src.mcp_servers.web_scraper_mcp.scraper import scrape_page
from src.shared.browser import get_browser
from src.shared.logging import agent_span, get_logger, get_tracer, operation_span, set_session_id
from src.shared.models import CrossSeller, ProductResult, SearchStatus

logger = get_logger(__name__)
_tracer = get_tracer(__name__)

StatusCallback = Callable[[str, str], Awaitable[None]]

_MAX_SITES_TO_SCRAPE = 5


def extract_category(query: str) -> str | None:
    """Extract a product category from a search query using keyword matching.

    Returns the canonical category key or None if no category is detected.
    Checks longer keywords first to handle multi-word matches.
    Category aliases are loaded from config/markets/languages.json.
    """
    text = query.lower().strip()
    aliases = get_all_category_aliases()
    for keyword, category in sorted(aliases.items(), key=lambda x: -len(x[0])):
        if keyword in text:
            return category
    return None


def detect_model_ids(query: str) -> list[str]:
    """Detect comma/space-separated model IDs in a query.

    Returns a list of model ID strings, or an empty list if the query
    is a natural-language search rather than a model-based lookup.

    Model IDs are alphanumeric tokens containing both letters and digits
    (e.g. 'A1234', 'WH-1000XM5', 'LG-GR-F501ELDZ').

    Works with single model IDs (e.g. "BFL523MB1F") as well as
    multiple (e.g. "BFL523MB1F, HBG578EB3").
    """
    # Strip common prefixes
    cleaned = re.sub(
        r"^(find|search|compare|look\s+up|price\s+(for|of|check))\s+",
        "",
        query.strip(),
        flags=re.IGNORECASE,
    )

    # Split on comma, semicolon, " and ", or " vs "
    parts = re.split(r"[,;]\s*|\s+(?:and|vs\.?)\s+", cleaned)
    parts = [p.strip() for p in parts if p.strip()]

    # Each part should look like a model ID: alphanumeric token with both
    # letters and digits, no spaces (model IDs are single tokens like
    # "BFL523MB1F" or "WH-1000XM5", not phrases).
    model_ids: list[str] = []
    for part in parts:
        token = part.strip()
        # Model IDs don't contain spaces (except hyphens/underscores)
        if " " in token:
            # Multi-word part → not a model ID
            continue
        has_letter = any(c.isalpha() for c in token)
        has_digit = any(c.isdigit() for c in token)
        if has_letter and has_digit and len(token) >= 3:
            model_ids.append(token)

    # Accept single model IDs too — if all parts parsed as model IDs
    if not model_ids:
        return []
    # If there's extra non-model text, it's a natural language query
    if len(model_ids) < len(parts):
        return []
    return model_ids


def _build_locale(language: str, market: str) -> str:
    """Build a browser locale string from language and market codes."""
    return f"{language}-{market.upper()}"


@dataclass
class AgentState:
    """Tracks the current state of an agent session."""

    session_id: str
    status: SearchStatus = SearchStatus.PENDING
    query: str = ""
    language: str = "en"
    results: list[ProductResult] = field(default_factory=list)
    cross_sellers: list[CrossSeller] = field(default_factory=list)
    conversation_history: list[dict[str, str]] = field(default_factory=list)
    status_messages: list[str] = field(default_factory=list)


class MainAgent:
    """Orchestrates the shopping workflow across MCP servers."""

    def __init__(
        self,
        session_id: str,
        status_callback: StatusCallback | None = None,
    ) -> None:
        self.state = AgentState(session_id=session_id)
        self._status_callback = status_callback

    async def process_query(
        self,
        query: str,
        language: str = "en",
        market: str = "us",
    ) -> AgentState:
        """Process a user search query through the full pipeline.

        Workflow:
        1. Extract product category from query
        2. Get/research product criteria for the category
        3. Search web for products
        4. Identify e-commerce sites from search results
        5. Scrape top e-commerce sites for product data
        6. Aggregate sellers (deduplicate products across sites)
        7. Validate results against criteria
        8. Format results for display
        """
        set_session_id(self.state.session_id)
        self.state.query = query
        self.state.language = language
        self.state.status = SearchStatus.IN_PROGRESS
        await self._add_status("Started search...")

        with agent_span(
            _tracer, "MainAgent",
            input=query,
            query=query, market=market,
        ) as root_span:
            try:
                # Check for model-based price search
                model_ids = detect_model_ids(query)
                if model_ids:
                    search_type = "multi_model" if len(model_ids) > 1 else "single_model_price"
                    root_span.set_attribute("search_type", search_type)
                    root_span.set_attribute("model_ids", json.dumps(model_ids))
                    await self._add_status(f"Searching prices for {len(model_ids)} model(s)...")
                    state = await self._process_multi_model(model_ids, language, market)
                    root_span.set_attribute("output", json.dumps({
                        "search_type": search_type,
                        "product_count": len(state.results),
                    }))
                    return state

                # Step 1: Extract product category
                category = extract_category(query)
                if category:
                    root_span.set_attribute("category", category)

                # Step 1.5: Extract user-intent attributes from query
                attributes = extract_query_attributes(query)

                if attributes:
                    root_span.set_attribute(
                        "query_attributes",
                        json.dumps(
                            [
                                {"key": a.criterion_key, "direction": a.direction, "label": a.display_label}
                                for a in attributes
                            ],
                            ensure_ascii=False,
                        ),
                    )

                # Step 2: Get criteria for the category
                criteria: dict[str, dict] = {}
                cache_key = category if category else normalize_category(query)
                if category:
                    await self._add_status(f"Looking up criteria for {category}...")
                    with operation_span(
                        _tracer, "get_criteria",
                        input=category,
                    ) as criteria_op:
                        criteria = get_criteria(category)
                        criteria_op.set_attribute(
                            "output",
                            json.dumps(list(criteria.keys())),
                        )

                # LLM fallback for unknown categories
                if not criteria:
                    cached = await get_cached(cache_key)
                    if cached:
                        criteria = cached
                        root_span.set_attribute("criteria_source", "cache")
                    else:
                        criteria = await discover_criteria_via_llm(cache_key)
                        if criteria:
                            await save_cached(cache_key, criteria)
                            root_span.set_attribute("criteria_source", "llm")

                # LLM fallback for attribute extraction on unknown categories
                if not attributes and criteria and not category:
                    attributes = await extract_query_attributes_via_llm(query, criteria)

                # Step 2.5: Boost importance of criteria matching user attributes
                if attributes and criteria:
                    for attr in attributes:
                        if attr.criterion_key in criteria:
                            criteria[attr.criterion_key] = {
                                **criteria[attr.criterion_key],
                                "importance": "high",
                            }

                # Step 3: Web search (HTTP-based, no browser)
                await self._add_status("Searching the web...")
                refined = None
                if attributes or category:
                    refined = build_refined_query(query, category, attributes)
                with operation_span(
                    _tracer, "search_web",
                    input=query,
                    language=language, market=market,
                ) as search_op:
                    if refined:
                        search_op.set_attribute("refined_query", refined)
                    search_results = await search_products(query, language, market, refined_query=refined)
                    search_op.set_attribute("result_count", len(search_results))
                    search_op.set_attribute(
                        "output",
                        json.dumps(
                            [{"url": r.url, "title": r.title} for r in search_results],
                            ensure_ascii=False,
                        ) if search_results else "[]",
                    )

                # Enrich criteria from search snippets
                if category and search_results:
                    snippets = [r.snippet for r in search_results if r.snippet]
                    if snippets:
                        criteria = research_criteria(snippets, criteria)

                # LLM enrichment from snippets when criteria is still empty
                if not criteria and search_results:
                    snippets = [r.snippet for r in search_results if r.snippet]
                    if snippets:
                        criteria = await discover_criteria_via_llm(cache_key, snippets=snippets)
                        if criteria:
                            await save_cached(cache_key, criteria)

                if not search_results:
                    root_span.set_attribute("exit_reason", "no_search_results")
                    root_span.set_attribute("output", json.dumps({"exit_reason": "no_search_results"}))
                    await self._add_status("No search results found")
                    self.state.status = SearchStatus.COMPLETED
                    return self.state

                # Step 4: Identify e-commerce sites
                await self._add_status(f"Analyzing {len(search_results)} results...")
                with operation_span(
                    _tracer, "detect_ecommerce",
                    input=json.dumps([r.url for r in search_results]),
                    result_count=len(search_results),
                ) as ecom_op:
                    urls_data = [
                        {"url": r.url, "title": r.title, "snippet": r.snippet}
                        for r in search_results
                    ]
                    ecommerce_signals = identify_ecommerce_sites(urls_data)
                    ecom_op.set_attribute("ecommerce_count", len(ecommerce_signals))
                    ecom_op.set_attribute(
                        "output",
                        json.dumps(
                            [
                                {"domain": s.domain, "url": s.url, "confidence": s.confidence}
                                for s in ecommerce_signals
                            ],
                            ensure_ascii=False,
                        ) if ecommerce_signals else "[]",
                    )

                if not ecommerce_signals:
                    root_span.set_attribute("exit_reason", "no_ecommerce_sites")
                    root_span.set_attribute("output", json.dumps({"exit_reason": "no_ecommerce_sites"}))
                    await self._add_status("No e-commerce sites found in results")
                    self.state.status = SearchStatus.COMPLETED
                    return self.state

                # Step 5: Scrape top e-commerce sites (browser needed here)
                sites_to_scrape = ecommerce_signals[:_MAX_SITES_TO_SCRAPE]
                await self._add_status(f"Scraping {len(sites_to_scrape)} e-commerce sites...")

                locale = _build_locale(language, market)

                async with get_browser() as browser:
                    with operation_span(
                        _tracer, "scrape_sites",
                        input=json.dumps(
                            [{"domain": s.domain, "url": s.url} for s in sites_to_scrape],
                            ensure_ascii=False,
                        ),
                        site_count=len(sites_to_scrape),
                    ) as scrape_op:
                        all_products: list[ProductResult] = []
                        for signal in sites_to_scrape:
                            try:
                                with operation_span(
                                    _tracer, f"scrape_site:{signal.domain}",
                                    input=signal.url,
                                ) as site_op:
                                    await self._add_status(f"Scraping {signal.domain}...")
                                    products = await scrape_page(
                                        browser, signal.url, query,
                                        locale=locale,
                                        criteria=criteria if criteria else None,
                                    )
                                    if category:
                                        for p in products:
                                            if not p.category:
                                                p.category = category
                                    all_products.extend(products)
                                    site_op.set_attribute("product_count", len(products))
                                    site_op.set_attribute("output", json.dumps({
                                        "url": signal.url,
                                        "product_count": len(products),
                                    }))
                            except Exception:
                                logger.warning("Failed to scrape %s", signal.url, exc_info=True)
                                continue

                        scrape_op.set_attribute("product_count", len(all_products))
                        scrape_op.set_attribute("output", json.dumps({
                            "total_products": len(all_products),
                        }))

                # Step 6: Aggregate sellers
                if all_products:
                    await self._add_status("Aggregating results...")
                    with operation_span(
                        _tracer, "aggregate_sellers",
                        input=json.dumps({"product_count": len(all_products)}),
                    ) as agg_op:
                        all_products = aggregate_sellers(all_products)
                        agg_op.set_attribute("output", json.dumps({
                            "deduplicated_count": len(all_products),
                        }))

                # Step 7: Validate results
                if all_products and criteria:
                    with operation_span(
                        _tracer, "validate_results",
                        input=json.dumps({"product_count": len(all_products)}),
                    ) as val_op:
                        validated = validate_results(all_products, criteria)
                        valid_products = [
                            v["product"] for v in validated if v["valid"]
                        ]
                        all_products = valid_products if valid_products else all_products
                        val_op.set_attribute("output", json.dumps({
                            "valid_count": len(valid_products),
                            "total_count": len(validated),
                        }))

                # Step 8: Format results (sort and cap)
                if all_products:
                    with operation_span(
                        _tracer, "format_results",
                        input=json.dumps({"product_count": len(all_products)}),
                    ) as fmt_op:
                        formatted = format_results(
                            all_products,
                            "single_product",
                            user_attributes=attributes if attributes else None,
                        )
                        all_products = [
                            ProductResult(**item["product"])
                            for item in formatted["products"]
                        ]
                        fmt_op.set_attribute("output", json.dumps({
                            "formatted_count": len(all_products),
                        }))

                self.state.results = all_products
                root_span.set_attribute("total_product_count", len(all_products))

                source_domains: set[str] = set()
                for p in all_products:
                    for s in p.sellers:
                        if s.url:
                            from urllib.parse import urlparse
                            host = urlparse(s.url).hostname or ""
                            if host.startswith("www."):
                                host = host[4:]
                            if host:
                                source_domains.add(host)
                        elif s.name:
                            source_domains.add(s.name)

                root_span.set_attribute("output", json.dumps({
                    "total_product_count": len(all_products),
                    "source_count": len(source_domains),
                }))
                await self._add_status(
                    f"Found {len(all_products)} products from {len(source_domains)} sites"
                )

            except Exception as exc:
                logger.error("Pipeline error for query '%s'", query, exc_info=True)
                root_span.set_status(trace.StatusCode.ERROR, str(exc))
                root_span.record_exception(exc)
                root_span.set_attribute("output", json.dumps({"error": str(exc)}))
                self.state.status = SearchStatus.FAILED
                await self._add_status("Search failed due to an error")
                return self.state

        self.state.status = SearchStatus.COMPLETED
        await self._add_status("Search complete")
        return self.state

    async def _process_multi_model(
        self,
        model_ids: list[str],
        language: str,
        market: str,
    ) -> AgentState:
        """Search for multiple model IDs in parallel for price comparison.

        Flow (per price_search.md):
        1. For each model, search via web + market aggregators in parallel
        2. Scrape all found URLs for prices and seller info
        3. Build seller→model→price map, find missing models per seller
        4. For sellers with missing models, search that seller's site directly
        5. Re-aggregate and return results with cross-seller bundles
        """
        locale = _build_locale(language, market)

        async def _scrape_urls(
            browser: object,
            urls: list[str],
            model_id: str,
        ) -> list[ProductResult]:
            """Scrape a list of URLs and return products relevant to model_id."""
            products: list[ProductResult] = []
            mid_lower = model_id.lower()
            for url in urls:
                try:
                    scraped = await scrape_page(
                        browser, url, model_id, locale=locale,
                    )
                    relevant = [
                        p for p in scraped
                        if mid_lower in p.name.lower()
                        or (p.model_id and mid_lower in p.model_id.lower())
                    ]
                    for p in relevant:
                        p.product_type = model_id
                    products.extend(relevant)
                except Exception:
                    logger.warning("Failed to scrape %s for model %s", url, model_id)
                    continue
            return products

        # Try to detect category from query context for aggregator filtering
        category = extract_category(self.state.query)

        # ------------------------------------------------------------------
        # Phase 1: Search for each model (web search + aggregators)
        # ------------------------------------------------------------------
        with operation_span(
            _tracer, "search_models",
            input=json.dumps(model_ids),
            model_count=len(model_ids),
        ) as search_op:
            async def search_single_model(model_id: str) -> list[str]:
                """Return a list of URLs to scrape for a given model ID."""
                await self._add_status(f"Searching for {model_id}...")
                results = await search_products(model_id, language, market)
                ecom_data = [
                    {"url": r.url, "title": r.title, "snippet": r.snippet}
                    for r in results
                ]
                ecom_signals = identify_ecommerce_sites(ecom_data)

                urls = [s.url for s in ecom_signals[:_MAX_SITES_TO_SCRAPE]]

                # Add aggregator direct URLs from DB (filtered by market + category)
                aggregator_entries = await get_aggregator_urls(model_id, market, category)
                seen_domains = {s.domain for s in ecom_signals[:_MAX_SITES_TO_SCRAPE]}
                for entry in aggregator_entries:
                    if entry["domain"] not in seen_domains:
                        urls.append(entry["url"])
                        seen_domains.add(entry["domain"])

                return urls

            # Discover new aggregators for this market+category (async, non-blocking)
            if category:
                asyncio.ensure_future(discover_aggregators(market, category))

            search_tasks = [search_single_model(mid) for mid in model_ids]
            urls_per_model = await asyncio.gather(*search_tasks, return_exceptions=True)
            search_op.set_attribute("output", json.dumps({
                "models_searched": len(model_ids),
                "urls_found": sum(
                    len(u) for u in urls_per_model if not isinstance(u, Exception)
                ),
            }))

        # ------------------------------------------------------------------
        # Phase 2: Scrape all URLs in parallel per model
        # ------------------------------------------------------------------
        await self._add_status("Scraping product pages...")
        all_products: list[ProductResult] = []

        async with get_browser() as browser:
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
                        scrape_tasks.append(_scrape_urls(browser, urls_result, mid))
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
                    await self._add_status(
                        f"Checking {len(missing)} sellers for {fill_count} missing models..."
                    )

                    with operation_span(
                        _tracer, "fill_missing_models",
                        input=json.dumps({
                            "sellers": len(missing),
                            "missing_count": fill_count,
                        }),
                    ) as fill_op:
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
                            filled = await _scrape_urls(browser, urls_to_scrape, mid)
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

        self.state.results = all_products
        self.state.cross_sellers = cross_sellers
        self.state.status = SearchStatus.COMPLETED

        cross_msg = ""
        if cross_sellers:
            cross_msg = f", {len(cross_sellers)} sellers carry multiple items"
        await self._add_status(
            f"Found {len(all_products)} products for {len(model_ids)} models{cross_msg}"
        )
        return self.state

    async def refine_search(self, refinement: str) -> AgentState:
        """Refine an existing search with additional criteria."""
        self.state.conversation_history.append(
            {"role": "user", "content": refinement}
        )
        # TODO: Implement refinement logic
        return self.state

    async def _add_status(self, message: str) -> None:
        self.state.status_messages.append(message)
        if self._status_callback:
            await self._status_callback(self.state.session_id, message)
