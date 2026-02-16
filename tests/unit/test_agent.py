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
async def test_main_agent_refine_search():
    agent = MainAgent(session_id="test-123")
    await agent.process_query("refrigerator")
    state = await agent.refine_search("only black models")
    assert len(state.conversation_history) == 1
    assert state.conversation_history[0]["content"] == "only black models"
