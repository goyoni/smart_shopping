"""Main orchestrator agent.

Manages conversation state, routes to specialized MCP servers,
and orchestrates the full search workflow.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from src.mcp_servers.product_criteria_mcp.criteria import (
    get_criteria,
    normalize_category,
    research_criteria,
)
from src.mcp_servers.results_processor_mcp.processor import (
    aggregate_sellers,
    format_results,
    validate_results,
)
from src.mcp_servers.web_search_mcp.ecommerce_detector import identify_ecommerce_sites
from src.mcp_servers.web_search_mcp.search import search_products
from src.mcp_servers.web_scraper_mcp.scraper import scrape_page
from src.shared.browser import get_browser
from src.shared.logging import get_logger, get_tracer, set_session_id
from src.shared.models import ProductResult, SearchStatus

import json

from opentelemetry import trace
from opentelemetry.trace import StatusCode

logger = get_logger(__name__)
_tracer = get_tracer(__name__)

StatusCallback = Callable[[str, str], Awaitable[None]]

_MAX_SITES_TO_SCRAPE = 5

# ---------------------------------------------------------------------------
# Category extraction from query
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS: dict[str, str] = {
    # English
    "refrigerator": "refrigerator",
    "fridge": "refrigerator",
    "microwave": "microwave",
    "oven": "oven",
    "stove": "stove",
    "cooktop": "stove",
    "washing machine": "washing_machine",
    "washer": "washing_machine",
    "dryer": "dryer",
    "dishwasher": "dishwasher",
    "television": "tv",
    "tv": "tv",
    "laptop": "laptop",
    "notebook": "laptop",
    "headphone": "headphones",
    "headphones": "headphones",
    "earbuds": "headphones",
    "air conditioner": "air_conditioner",
    "ac unit": "air_conditioner",
    "vacuum": "vacuum",
    "vacuum cleaner": "vacuum",
    # Hebrew
    "מקרר": "refrigerator",
    "מיקרוגל": "microwave",
    "תנור": "oven",
    "כיריים": "stove",
    "מכונת כביסה": "washing_machine",
    "מייבש": "dryer",
    "מדיח כלים": "dishwasher",
    "מדיח": "dishwasher",
    "טלוויזיה": "tv",
    "מחשב נייד": "laptop",
    "אוזניות": "headphones",
    "מזגן": "air_conditioner",
    "שואב אבק": "vacuum",
    # Arabic
    "ثلاجة": "refrigerator",
    "ميكروويف": "microwave",
    "فرن": "oven",
    "غسالة": "washing_machine",
    "غسالة صحون": "dishwasher",
    "تلفزيون": "tv",
    "حاسوب محمول": "laptop",
    "سماعات": "headphones",
    "مكيف": "air_conditioner",
    "مكنسة كهربائية": "vacuum",
}


def extract_category(query: str) -> str | None:
    """Extract a product category from a search query using keyword matching.

    Returns the canonical category key or None if no category is detected.
    Checks longer keywords first to handle multi-word matches.
    """
    text = query.lower().strip()
    for keyword, category in sorted(_CATEGORY_KEYWORDS.items(), key=lambda x: -len(x[0])):
        if keyword in text:
            return category
    return None


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

        with _tracer.start_as_current_span(
            "process_query",
            attributes={"query": query, "market": market},
        ) as root_span:
            root_span.add_event("process_query.start", {"query": query, "market": market})
            try:
                    # Step 1: Extract product category
                    category = extract_category(query)
                    if category:
                        root_span.set_attribute("category", category)

                    # Step 2: Get criteria for the category
                    criteria: dict[str, dict] = {}
                    if category:
                        await self._add_status(f"Looking up criteria for {category}...")
                        criteria = get_criteria(category)

                    # Step 3: Web search (direct HTTP — no browser needed)
                    await self._add_status("Searching the web...")
                    with _tracer.start_as_current_span(
                        "search_web",
                        attributes={"query": query, "language": language, "market": market},
                    ) as search_span:
                        search_span.add_event("search_web.start", {"query": query, "language": language, "market": market})
                        search_results = await search_products(query, language, market)
                        search_span.set_attribute("result_count", len(search_results))
                        if search_results:
                            search_span.set_attribute(
                                "output",
                                json.dumps(
                                    [{"url": r.url, "title": r.title} for r in search_results],
                                    ensure_ascii=False,
                                ),
                            )
                        search_span.add_event("search_web.end", {"result_count": len(search_results)})

                    # Enrich criteria from search snippets
                    if category and search_results:
                        snippets = [r.snippet for r in search_results if r.snippet]
                        if snippets:
                            criteria = research_criteria(snippets, criteria)

                    if not search_results:
                        root_span.set_attribute("exit_reason", "no_search_results")
                        root_span.add_event("process_query.end", {"exit_reason": "no_search_results"})
                        await self._add_status("No search results found")
                        self.state.status = SearchStatus.COMPLETED
                        return self.state

                    # Step 4: Identify e-commerce sites
                    await self._add_status(f"Analyzing {len(search_results)} results...")
                    with _tracer.start_as_current_span(
                        "detect_ecommerce",
                        attributes={"result_count": len(search_results)},
                    ) as ecom_span:
                        ecom_span.add_event("detect_ecommerce.start", {"result_count": len(search_results)})
                        urls_data = [
                            {"url": r.url, "title": r.title, "snippet": r.snippet}
                            for r in search_results
                        ]
                        ecommerce_signals = identify_ecommerce_sites(urls_data)
                        ecom_span.set_attribute("ecommerce_count", len(ecommerce_signals))
                        if ecommerce_signals:
                            ecom_span.set_attribute(
                                "output",
                                json.dumps(
                                    [
                                        {"domain": s.domain, "url": s.url, "confidence": s.confidence}
                                        for s in ecommerce_signals
                                    ],
                                    ensure_ascii=False,
                                ),
                            )
                        ecom_span.add_event("detect_ecommerce.end", {"ecommerce_count": len(ecommerce_signals)})

                    if not ecommerce_signals:
                        root_span.set_attribute("exit_reason", "no_ecommerce_sites")
                        root_span.add_event("process_query.end", {"exit_reason": "no_ecommerce_sites"})
                        await self._add_status("No e-commerce sites found in results")
                        self.state.status = SearchStatus.COMPLETED
                        return self.state

                    # Step 5: Scrape top e-commerce sites (browser needed here)
                    sites_to_scrape = ecommerce_signals[:_MAX_SITES_TO_SCRAPE]
                    await self._add_status(f"Scraping {len(sites_to_scrape)} e-commerce sites...")

                    locale = _build_locale(language, market)

                    async with get_browser() as browser:
                        with _tracer.start_as_current_span(
                            "scrape_sites",
                            attributes={
                                "site_count": len(sites_to_scrape),
                                "input": json.dumps(
                                    [{"domain": s.domain, "url": s.url} for s in sites_to_scrape],
                                    ensure_ascii=False,
                                ),
                            },
                        ) as scrape_span:
                            scrape_span.add_event("scrape_sites.start", {"site_count": len(sites_to_scrape)})
                            all_products: list[ProductResult] = []
                            for signal in sites_to_scrape:
                                try:
                                    await self._add_status(f"Scraping {signal.domain}...")
                                    products = await scrape_page(
                                        browser, signal.url, query, locale=locale,
                                    )
                                    # Tag products with category
                                    if category:
                                        for p in products:
                                            if not p.category:
                                                p.category = category
                                    all_products.extend(products)
                                    scrape_span.add_event(
                                        signal.domain,
                                        attributes={
                                            "url": signal.url,
                                            "product_count": len(products),
                                        },
                                    )
                                except Exception as exc:
                                    logger.warning("Failed to scrape %s", signal.url, exc_info=True)
                                    trace.get_current_span().record_exception(exc)
                                    continue

                            scrape_span.set_attribute(
                                "product_count", len(all_products)
                            )
                            scrape_span.add_event("scrape_sites.end", {"product_count": len(all_products)})

                    # Step 6: Aggregate sellers
                    if all_products:
                        await self._add_status("Aggregating results...")
                        all_products = aggregate_sellers(all_products)

                    # Step 7: Validate results
                    if all_products and criteria:
                        validated = validate_results(all_products, criteria)
                        # Keep only valid products, sorted by completeness
                        valid_products = [
                            v["product"] for v in validated if v["valid"]
                        ]
                        all_products = valid_products if valid_products else all_products

                    # Step 8: Format results (sort and cap)
                    if all_products:
                        formatted = format_results(all_products, "single_product")
                        # Extract sorted products from formatted output
                        all_products = [
                            ProductResult(**item["product"])
                            for item in formatted["products"]
                        ]

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

                    root_span.add_event("process_query.end", {"total_product_count": len(all_products)})
                    await self._add_status(
                        f"Found {len(all_products)} products from {len(source_domains)} sites"
                    )

            except Exception as exc:
                logger.error("Pipeline error for query '%s'", query, exc_info=True)
                root_span.set_status(StatusCode.ERROR, str(exc))
                root_span.record_exception(exc)
                root_span.add_event("process_query.end", {"error": str(exc)})
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
