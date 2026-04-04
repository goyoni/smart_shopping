"""Shared test configuration.

Sets DATABASE_URL to in-memory SQLite before any application modules are imported,
ensuring test isolation from the real database.
"""

from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"

import pytest  # noqa: E402

from src.backend.db.engine import init_db  # noqa: E402
from src.mcp_servers.web_search_mcp.ecommerce_detector import _reset_cache  # noqa: E402


@pytest.fixture(autouse=True)
async def _create_tables():
    """Create database tables for each test.

    Function-scoped because aiosqlite in-memory DBs don't survive
    across pytest-asyncio's per-function event loops.
    """
    _reset_cache()
    await init_db()
