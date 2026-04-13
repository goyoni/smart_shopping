"""Main orchestrator agent.

Manages conversation state, routes to specialized MCP servers,
and orchestrates the full search workflow.
"""

from __future__ import annotations

import asyncio
import json

from opentelemetry import trace

from src.mcp_servers.product_criteria_mcp.criteria import (
    extract_query_attributes,
    get_criteria,
    normalize_category,
    research_criteria,
)
from src.mcp_servers.product_criteria_mcp.db_cache import get_cached, save_cached
from src.mcp_servers.product_criteria_mcp.llm_criteria import (
    discover_criteria_via_llm,
    extract_query_attributes_via_llm,
)
from src.mcp_servers.results_processor_mcp.processor import (
    aggregate_sellers,
    format_results,
    validate_results,
)
from src.mcp_servers.web_search_mcp.ecommerce_detector import identify_ecommerce_sites
from src.mcp_servers.web_search_mcp.search import (
    build_refined_query,
    search_products_via_searxng,
)
from src.mcp_servers.web_scraper_mcp.scraper import scrape_page
from src.shared.browser import get_browser
from src.shared.logging import agent_span, get_logger, get_tracer, operation_span, set_session_id
from src.shared.models import ProductResult, SearchStatus

from .multi_model import process_multi_model
from .query_utils import (
    AgentState,
    StatusCallback,
    _build_locale,
    _MAX_SITES_TO_SCRAPE,
    detect_model_ids,
    extract_category,
)

