"""API route definitions."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, HTTPException, Request

from sqlalchemy import select

from src.agents.main_agent import MainAgent
from src.backend.db.engine import async_session
from src.backend.db.models import ScrapingInstruction, SearchHistory, ShoppingListItem as ShoppingListItemDB
from src.backend.websocket.handler import send_status
from src.shared.geo import detect_market, get_client_ip
from src.shared.logging import set_session_id
from src.shared.models import (
    AddToShoppingListRequest,
    ProductResult,
    SearchHistoryItem,
    SearchHistoryResponse,
    SearchRequest,
    SearchResponse,
    SearchStatus,
    ShoppingListItem,
    ShoppingListResponse,
)

router = APIRouter()

_LANG_TO_MARKET: dict[str, str] = {"he": "il", "ar": "il", "de": "de", "fr": "fr"}


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest, raw_request: Request) -> SearchResponse:
    session_id = request.session_id or uuid.uuid4().hex
    set_session_id(session_id)

    # Derive market: explicit > GeoIP > language-based > config default
    # Language-to-market only maps languages strongly tied to one country.
    # English is excluded — it's spoken globally and not a location signal.
    market = request.market
    if not market:
        client_ip = get_client_ip(raw_request)
        market = detect_market(client_ip)
    if not market:
        market = _LANG_TO_MARKET.get(request.language)

    agent = MainAgent(session_id=session_id, status_callback=send_status)
    state = await agent.process_query(
        request.query, language=request.language, market=market,
    )

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


@router.get("/shopping-list", response_model=ShoppingListResponse)
async def get_shopping_list() -> ShoppingListResponse:
    async with async_session() as session:
        stmt = select(ShoppingListItemDB).order_by(ShoppingListItemDB.created_at.desc())
        result = await session.execute(stmt)
        rows = result.scalars().all()

    items: list[ShoppingListItem] = []
    for row in rows:
        try:
            product = ProductResult(**json.loads(row.product_json))
        except (json.JSONDecodeError, TypeError):
            continue
        items.append(
            ShoppingListItem(
                id=row.id,
                product=product,
                quantity=row.quantity,
                notes=row.notes,
            )
        )

    return ShoppingListResponse(items=items)


@router.post("/shopping-list", response_model=ShoppingListItem, status_code=201)
async def add_to_shopping_list(request: AddToShoppingListRequest) -> ShoppingListItem:
    async with async_session() as session:
        record = ShoppingListItemDB(
            product_json=json.dumps(request.product.model_dump()),
            quantity=request.quantity,
            notes=request.notes,
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)

    return ShoppingListItem(
        id=record.id,
        product=request.product,
        quantity=request.quantity,
        notes=request.notes,
    )


@router.delete("/shopping-list/{item_id}", status_code=204)
async def remove_from_shopping_list(item_id: int) -> None:
    async with async_session() as session:
        stmt = select(ShoppingListItemDB).where(ShoppingListItemDB.id == item_id)
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="Item not found")
        await session.delete(record)
        await session.commit()


@router.get("/history", response_model=SearchHistoryResponse)
async def get_history() -> SearchHistoryResponse:
    async with async_session() as session:
        stmt = select(SearchHistory).order_by(SearchHistory.created_at.desc()).limit(50)
        result = await session.execute(stmt)
        rows = result.scalars().all()

    items: list[SearchHistoryItem] = []
    for row in rows:
        result_count = 0
        if row.results_json:
            try:
                result_count = len(json.loads(row.results_json))
            except (json.JSONDecodeError, TypeError):
                pass
        items.append(
            SearchHistoryItem(
                session_id=row.session_id,
                query=row.query,
                status=row.status,
                result_count=result_count,
                created_at=row.created_at.isoformat(),
            )
        )

    return SearchHistoryResponse(items=items)


@router.get("/search/{session_id}", response_model=SearchResponse)
async def get_search_results(session_id: str) -> SearchResponse:
    """Retrieve saved search results by session_id."""
    async with async_session() as session:
        stmt = select(SearchHistory).where(SearchHistory.session_id == session_id)
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()

    if not record:
        raise HTTPException(status_code=404, detail="Search not found")

    results: list[ProductResult] = []
    if record.results_json:
        try:
            raw = json.loads(record.results_json)
            results = [ProductResult(**item) for item in raw]
        except (json.JSONDecodeError, TypeError):
            pass

    return SearchResponse(
        session_id=record.session_id,
        status=SearchStatus(record.status),
        results=results,
        status_message="",
    )


@router.get("/scraping/success-rates")
async def get_success_rates() -> dict[str, list]:
    """Return scraping success rates per domain."""
    async with async_session() as session:
        stmt = select(ScrapingInstruction).order_by(ScrapingInstruction.updated_at.desc())
        result = await session.execute(stmt)
        rows = result.scalars().all()

    rates = [
        {
            "domain": row.domain,
            "success_rate": round(row.success_rate, 2),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]
    return {"rates": rates}
