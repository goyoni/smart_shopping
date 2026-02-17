"""Shared test configuration.

Sets DATABASE_URL to in-memory SQLite before any application modules are imported,
ensuring test isolation from the real database.
"""

from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"

import pytest  # noqa: E402

from src.backend.db.engine import init_db  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
async def _create_tables():
    """Create database tables once for the entire test session."""
    await init_db()