logger = get_logger(__name__)
_tracer = get_tracer(__name__)


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
                root_span.add_event("decision.query_type", {
                    "has_model_ids": bool(model_ids),
                    "model_ids": json.dumps(model_ids) if model_ids else "[]",
                    "summary": f"Query '{query}' -> {'model IDs: ' + ', '.join(model_ids) if model_ids else 'natural language search (no model IDs detected)'}",
                })
                if model_ids:
                    search_type = "multi_model" if len(model_ids) > 1 else "single_model_price"
                    root_span.set_attribute("search_type", search_type)
                    root_span.set_attribute("model_ids", json.dumps(model_ids))
                    await self._add_status(f"Searching prices for {len(model_ids)} model(s)...")
                    state = await process_multi_model(
                        self.state, model_ids, language, market, self._status_callback,
                    )
                    state.status = SearchStatus.COMPLETED
                    root_span.set_attribute("output", json.dumps({
                        "search_type": search_type,
                        "product_count": len(state.results),
                    }))
                    return state

                # Step 1: Extract product category
                category = extract_category(query)
                root_span.add_event("decision.category", {
                    "category": category or "none",
                    "summary": f"Category extraction: '{query}' -> {category or 'no category matched (will try LLM criteria discovery)'}",
                })
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
                        root_span.add_event("decision.criteria", {
                            "source": "cache",
                            "criteria_keys": json.dumps(list(criteria.keys())),
                            "summary": f"Criteria loaded from cache for '{cache_key}': {list(criteria.keys())}",
                        })
                    else:
                        criteria = await discover_criteria_via_llm(cache_key)
                        if criteria:
                            await save_cached(cache_key, criteria)
                            root_span.set_attribute("criteria_source", "llm")
                            root_span.add_event("decision.criteria", {
                                "source": "llm",
                                "criteria_keys": json.dumps(list(criteria.keys())),
                                "summary": f"Criteria discovered via LLM for '{cache_key}': {list(criteria.keys())}",
                            })
                        else:
                            root_span.add_event("decision.criteria", {
                                "source": "none",
                                "summary": f"No criteria found for '{cache_key}' (neither cache nor LLM returned results)",
                            })

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

                # Step 3: Web search (browser-based Google + DDG fallback)
                await self._add_status("Searching the web...")
                refined = None
                if attributes or category:
                    refined = build_refined_query(query, category, attributes)
                    root_span.add_event("decision.query_refinement", {
                        "original_query": query,
                        "refined_query": refined or query,
                        "has_category": bool(category),
                        "attribute_count": len(attributes),
                        "summary": f"Query refined: '{query}' -> '{refined}'" if refined != query else f"Query unchanged: '{query}'",
                    })

                # Open browser early -- used for both search and scraping
                async with get_browser() as browser:
                    with operation_span(
                        _tracer, "search_web",
                        input=query,
                        language=language, market=market,
                    ) as search_op:
                        if refined:
                            search_op.set_attribute("refined_query", refined)
                        search_results = await search_products_via_searxng(
                            query, language, market, refined_query=refined,
                        )
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
                        ecommerce_signals = await identify_ecommerce_sites(urls_data, market=market)
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
                        root_span.set_attribute("summary", f"Pipeline stopped: {len(search_results)} search results but 0 classified as ecommerce")
                        await self._add_status("No e-commerce sites found in results")
                        self.state.status = SearchStatus.COMPLETED
                        return self.state

                    # Step 5: Dedup by domain and scrape in parallel
                    seen_domains: dict[str, object] = {}
                    for sig in ecommerce_signals:
                        if sig.domain not in seen_domains:
                            seen_domains[sig.domain] = sig
                    sites_to_scrape = list(seen_domains.values())[:_MAX_SITES_TO_SCRAPE]

                    root_span.add_event("decision.sites_to_scrape", {
                        "total_ecommerce": len(ecommerce_signals),
                        "unique_domains": len(seen_domains),
                        "scraping_count": len(sites_to_scrape),
                        "sites": json.dumps([{"domain": s.domain, "confidence": s.confidence, "signals": s.signals} for s in sites_to_scrape], ensure_ascii=False),
                        "summary": f"Scraping {len(sites_to_scrape)} sites ({len(seen_domains)} unique domains from {len(ecommerce_signals)} URLs): {', '.join(s.domain for s in sites_to_scrape)}",
                    })
                    total_sites = len(sites_to_scrape)
                    await self._add_status(f"Scraping product pages (0/{total_sites})...")

                    locale = _build_locale(language, market)
                    _scrape_sem = asyncio.Semaphore(5)
                    _completed = 0
                    _completed_lock = asyncio.Lock()

                    async def _scrape_one(signal):
                        nonlocal _completed
                        async with _scrape_sem:
                            try:
                                with operation_span(
                                    _tracer, f"scrape_site:{signal.domain}",
                                    input=signal.url,
                                ) as site_op:
                                    products = await scrape_page(
                                        browser, signal.url, query,
                                        locale=locale,
                                        market=market,
                                        criteria=criteria if criteria else None,
                                    )
                                    if category:
                                        for p in products:
                                            if not p.category:
                                                p.category = category
                                    site_op.set_attribute("product_count", len(products))
                                    site_op.set_attribute("output", json.dumps({
                                        "url": signal.url,
                                        "product_count": len(products),
                                        "products": [
                                            {
                                                "name": p.name[:100],
                                                "model_id": p.model_id,
                                                "brand": p.brand,
                                                "price": p.sellers[0].price if p.sellers else None,
                                                "currency": p.sellers[0].currency if p.sellers else None,
                                                "seller_count": len(p.sellers),
                                            }
                                            for p in products[:20]
                                        ],
                                    }, ensure_ascii=False))
                                    return products
                            except Exception:
                                logger.warning("Failed to scrape %s", signal.url, exc_info=True)
                                return []
                            finally:
                                async with _completed_lock:
                                    _completed += 1
                                    await self._add_status(
                                        f"Scraping product pages ({_completed}/{total_sites})..."
                                    )

                    with operation_span(
                        _tracer, "scrape_sites",
                        input=json.dumps(
                            [{"domain": s.domain, "url": s.url} for s in sites_to_scrape],
                            ensure_ascii=False,
                        ),
                        site_count=len(sites_to_scrape),
                    ) as scrape_op:
                        results_lists = await asyncio.gather(
                            *[_scrape_one(sig) for sig in sites_to_scrape]
                        )
                        all_products: list[ProductResult] = []
                        for product_list in results_lists:
                            all_products.extend(product_list)

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

                # Step 7: Validate results (always run — drop null-price products)
                if all_products:
                    with operation_span(
                        _tracer, "validate_results",
                        input=json.dumps({"product_count": len(all_products)}),
                    ) as val_op:
                        validated = validate_results(
                            all_products, criteria if criteria else None,
                        )
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
