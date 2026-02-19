"""Results Processor MCP Server implementation."""

from __future__ import annotations

import json

from mcp.server import Server
from mcp.types import Tool, TextContent

from src.mcp_servers.results_processor_mcp.processor import (
    aggregate_sellers,
    format_results,
    validate_results,
)
from src.shared.models import ProductResult

server = Server("results-processor")


def _deserialize_products(raw: list[dict]) -> list[ProductResult]:
    """Convert a list of raw dicts to ProductResult models."""
    return [ProductResult(**item) for item in raw]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="validate_results",
            description="Check that results meet the desired product criteria",
            inputSchema={
                "type": "object",
                "properties": {
                    "results": {"type": "array", "description": "List of product results"},
                    "criteria": {
                        "type": "object",
                        "description": "Required criteria to validate against",
                    },
                },
                "required": ["results", "criteria"],
            },
        ),
        Tool(
            name="aggregate_sellers",
            description="Find sellers offering multiple products for potential bulk discounts",
            inputSchema={
                "type": "object",
                "properties": {
                    "results": {
                        "type": "array",
                        "description": "List of product results with seller info",
                    }
                },
                "required": ["results"],
            },
        ),
        Tool(
            name="format_results",
            description="Format results for display to the user",
            inputSchema={
                "type": "object",
                "properties": {
                    "results": {"type": "array"},
                    "format_type": {
                        "type": "string",
                        "enum": [
                            "single_product",
                            "multi_product",
                            "price_comparison",
                            "matched_set",
                        ],
                    },
                },
                "required": ["results", "format_type"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "validate_results":
        products = _deserialize_products(arguments["results"])
        criteria = arguments.get("criteria", {})
        validated = validate_results(products, criteria)
        # Serialize: replace ProductResult with dict
        serializable = []
        for entry in validated:
            serializable.append({
                "product": entry["product"].model_dump(),
                "valid": entry["valid"],
                "completeness": entry["completeness"],
                "warnings": entry["warnings"],
            })
        return [TextContent(
            type="text",
            text=json.dumps(serializable, ensure_ascii=False),
        )]

    elif name == "aggregate_sellers":
        products = _deserialize_products(arguments["results"])
        aggregated = aggregate_sellers(products)
        return [TextContent(
            type="text",
            text=json.dumps(
                [p.model_dump() for p in aggregated],
                ensure_ascii=False,
            ),
        )]

    elif name == "format_results":
        products = _deserialize_products(arguments["results"])
        format_type = arguments.get("format_type", "single_product")
        formatted = format_results(products, format_type)
        return [TextContent(
            type="text",
            text=json.dumps(formatted, ensure_ascii=False),
        )]

    raise ValueError(f"Unknown tool: {name}")
