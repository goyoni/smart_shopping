"""Unit tests for backend API routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.agents.main_agent import AgentState
from src.backend.main import app
from src.shared.models import ProductResult, SearchStatus, Seller


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_search_endpoint(client: AsyncClient):
    mock_state = AgentState(session_id="mock-session")
    mock_state.status = SearchStatus.COMPLETED
    mock_state.results = [
        ProductResult(name="Test Product", model_id="TP-1", brand="Brand"),
    ]
    mock_state.status_messages = ["Search complete"]

    with patch("src.backend.api.routes.MainAgent") as MockAgent:
        instance = AsyncMock()
        instance.process_query.return_value = mock_state
        MockAgent.return_value = instance

        response = await client.post(
            "/api/search",
            json={"query": "black refrigerator", "language": "en"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    assert data["status"] == "completed"
    assert len(data["results"]) == 1
    assert data["results"][0]["name"] == "Test Product"


@pytest.mark.asyncio
async def test_search_passes_market(client: AsyncClient):
    mock_state = AgentState(session_id="mock-session")
    mock_state.status = SearchStatus.COMPLETED
    mock_state.status_messages = ["Done"]

    with patch("src.backend.api.routes.MainAgent") as MockAgent:
        instance = AsyncMock()
        instance.process_query.return_value = mock_state
        MockAgent.return_value = instance

        response = await client.post(
            "/api/search",
            json={"query": "laptop", "market": "il"},
        )

    assert response.status_code == 200
    instance.process_query.assert_awaited_once_with(
        "laptop", language="en", market="il",
    )


@pytest.mark.asyncio
async def test_search_auto_detects_market(client: AsyncClient):
    mock_state = AgentState(session_id="mock-session")
    mock_state.status = SearchStatus.COMPLETED
    mock_state.status_messages = ["Done"]

    with (
        patch("src.backend.api.routes.MainAgent") as MockAgent,
        patch("src.backend.api.routes.detect_market", return_value="il") as mock_detect,
    ):
        instance = AsyncMock()
        instance.process_query.return_value = mock_state
        MockAgent.return_value = instance

        response = await client.post(
            "/api/search",
            json={"query": "laptop"},
        )

    assert response.status_code == 200
    mock_detect.assert_called_once()
    instance.process_query.assert_awaited_once_with(
        "laptop", language="en", market="il",
    )


@pytest.mark.asyncio
async def test_search_generates_session_id(client: AsyncClient):
    mock_state = AgentState(session_id="will-be-replaced")
    mock_state.status = SearchStatus.COMPLETED
    mock_state.status_messages = ["Done"]

    with patch("src.backend.api.routes.MainAgent") as MockAgent:
        instance = AsyncMock()
        instance.process_query.return_value = mock_state
        MockAgent.return_value = instance

        response = await client.post(
            "/api/search",
            json={"query": "desk lamp"},
        )

    data = response.json()
    assert len(data["session_id"]) == 32  # uuid4().hex length


@pytest.mark.asyncio
async def test_search_uses_provided_session_id(client: AsyncClient):
    mock_state = AgentState(session_id="my-session-123")
    mock_state.status = SearchStatus.COMPLETED
    mock_state.status_messages = ["Done"]

    with patch("src.backend.api.routes.MainAgent") as MockAgent:
        instance = AsyncMock()
        instance.process_query.return_value = mock_state
        MockAgent.return_value = instance

        response = await client.post(
            "/api/search",
            json={"query": "desk lamp", "session_id": "my-session-123"},
        )

    data = response.json()
    assert data["session_id"] == "my-session-123"


@pytest.mark.asyncio
async def test_shopping_list_endpoint(client: AsyncClient):
    response = await client.get("/api/shopping-list")
    assert response.status_code == 200
    assert response.json() == {"items": []}
