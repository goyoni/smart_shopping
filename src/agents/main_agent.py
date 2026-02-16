"""Main orchestrator agent.

Manages conversation state, routes to specialized MCP servers,
and orchestrates the full search workflow.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.shared.models import ProductResult, SearchStatus


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

    def __init__(self, session_id: str) -> None:
        self.state = AgentState(session_id=session_id)

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
        self._add_status("Started search...")

        # TODO: Implement the full agentic workflow
        self.state.status = SearchStatus.COMPLETED
        self._add_status("Search complete (no results yet - not implemented)")
        return self.state

    async def refine_search(self, refinement: str) -> AgentState:
        """Refine an existing search with additional criteria."""
        self.state.conversation_history.append(
            {"role": "user", "content": refinement}
        )
        # TODO: Implement refinement logic
        return self.state

    def _add_status(self, message: str) -> None:
        self.state.status_messages.append(message)
