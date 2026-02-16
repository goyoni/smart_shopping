"""API route definitions."""

from __future__ import annotations

from fastapi import APIRouter

from src.shared.models import SearchRequest, SearchResponse, SearchStatus

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> SearchResponse:
    # TODO: Wire up to main agent
    return SearchResponse(
        session_id=request.session_id or "placeholder",
        status=SearchStatus.PENDING,
        status_message="Search initiated",
    )


@router.get("/shopping-list")
async def get_shopping_list() -> dict[str, list]:
    # TODO: Implement shopping list retrieval
    return {"items": []}
