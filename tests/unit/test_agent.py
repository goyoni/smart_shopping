"""Unit tests for the main agent."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.agents.main_agent import AgentState, MainAgent, extract_category, _build_locale
from src.mcp_servers.web_search_mcp.search import SearchResult
from src.shared.models import ProductResult, SearchStatus, Seller


def test_agent_state_defaults():
    state = AgentState(session_id="test-123")
    assert state.status == SearchStatus.PENDING
    assert state.results == []
    assert state.conversation_history == []


# ---------------------------------------------------------------------------
# Category extraction
# ---------------------------------------------------------------------------

class TestExtractCategory:
    def test_english(self):
        assert extract_category("quiet refrigerator") == "refrigerator"

    def test_english_alias(self):
        assert extract_category("best fridge 2025") == "refrigerator"

    def test_multi_word(self):
        assert extract_category("buy washing machine") == "washing_machine"

    def test_hebrew(self):
        assert extract_category("מקרר שקט") == "refrigerator"

    def test_no_match(self):
        assert extract_category("best deal on shoes") is None

    def test_case_insensitive(self):
        assert extract_category("Best LAPTOP for students") == "laptop"


class TestBuildLocale:
    def test_english_us(self):
        assert _build_locale("en", "us") == "en-US"

    def test_hebrew_il(self):
        assert _build_locale("he", "il") == "he-IL"


# ---------------------------------------------------------------------------
# Pipeline tests
# ---------------------------------------------------------------------------

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
    assert len(state.results) >= 1
    assert state.results[0].name == "Test Product"
    mock_search.assert_awaited_once()
    mock_scrape.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_agent_no_search_results():
    with patch("src.agents.main_agent.search_products", return_value=[]):
        agent = MainAgent(session_id="test-empty")
        state = await agent.process_query("nonexistent product xyz")

    assert state.status == SearchStatus.COMPLETED
    assert state.results == []
    assert any("No search results" in m for m in state.status_messages)


@pytest.mark.asyncio
async def test_main_agent_no_ecommerce_sites():
    # Return results but none are e-commerce
    mock_search_results = [
        SearchResult(url="https://www.youtube.com/watch?v=1", title="Review", snippet=""),
        SearchResult(url="https://www.reddit.com/r/gadgets", title="Discussion", snippet=""),
    ]

    with patch("src.agents.main_agent.search_products", return_value=mock_search_results):
        agent = MainAgent(session_id="test-no-ecom")
        state = await agent.process_query("product review video")

    assert state.status == SearchStatus.COMPLETED
    assert state.results == []
    assert any("No e-commerce" in m for m in state.status_messages)


@pytest.mark.asyncio
async def test_main_agent_browser_error():
    mock_search_results = [
        SearchResult(url="https://www.amazon.com/dp/B1", title="Product A", snippet=""),
    ]

    with (
        patch("src.agents.main_agent.search_products", return_value=mock_search_results),
        patch("src.agents.main_agent.get_browser") as mock_get_browser,
    ):
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

    async def mock_scrape(browser, url, query, *, locale="en-US", criteria=None):
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

    with patch("src.agents.main_agent.search_products", return_value=[]):
        agent = MainAgent(session_id="cb-test", status_callback=callback)
        await agent.process_query("test query")

    assert len(received) >= 2
    assert received[0][0] == "cb-test"
    assert received[0][1] == "Started search..."


@pytest.mark.asyncio
async def test_main_agent_refine_search():
    with patch("src.agents.main_agent.search_products", return_value=[]):
        agent = MainAgent(session_id="test-123")
        await agent.process_query("refrigerator")

    state = await agent.refine_search("only black models")
    assert len(state.conversation_history) == 1
    assert state.conversation_history[0]["content"] == "only black models"


@pytest.mark.asyncio
async def test_pipeline_calls_aggregate_and_format():
    """Verify that the pipeline runs aggregation and formatting."""
    mock_browser = AsyncMock()

    mock_search_results = [
        SearchResult(url="https://www.amazon.com/dp/B1", title="Fridge", snippet="quiet fridge"),
    ]

    mock_products = [
        ProductResult(
            name="Test Fridge",
            model_id="TF-001",
            brand="CoolBrand",
            sellers=[Seller(name="amazon.com", price=599.99, currency="USD", url="https://www.amazon.com/dp/B1")],
        ),
    ]

    with (
        patch("src.agents.main_agent.get_browser") as mock_get_browser,
        patch("src.agents.main_agent.search_products", return_value=mock_search_results),
        patch("src.agents.main_agent.scrape_page", return_value=mock_products),
        patch("src.agents.main_agent.aggregate_sellers", wraps=lambda x: x) as mock_agg,
        patch("src.agents.main_agent.format_results") as mock_fmt,
    ):
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_browser)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_browser.return_value = mock_ctx

        mock_fmt.return_value = {
            "products": [{"product": mock_products[0].model_dump()}],
            "total_count": 1,
            "displayed_count": 1,
            "source_count": 1,
            "format_type": "single_product",
        }

        agent = MainAgent(session_id="test-agg-fmt")
        state = await agent.process_query("quiet refrigerator", language="en", market="us")

    assert state.status == SearchStatus.COMPLETED
    mock_agg.assert_called_once()
    mock_fmt.assert_called_once()


@pytest.mark.asyncio
async def test_pipeline_passes_locale_to_scraper():
    """Verify locale is passed through to scrape_page."""
    mock_browser = AsyncMock()

    mock_search_results = [
        SearchResult(url="https://www.amazon.com/dp/B1", title="Product", snippet=""),
    ]

    scrape_kwargs: dict = {}

    async def capture_scrape(browser, url, query, *, locale="en-US", criteria=None):
        scrape_kwargs["locale"] = locale
        return []

    with (
        patch("src.agents.main_agent.get_browser") as mock_get_browser,
        patch("src.agents.main_agent.search_products", return_value=mock_search_results),
        patch("src.agents.main_agent.scrape_page", side_effect=capture_scrape),
    ):
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_browser)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_browser.return_value = mock_ctx

        agent = MainAgent(session_id="test-locale")
        await agent.process_query("מקרר", language="he", market="il")

    assert scrape_kwargs.get("locale") == "he-IL"


@pytest.mark.asyncio
async def test_pipeline_extracts_category():
    """Verify category is extracted and criteria are looked up."""
    with patch("src.agents.main_agent.search_products", return_value=[]):
        agent = MainAgent(session_id="test-cat")
        state = await agent.process_query("quiet refrigerator")

    # Should have the criteria lookup status message
    assert any("criteria" in m.lower() for m in state.status_messages)


@pytest.mark.asyncio
async def test_pipeline_passes_criteria_to_scraper():
    """Verify criteria dict is forwarded to scrape_page for known categories."""
    mock_browser = AsyncMock()

    mock_search_results = [
        SearchResult(url="https://www.amazon.com/dp/B1", title="Fridge", snippet="quiet fridge"),
    ]

    scrape_kwargs: dict = {}

    async def capture_scrape(browser, url, query, *, locale="en-US", criteria=None):
        scrape_kwargs["criteria"] = criteria
        return []

    with (
        patch("src.agents.main_agent.get_browser") as mock_get_browser,
        patch("src.agents.main_agent.search_products", return_value=mock_search_results),
        patch("src.agents.main_agent.scrape_page", side_effect=capture_scrape),
    ):
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_browser)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_browser.return_value = mock_ctx

        agent = MainAgent(session_id="test-criteria-pass")
        await agent.process_query("quiet refrigerator", language="en", market="us")

    # "refrigerator" is a known category, so criteria should be non-None
    assert scrape_kwargs.get("criteria") is not None
    assert isinstance(scrape_kwargs["criteria"], dict)
