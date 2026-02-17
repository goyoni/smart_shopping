"""API route definitions."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter

from src.agents.main_agent import MainAgent
from src.backend.db.engine import async_session
from src.backend.db.models import SearchHistory
from src.backend.websocket.handler import send_status
from src.shared.models import SearchRequest, SearchResponse

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> SearchResponse:
    session_id = request.session_id or uuid.uuid4().hex

    agent = MainAgent(session_id=session_id, status_callback=send_status)
    state = await agent.process_query(request.query, language=request.language)

    async with async_session() as session:
        record = SearchHistory(
            session_id=session_id,
            query=request.query,
            status=state.status.value,
            results_json=json.dumps([r.model_dump() for r in state.results]),
            language=request.language,
        )
        session.add(record)
        await session.commit()

    return SearchResponse(
        session_id=session_id,
        status=state.status,
        results=state.results,
        status_message=state.status_messages[-1] if state.status_messages else "",
    )


@router.get("/shopping-list")
async def get_shopping_list() -> dict[str, list]:
    # TODO: Implement shopping list retrieval
    return {"items": []}
