"""Unit tests for backend API routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.agents.main_agent import AgentState
from src.backend.main import app
from src.shared.config import settings
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
        "laptop", language=settings.default_language, market="il",
    )


@pytest.mark.asyncio
async def test_search_auto_detects_market(client: AsyncClient):
    mock_state = AgentState(session_id="mock-session")
    mock_state.status = SearchStatus.COMPLETED
    mock_state.status_messages = ["Done"]

    with (
        patch("src.backend.api.routes.MainAgent") as MockAgent,
        patch("src.backend.api.routes.detect_market", return_value="jp") as mock_detect,
    ):
        instance = AsyncMock()
        instance.process_query.return_value = mock_state
        MockAgent.return_value = instance

        # Use a language with no mapping so GeoIP fallback is exercised
        response = await client.post(
            "/api/search",
            json={"query": "laptop", "language": "ja"},
        )

    assert response.status_code == 200
    mock_detect.assert_called_once()
    instance.process_query.assert_awaited_once_with(
        "laptop", language="ja", market="jp",
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
async def test_shopping_list_empty(client: AsyncClient):
    response = await client.get("/api/shopping-list")
    assert response.status_code == 200
    assert response.json() == {"items": []}


@pytest.mark.asyncio
async def test_shopping_list_add_and_get(client: AsyncClient):
    product = {"name": "Test Fridge", "model_id": "TF-1", "brand": "CoolBrand"}
    add_response = await client.post(
        "/api/shopping-list",
        json={"product": product, "quantity": 2, "notes": "for kitchen"},
    )
    assert add_response.status_code == 201
    item = add_response.json()
    assert item["product"]["name"] == "Test Fridge"
    assert item["quantity"] == 2
    assert item["notes"] == "for kitchen"
    assert item["id"] is not None

    get_response = await client.get("/api/shopping-list")
    assert get_response.status_code == 200
    items = get_response.json()["items"]
    assert len(items) >= 1
    assert any(i["product"]["name"] == "Test Fridge" for i in items)


@pytest.mark.asyncio
async def test_shopping_list_delete(client: AsyncClient):
    product = {"name": "Delete Me", "model_id": "DM-1"}
    add_response = await client.post(
        "/api/shopping-list",
        json={"product": product},
    )
    item_id = add_response.json()["id"]

    del_response = await client.delete(f"/api/shopping-list/{item_id}")
    assert del_response.status_code == 204

    # Verify it's gone
    get_response = await client.get("/api/shopping-list")
    items = get_response.json()["items"]
    assert not any(i["id"] == item_id for i in items)


@pytest.mark.asyncio
async def test_shopping_list_delete_not_found(client: AsyncClient):
    response = await client.delete("/api/shopping-list/99999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/search/{session_id} — retrieve saved results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_search_results_found(client: AsyncClient):
    """POST a search, then GET by session_id returns saved results."""
    mock_state = AgentState(session_id="hist-sess-1")
    mock_state.status = SearchStatus.COMPLETED
    mock_state.results = [
        ProductResult(name="Saved Product", model_id="SP-1", brand="BrandX"),
    ]
    mock_state.status_messages = ["Done"]

    with patch("src.backend.api.routes.MainAgent") as MockAgent:
        instance = AsyncMock()
        instance.process_query.return_value = mock_state
        MockAgent.return_value = instance

        await client.post(
            "/api/search",
            json={"query": "test product", "session_id": "hist-sess-1", "language": "en", "market": "us"},
        )

    response = await client.get("/api/search/hist-sess-1")
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "hist-sess-1"
    assert data["status"] == "completed"
    assert len(data["results"]) == 1
    assert data["results"][0]["name"] == "Saved Product"


@pytest.mark.asyncio
async def test_get_search_results_not_found(client: AsyncClient):
    """GET a nonexistent session returns 404."""
    response = await client.get("/api/search/nonexistent-session-xyz")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_search_results_no_results(client: AsyncClient):
    """POST with empty results, GET returns empty list."""
    mock_state = AgentState(session_id="hist-empty-1")
    mock_state.status = SearchStatus.COMPLETED
    mock_state.results = []
    mock_state.status_messages = ["No results"]

    with patch("src.backend.api.routes.MainAgent") as MockAgent:
        instance = AsyncMock()
        instance.process_query.return_value = mock_state
        MockAgent.return_value = instance

        await client.post(
            "/api/search",
            json={"query": "nothing here", "session_id": "hist-empty-1", "language": "en", "market": "us"},
        )

    response = await client.get("/api/search/hist-empty-1")
    assert response.status_code == 200
    data = response.json()
    assert data["results"] == []


# ---------------------------------------------------------------------------
# Language-to-market derivation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_rates_endpoint(client: AsyncClient):
    response = await client.get("/api/scraping/success-rates")
    assert response.status_code == 200
    data = response.json()
    assert "rates" in data
    assert isinstance(data["rates"], list)


@pytest.mark.asyncio
async def test_search_derives_market_from_language(client: AsyncClient):
    """When GeoIP returns None, language='he' should derive market='il'."""
    mock_state = AgentState(session_id="lang-market-1")
    mock_state.status = SearchStatus.COMPLETED
    mock_state.status_messages = ["Done"]

    with (
        patch("src.backend.api.routes.MainAgent") as MockAgent,
        patch("src.backend.api.routes.detect_market", return_value=None) as mock_detect,
    ):
        instance = AsyncMock()
        instance.process_query.return_value = mock_state
        MockAgent.return_value = instance

        response = await client.post(
            "/api/search",
            json={"query": "folding table", "language": "he"},
        )

    assert response.status_code == 200
    # GeoIP was called but returned None, so language fallback kicks in
    mock_detect.assert_called_once()
    instance.process_query.assert_awaited_once_with(
        "folding table", language="he", market="il",
    )
