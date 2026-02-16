"""WebSocket handler for real-time status updates."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

websocket_router = APIRouter()

# Active WebSocket connections keyed by session_id
_connections: dict[str, WebSocket] = {}


@websocket_router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    _connections[session_id] = websocket
    try:
        while True:
            # Keep connection alive; client sends pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        _connections.pop(session_id, None)


async def send_status(session_id: str, message: str) -> None:
    ws = _connections.get(session_id)
    if ws:
        await ws.send_json({"type": "status", "message": message})
