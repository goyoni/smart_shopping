"""Main orchestrator agent.

Manages conversation state, routes to specialized MCP servers,
and orchestrates the full search workflow.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from src.shared.models import ProductResult, SearchStatus

StatusCallback = Callable[[str, str], Awaitable[None]]


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

    async def process_query(self, query: str, language: str = "en") -> AgentState:
        """Process a user search query through the full pipeline.

        Workflow:
        1. Validate input (IO Validator MCP)
        2. Determine product criteria (Product Criteria MCP)
        3. Search for products (Web Search MCP)
        4. Scrape results (Web Scraper MCP)
        5. Process and format results (Results Processor MCP)
        6. Validate output (IO Validator MCP)
        """
        self.state.query = query
        self.state.language = language
        self.state.status = SearchStatus.IN_PROGRESS
        await self._add_status("Started search...")

        # Echo result for Phase 1 — will be replaced by full agentic workflow
        self.state.results = [
            ProductResult(name=f"Echo: {query}", model="echo-v1"),
        ]
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
