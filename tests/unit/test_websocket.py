"""Unit tests for WebSocket handler."""

from __future__ import annotations

import pytest

from src.backend.websocket.handler import send_status


@pytest.mark.asyncio
async def test_send_status_no_connection():
    """send_status should not raise when there is no active connection."""
    await send_status("nonexistent-session", "hello")
