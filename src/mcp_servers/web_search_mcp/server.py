"""Web Search MCP Server implementation."""

from __future__ import annotations

import json

from mcp.server import Server
from mcp.types import TextContent, Tool

from src.mcp_servers.web_search_mcp.ecommerce_detector import identify_ecommerce_sites
from src.mcp_servers.web_search_mcp.search import search_products

server = Server("web-search")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_products",
            description="Search for products across the web and return relevant e-commerce URLs",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Product search query"},
                    "language": {"type": "string", "default": "en"},
                    "market": {"type": "string", "default": "us"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="identify_ecommerce_sites",
            description="Identify which URLs are e-commerce sites from a list of URLs",
            inputSchema={
                "type": "object",
                "properties": {
                    "urls": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "List of URL objects with url, title, snippet fields",
                    }
                },
                "required": ["urls"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "search_products":
        query = arguments["query"]
        language = arguments.get("language", "en")
        market = arguments.get("market", "us")
        results = await search_products(query, language, market)
        urls_data = [
            {"url": r.url, "title": r.title, "snippet": r.snippet}
            for r in results
        ]
        return [TextContent(type="text", text=json.dumps({"urls": urls_data, "status": "ok"}))]

    elif name == "identify_ecommerce_sites":
        urls_data = arguments["urls"]
        signals = identify_ecommerce_sites(urls_data)
        ecommerce_urls = [
            {"url": s.url, "domain": s.domain, "confidence": s.confidence, "signals": s.signals}
            for s in signals
        ]
        return [TextContent(type="text", text=json.dumps({"ecommerce_urls": ecommerce_urls, "status": "ok"}))]

    raise ValueError(f"Unknown tool: {name}")
