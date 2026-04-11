"""SQLAlchemy database engine and session management."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.shared.config import settings

_connect_args: dict = {}
_pool_kwargs: dict = {}

# In-memory SQLite needs a shared connection pool so all sessions see the same DB
if (settings.database_url.startswith("sqlite") and ":memory:" in settings.database_url) or settings.database_url == "sqlite+aiosqlite://":
    _connect_args = {"check_same_thread": False}
    _pool_kwargs = {"poolclass": StaticPool}

engine = create_async_engine(
    settings.database_url,
    echo=(settings.env == "local"),
    connect_args=_connect_args,
    **_pool_kwargs,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:  # type: ignore[misc]
    async with async_session() as session:
        yield session


async def init_db() -> None:
    """Create all database tables and apply lightweight migrations."""
    from sqlalchemy import inspect, text

    from src.backend.db.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Add cross_sellers_json column if missing (migration for existing DBs)
        def _migrate(sync_conn):  # type: ignore[no-untyped-def]
            insp = inspect(sync_conn)
            if insp.has_table("search_history"):
                cols = {c["name"] for c in insp.get_columns("search_history")}
                if "cross_sellers_json" not in cols:
                    sync_conn.execute(
                        text("ALTER TABLE search_history ADD COLUMN cross_sellers_json TEXT")
                    )
            if insp.has_table("site_search_strategies"):
                cols = {c["name"] for c in insp.get_columns("site_search_strategies")}
                if "fail_count" not in cols:
                    sync_conn.execute(
                        text("ALTER TABLE site_search_strategies ADD COLUMN fail_count INTEGER DEFAULT 0")
                    )

        await conn.run_sync(_migrate)
