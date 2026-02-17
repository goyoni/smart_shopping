"""Unit tests for the main agent."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.agents.main_agent import AgentState, MainAgent
from src.mcp_servers.web_search_mcp.ecommerce_detector import EcommerceSignal
from src.mcp_servers.web_search_mcp.search import SearchResult
from src.shared.models import ProductResult, SearchStatus, Seller


def test_agent_state_defaults():
    state = AgentState(session_id="test-123")
    assert state.status == SearchStatus.PENDING
    assert state.results == []
    assert state.conversation_history == []


@pytest.mark.asyncio
async def test_main_agent_successful_pipeline():
    mock_browser = AsyncMock()

    mock_search_results = [
        SearchResult(url="https://www.amazon.com/dp/B123", title="Product A", snippet="Buy it"),
        SearchResult(url="https://www.youtube.com/watch", title="Review", snippet=""),
    ]

    mock_products = [
        ProductResult(
            name="Test Product",
            model_id="TP-001",
            brand="TestBrand",
            sellers=[Seller(name="amazon.com", price=29.99, currency="USD")],
        ),
    ]

    with (
        patch("src.agents.main_agent.get_browser") as mock_get_browser,
        patch("src.agents.main_agent.search_products", return_value=mock_search_results) as mock_search,
        patch("src.agents.main_agent.scrape_page", return_value=mock_products) as mock_scrape,
    ):
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_browser)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_browser.return_value = mock_ctx

        agent = MainAgent(session_id="test-pipeline")
        state = await agent.process_query("wireless headphones", language="en", market="us")

    assert state.status == SearchStatus.COMPLETED
    assert state.query == "wireless headphones"
    assert len(state.results) == 1
    assert state.results[0].name == "Test Product"
    mock_search.assert_awaited_once()
    mock_scrape.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_agent_no_search_results():
    mock_browser = AsyncMock()

    with (
        patch("src.agents.main_agent.get_browser") as mock_get_browser,
        patch("src.agents.main_agent.search_products", return_value=[]),
    ):
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_browser)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_browser.return_value = mock_ctx

        agent = MainAgent(session_id="test-empty")
        state = await agent.process_query("nonexistent product xyz")

    assert state.status == SearchStatus.COMPLETED
    assert state.results == []
    assert any("No search results" in m for m in state.status_messages)


@pytest.mark.asyncio
async def test_main_agent_no_ecommerce_sites():
    mock_browser = AsyncMock()

    # Return results but none are e-commerce
    mock_search_results = [
        SearchResult(url="https://www.youtube.com/watch?v=1", title="Review", snippet=""),
        SearchResult(url="https://www.reddit.com/r/gadgets", title="Discussion", snippet=""),
    ]

    with (
        patch("src.agents.main_agent.get_browser") as mock_get_browser,
        patch("src.agents.main_agent.search_products", return_value=mock_search_results),
    ):
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_browser)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_browser.return_value = mock_ctx

        agent = MainAgent(session_id="test-no-ecom")
        state = await agent.process_query("product review video")

    assert state.status == SearchStatus.COMPLETED
    assert state.results == []
    assert any("No e-commerce" in m for m in state.status_messages)


@pytest.mark.asyncio
async def test_main_agent_browser_error():
    with patch("src.agents.main_agent.get_browser") as mock_get_browser:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("Browser launch failed"))
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_browser.return_value = mock_ctx

        agent = MainAgent(session_id="test-error")
        state = await agent.process_query("test query")

    assert state.status == SearchStatus.FAILED
    assert any("failed" in m.lower() for m in state.status_messages)


@pytest.mark.asyncio
async def test_main_agent_scrape_site_error_continues():
    """One site failure should not crash the pipeline."""
    mock_browser = AsyncMock()

    mock_search_results = [
        SearchResult(url="https://www.amazon.com/dp/B1", title="Product A", snippet=""),
        SearchResult(url="https://www.ebay.com/itm/2", title="Product B", snippet=""),
    ]

    mock_products = [
        ProductResult(name="Ebay Product", sellers=[Seller(name="ebay.com", price=19.99)]),
    ]

    call_count = 0

    async def mock_scrape(browser, url, query):
        nonlocal call_count
        call_count += 1
        if "amazon.com" in url:
            raise RuntimeError("Amazon blocked us")
        return mock_products

    with (
        patch("src.agents.main_agent.get_browser") as mock_get_browser,
        patch("src.agents.main_agent.search_products", return_value=mock_search_results),
        patch("src.agents.main_agent.scrape_page", side_effect=mock_scrape),
    ):
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_browser)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_browser.return_value = mock_ctx

        agent = MainAgent(session_id="test-partial")
        state = await agent.process_query("laptop")

    assert state.status == SearchStatus.COMPLETED
    assert len(state.results) == 1
    assert state.results[0].name == "Ebay Product"


@pytest.mark.asyncio
async def test_main_agent_status_callback():
    received: list[tuple[str, str]] = []

    async def callback(session_id: str, message: str) -> None:
        received.append((session_id, message))

    mock_browser = AsyncMock()

    with (
        patch("src.agents.main_agent.get_browser") as mock_get_browser,
        patch("src.agents.main_agent.search_products", return_value=[]),
    ):
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_browser)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_browser.return_value = mock_ctx

        agent = MainAgent(session_id="cb-test", status_callback=callback)
        await agent.process_query("test query")

    assert len(received) >= 2
    assert received[0][0] == "cb-test"
    assert received[0][1] == "Started search..."


@pytest.mark.asyncio
async def test_main_agent_refine_search():
    mock_browser = AsyncMock()

    with (
        patch("src.agents.main_agent.get_browser") as mock_get_browser,
        patch("src.agents.main_agent.search_products", return_value=[]),
    ):
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_browser)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_browser.return_value = mock_ctx

        agent = MainAgent(session_id="test-123")
        await agent.process_query("refrigerator")

    state = await agent.refine_search("only black models")
    assert len(state.conversation_history) == 1
    assert state.conversation_history[0]["content"] == "only black models"
