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
    """Create all database tables."""
    from src.backend.db.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
