"""Tests for LLM-powered criteria discovery."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_servers.product_criteria_mcp.criteria import QueryAttribute
from src.mcp_servers.product_criteria_mcp.llm_criteria import (
    _strip_markdown_fences,
    _validate_criteria_structure,
    discover_criteria_via_llm,
    extract_query_attributes_via_llm,
)


# ---------------------------------------------------------------------------
# Helper: mock litellm response
# ---------------------------------------------------------------------------

def _make_llm_response(content: str) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    return response


# ---------------------------------------------------------------------------
# discover_criteria_via_llm
# ---------------------------------------------------------------------------

class TestDiscoverCriteriaViaLlm:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_api_key(self):
        with patch("src.mcp_servers.product_criteria_mcp.llm_criteria.settings") as mock_settings:
            mock_settings.llm_api_key = ""
            result = await discover_criteria_via_llm("folding kitchen table")
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_criteria_on_success(self):
        criteria_json = json.dumps({
            "price": {"display_name": "Price", "unit": "", "importance": "high", "description": ""},
            "dimensions": {"display_name": "Dimensions", "unit": "cm", "importance": "high", "description": "Folded and unfolded size"},
            "weight": {"display_name": "Weight", "unit": "kg", "importance": "medium", "description": "Table weight"},
            "material": {"display_name": "Material", "unit": "", "importance": "medium", "description": "Frame and top material"},
            "max_load": {"display_name": "Max Load", "unit": "kg", "importance": "medium", "description": "Maximum weight capacity"},
        })

        with (
            patch("src.mcp_servers.product_criteria_mcp.llm_criteria.settings") as mock_settings,
            patch("src.mcp_servers.product_criteria_mcp.llm_criteria.litellm") as mock_litellm,
        ):
            mock_settings.llm_api_key = "test-key"
            mock_settings.llm_model = "gpt-4o-mini"
            mock_settings.llm_temperature = 0.2
            mock_litellm.acompletion = AsyncMock(return_value=_make_llm_response(criteria_json))

            result = await discover_criteria_via_llm("folding kitchen table")

        assert len(result) == 5
        assert "price" in result
        assert "dimensions" in result
        assert result["material"]["display_name"] == "Material"

    @pytest.mark.asyncio
    async def test_handles_markdown_fences(self):
        criteria = {
            "price": {"display_name": "Price", "unit": "", "importance": "high", "description": ""},
            "size": {"display_name": "Size", "unit": "cm", "importance": "medium", "description": "Product dimensions"},
        }
        wrapped = f"```json\n{json.dumps(criteria)}\n```"

        with (
            patch("src.mcp_servers.product_criteria_mcp.llm_criteria.settings") as mock_settings,
            patch("src.mcp_servers.product_criteria_mcp.llm_criteria.litellm") as mock_litellm,
        ):
            mock_settings.llm_api_key = "test-key"
            mock_settings.llm_model = "gpt-4o-mini"
            mock_settings.llm_temperature = 0.2
            mock_litellm.acompletion = AsyncMock(return_value=_make_llm_response(wrapped))

            result = await discover_criteria_via_llm("some product")

        assert "price" in result
        assert "size" in result

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        with (
            patch("src.mcp_servers.product_criteria_mcp.llm_criteria.settings") as mock_settings,
            patch("src.mcp_servers.product_criteria_mcp.llm_criteria.litellm") as mock_litellm,
        ):
            mock_settings.llm_api_key = "test-key"
            mock_settings.llm_model = "gpt-4o-mini"
            mock_settings.llm_temperature = 0.2
            mock_litellm.acompletion = AsyncMock(side_effect=RuntimeError("API error"))

            result = await discover_criteria_via_llm("folding kitchen table")

        assert result == {}

    @pytest.mark.asyncio
    async def test_passes_snippets_in_prompt(self):
        criteria_json = json.dumps({
            "price": {"display_name": "Price", "unit": "", "importance": "high", "description": ""},
        })

        with (
            patch("src.mcp_servers.product_criteria_mcp.llm_criteria.settings") as mock_settings,
            patch("src.mcp_servers.product_criteria_mcp.llm_criteria.litellm") as mock_litellm,
        ):
            mock_settings.llm_api_key = "test-key"
            mock_settings.llm_model = "gpt-4o-mini"
            mock_settings.llm_temperature = 0.2
            mock_litellm.acompletion = AsyncMock(return_value=_make_llm_response(criteria_json))

            snippets = ["Great folding table for camping", "Lightweight aluminum frame"]
            await discover_criteria_via_llm("folding table", snippets=snippets)

        # Verify snippets were included in the messages
        call_kwargs = mock_litellm.acompletion.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
        user_msg = messages[-1]["content"]
        assert "Great folding table for camping" in user_msg
        assert "Lightweight aluminum frame" in user_msg

    @pytest.mark.asyncio
    async def test_ensures_price_criterion(self):
        """Even if LLM omits price, it should be added."""
        criteria_json = json.dumps({
            "weight": {"display_name": "Weight", "unit": "kg", "importance": "medium", "description": ""},
        })

        with (
            patch("src.mcp_servers.product_criteria_mcp.llm_criteria.settings") as mock_settings,
            patch("src.mcp_servers.product_criteria_mcp.llm_criteria.litellm") as mock_litellm,
        ):
            mock_settings.llm_api_key = "test-key"
            mock_settings.llm_model = "gpt-4o-mini"
            mock_settings.llm_temperature = 0.2
            mock_litellm.acompletion = AsyncMock(return_value=_make_llm_response(criteria_json))

            result = await discover_criteria_via_llm("some product")

        assert "price" in result


# ---------------------------------------------------------------------------
# extract_query_attributes_via_llm
# ---------------------------------------------------------------------------

class TestExtractQueryAttributesViaLlm:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_api_key(self):
        with patch("src.mcp_servers.product_criteria_mcp.llm_criteria.settings") as mock_settings:
            mock_settings.llm_api_key = ""
            result = await extract_query_attributes_via_llm(
                "lightweight folding table",
                {"weight": {"display_name": "Weight"}, "price": {"display_name": "Price"}},
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_extracts_attributes(self):
        attrs_json = json.dumps([
            {"criterion_key": "weight", "direction": "low", "display_label": "lightweight"},
            {"criterion_key": "price", "direction": "low", "display_label": "affordable"},
        ])

        criteria = {
            "weight": {"display_name": "Weight", "unit": "kg"},
            "price": {"display_name": "Price", "unit": ""},
            "size": {"display_name": "Size", "unit": "cm"},
        }

        with (
            patch("src.mcp_servers.product_criteria_mcp.llm_criteria.settings") as mock_settings,
            patch("src.mcp_servers.product_criteria_mcp.llm_criteria.litellm") as mock_litellm,
        ):
            mock_settings.llm_api_key = "test-key"
            mock_settings.llm_model = "gpt-4o-mini"
            mock_settings.llm_temperature = 0.2
            mock_litellm.acompletion = AsyncMock(return_value=_make_llm_response(attrs_json))

            result = await extract_query_attributes_via_llm("lightweight cheap table", criteria)

        assert len(result) == 2
        assert result[0].criterion_key == "weight"
        assert result[0].direction == "low"
        assert result[1].criterion_key == "price"

    @pytest.mark.asyncio
    async def test_ignores_unknown_criterion_keys(self):
        attrs_json = json.dumps([
            {"criterion_key": "weight", "direction": "low", "display_label": "lightweight"},
            {"criterion_key": "nonexistent_key", "direction": "high", "display_label": "unknown"},
        ])

        criteria = {
            "weight": {"display_name": "Weight", "unit": "kg"},
            "price": {"display_name": "Price", "unit": ""},
        }

        with (
            patch("src.mcp_servers.product_criteria_mcp.llm_criteria.settings") as mock_settings,
            patch("src.mcp_servers.product_criteria_mcp.llm_criteria.litellm") as mock_litellm,
        ):
            mock_settings.llm_api_key = "test-key"
            mock_settings.llm_model = "gpt-4o-mini"
            mock_settings.llm_temperature = 0.2
            mock_litellm.acompletion = AsyncMock(return_value=_make_llm_response(attrs_json))

            result = await extract_query_attributes_via_llm("lightweight table", criteria)

        assert len(result) == 1
        assert result[0].criterion_key == "weight"

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        with (
            patch("src.mcp_servers.product_criteria_mcp.llm_criteria.settings") as mock_settings,
            patch("src.mcp_servers.product_criteria_mcp.llm_criteria.litellm") as mock_litellm,
        ):
            mock_settings.llm_api_key = "test-key"
            mock_settings.llm_model = "gpt-4o-mini"
            mock_settings.llm_temperature = 0.2
            mock_litellm.acompletion = AsyncMock(side_effect=RuntimeError("API down"))

            result = await extract_query_attributes_via_llm(
                "table",
                {"price": {"display_name": "Price"}},
            )

        assert result == []


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

class TestStripMarkdownFences:
    def test_strips_json_fence(self):
        assert _strip_markdown_fences('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_strips_bare_fence(self):
        assert _strip_markdown_fences('```\n{"a": 1}\n```') == '{"a": 1}'

    def test_no_fences(self):
        assert _strip_markdown_fences('{"a": 1}') == '{"a": 1}'


class TestValidateCriteriaStructure:
    def test_valid(self):
        data = {"price": {"display_name": "Price", "unit": "", "importance": "high", "description": ""}}
        result = _validate_criteria_structure(data)
        assert result is not None
        assert "price" in result

    def test_missing_display_name(self):
        data = {"price": {"unit": ""}}
        result = _validate_criteria_structure(data)
        assert result is None

    def test_non_dict(self):
        assert _validate_criteria_structure([1, 2, 3]) is None
        assert _validate_criteria_structure("string") is None

    def test_invalid_importance_normalized(self):
        data = {"price": {"display_name": "Price", "importance": "critical"}}
        result = _validate_criteria_structure(data)
        assert result is not None
        assert result["price"]["importance"] == "medium"
