"""Unit tests for backend API routes."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.backend.main import app


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
    response = await client.post(
        "/api/search",
        json={"query": "black refrigerator", "language": "en"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    assert data["status"] == "completed"


@pytest.mark.asyncio
async def test_search_returns_echo_result(client: AsyncClient):
    response = await client.post(
        "/api/search",
        json={"query": "blue sofa"},
    )
    data = response.json()
    assert len(data["results"]) == 1
    assert data["results"][0]["name"] == "Echo: blue sofa"
    assert data["results"][0]["model"] == "echo-v1"


@pytest.mark.asyncio
async def test_search_generates_session_id(client: AsyncClient):
    response = await client.post(
        "/api/search",
        json={"query": "desk lamp"},
    )
    data = response.json()
    assert len(data["session_id"]) == 32  # uuid4().hex length


@pytest.mark.asyncio
async def test_search_uses_provided_session_id(client: AsyncClient):
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
