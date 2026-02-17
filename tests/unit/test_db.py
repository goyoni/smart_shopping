"""Unit tests for database initialization and models."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from src.backend.db.engine import async_session, init_db
from src.backend.db.models import SearchHistory


@pytest.mark.asyncio
async def test_init_db_creates_tables():
    await init_db()
    # Verify the table exists by querying it without error
    async with async_session() as session:
        result = await session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='search_history'")
        )
        assert result.scalar_one() == "search_history"


@pytest.mark.asyncio
async def test_insert_search_history():
    await init_db()
    async with async_session() as session:
        record = SearchHistory(
            session_id="db-test-1",
            query="test product",
            status="completed",
            results_json="[]",
            language="en",
        )
        session.add(record)
        await session.commit()

    async with async_session() as session:
        result = await session.execute(
            select(SearchHistory).where(SearchHistory.session_id == "db-test-1")
        )
        row = result.scalar_one()
        assert row.query == "test product"
        assert row.status == "completed"
