"""FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.backend.api.routes import router
from src.backend.db.engine import init_db
from src.backend.websocket.handler import websocket_router
from src.shared.logging import setup_logging, shutdown_tracing


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    await init_db()
    yield
    shutdown_tracing()


app = FastAPI(
    title="Smart Shopping Agent",
    description="Agentic shopping assistant with adaptive web discovery",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
app.include_router(websocket_router)

# Serve Next.js static export if it exists
_frontend_out = Path(__file__).resolve().parent.parent / "frontend" / "out"
if _frontend_out.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_out), html=True), name="frontend")
