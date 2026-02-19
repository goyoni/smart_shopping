"""Product Criteria MCP Server implementation."""

from __future__ import annotations

import json

from mcp.server import Server
from mcp.types import Tool, TextContent

from src.mcp_servers.product_criteria_mcp.criteria import (
    get_criteria,
    merge_criteria,
    normalize_category,
    research_criteria,
)
from src.mcp_servers.product_criteria_mcp.db_cache import get_cached, save_cached

server = Server("product-criteria")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_cached_criteria",
            description="Get cached product criteria for a category",
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Product category"}
                },
                "required": ["category"],
            },
        ),
        Tool(
            name="research_criteria",
            description="Research important criteria for a product category via web",
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "language": {"type": "string", "default": "en"},
                },
                "required": ["category"],
            },
        ),
        Tool(
            name="merge_user_criteria",
            description="Merge user-specified criteria with general criteria for a category",
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "user_criteria": {
                        "type": "object",
                        "description": "User-specified criteria to merge",
                    },
                },
                "required": ["category", "user_criteria"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "get_cached_criteria":
        category = normalize_category(arguments["category"])

        # Try DB cache first
        cached = await get_cached(category)
        if cached is not None:
            return [TextContent(
                type="text",
                text=json.dumps({"criteria": cached, "status": "cached"}, ensure_ascii=False),
            )]

        # Fall back to pre-defined catalog
        criteria = get_criteria(category)
        if criteria:
            await save_cached(category, criteria)
            return [TextContent(
                type="text",
                text=json.dumps({"criteria": criteria, "status": "catalog"}, ensure_ascii=False),
            )]

        return [TextContent(
            type="text",
            text=json.dumps({"criteria": None, "status": "not_found"}),
        )]

    elif name == "research_criteria":
        category = normalize_category(arguments["category"])
        snippets = arguments.get("snippets", [])
        base = get_criteria(category)
        merged = research_criteria(snippets, base)
        if merged:
            await save_cached(category, merged)
        return [TextContent(
            type="text",
            text=json.dumps({"criteria": merged, "status": "researched"}, ensure_ascii=False),
        )]

    elif name == "merge_user_criteria":
        category = normalize_category(arguments["category"])
        user_criteria = arguments["user_criteria"]

        # Get base criteria (from cache or catalog)
        cached = await get_cached(category)
        base = cached if cached is not None else get_criteria(category)

        merged = merge_criteria(base, user_criteria)
        await save_cached(category, merged)
        return [TextContent(
            type="text",
            text=json.dumps({"merged": merged, "status": "merged"}, ensure_ascii=False),
        )]

    raise ValueError(f"Unknown tool: {name}")
