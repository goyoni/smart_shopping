"""Main orchestrator agent.

Manages conversation state, routes to specialized MCP servers,
and orchestrates the full search workflow.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from src.mcp_servers.web_search_mcp.ecommerce_detector import identify_ecommerce_sites
from src.mcp_servers.web_search_mcp.search import search_products
from src.mcp_servers.web_scraper_mcp.scraper import scrape_page
from src.shared.browser import get_browser
from src.shared.logging import get_logger, get_tracer, set_session_id
from src.shared.models import ProductResult, SearchStatus

from opentelemetry import trace
from opentelemetry.trace import StatusCode

logger = get_logger(__name__)
_tracer = get_tracer(__name__)

StatusCallback = Callable[[str, str], Awaitable[None]]

_MAX_SITES_TO_SCRAPE = 5


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
        1. Search Google for products
        2. Identify e-commerce sites from search results
        3. Scrape top e-commerce sites for product data
        4. Collect results
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
            try:
                async with get_browser() as browser:
                    # Step 1: Google search
                    await self._add_status("Searching the web...")
                    with _tracer.start_as_current_span(
                        "search_web",
                        attributes={"query": query, "language": language, "market": market},
                    ):
                        search_results = await search_products(browser, query, language, market)

                    if not search_results:
                        await self._add_status("No search results found")
                        self.state.status = SearchStatus.COMPLETED
                        return self.state

                    # Step 2: Identify e-commerce sites
                    await self._add_status(f"Analyzing {len(search_results)} results...")
                    with _tracer.start_as_current_span(
                        "detect_ecommerce",
                        attributes={"result_count": len(search_results)},
                    ):
                        urls_data = [
                            {"url": r.url, "title": r.title, "snippet": r.snippet}
                            for r in search_results
                        ]
                        ecommerce_signals = identify_ecommerce_sites(urls_data)

                    if not ecommerce_signals:
                        await self._add_status("No e-commerce sites found in results")
                        self.state.status = SearchStatus.COMPLETED
                        return self.state

                    # Step 3: Scrape top e-commerce sites
                    sites_to_scrape = ecommerce_signals[:_MAX_SITES_TO_SCRAPE]
                    await self._add_status(f"Scraping {len(sites_to_scrape)} e-commerce sites...")

                    with _tracer.start_as_current_span(
                        "scrape_sites",
                        attributes={"site_count": len(sites_to_scrape)},
                    ):
                        all_products: list[ProductResult] = []
                        for signal in sites_to_scrape:
                            try:
                                await self._add_status(f"Scraping {signal.domain}...")
                                products = await scrape_page(browser, signal.url, query)
                                all_products.extend(products)
                            except Exception as exc:
                                logger.warning("Failed to scrape %s", signal.url, exc_info=True)
                                trace.get_current_span().record_exception(exc)
                                continue

                        trace.get_current_span().set_attribute(
                            "product_count", len(all_products)
                        )

                    self.state.results = all_products
                    await self._add_status(f"Found {len(all_products)} products from {len(sites_to_scrape)} sites")

            except Exception as exc:
                logger.error("Pipeline error for query '%s'", query, exc_info=True)
                root_span.set_status(StatusCode.ERROR, str(exc))
                root_span.record_exception(exc)
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
