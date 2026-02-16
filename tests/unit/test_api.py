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
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_shopping_list_endpoint(client: AsyncClient):
    response = await client.get("/api/shopping-list")
    assert response.status_code == 200
    assert response.json() == {"items": []}
