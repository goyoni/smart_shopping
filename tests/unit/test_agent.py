"""Unit tests for the main agent."""

from __future__ import annotations

import pytest

from src.agents.main_agent import AgentState, MainAgent
from src.shared.models import SearchStatus


def test_agent_state_defaults():
    state = AgentState(session_id="test-123")
    assert state.status == SearchStatus.PENDING
    assert state.results == []
    assert state.conversation_history == []


@pytest.mark.asyncio
async def test_main_agent_process_query():
    agent = MainAgent(session_id="test-123")
    state = await agent.process_query("black refrigerator", language="en")
    assert state.query == "black refrigerator"
    assert state.language == "en"
    assert state.status == SearchStatus.COMPLETED
    assert len(state.status_messages) > 0


@pytest.mark.asyncio
async def test_main_agent_echo_result():
    agent = MainAgent(session_id="test-456")
    state = await agent.process_query("wireless headphones")
    assert len(state.results) == 1
    assert state.results[0].name == "Echo: wireless headphones"
    assert state.results[0].model == "echo-v1"


@pytest.mark.asyncio
async def test_main_agent_status_callback():
    received: list[tuple[str, str]] = []

    async def callback(session_id: str, message: str) -> None:
        received.append((session_id, message))

    agent = MainAgent(session_id="cb-test", status_callback=callback)
    await agent.process_query("test query")
    assert len(received) == 2
    assert received[0] == ("cb-test", "Started search...")
    assert received[1] == ("cb-test", "Search complete")


@pytest.mark.asyncio
async def test_main_agent_refine_search():
    agent = MainAgent(session_id="test-123")
    await agent.process_query("refrigerator")
    state = await agent.refine_search("only black models")
    assert len(state.conversation_history) == 1
    assert state.conversation_history[0]["content"] == "only black models"
