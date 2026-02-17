"""Web Scraper MCP Server implementation."""

from __future__ import annotations

import json

from mcp.server import Server
from mcp.types import TextContent, Tool

from src.mcp_servers.web_scraper_mcp.db_cache import get_cached_strategy, save_strategy
from src.mcp_servers.web_scraper_mcp.scraper import scrape_page
from src.mcp_servers.web_scraper_mcp.strategy import ScrapingStrategy
from src.shared.browser import get_browser

server = Server("web-scraper")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="scrape_page",
            description="Scrape a product page using stored or newly learned strategy",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to scrape"},
                    "product_query": {
                        "type": "string",
                        "description": "What product to look for on the page",
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="get_scraping_instructions",
            description="Retrieve cached scraping instructions for a domain",
            inputSchema={
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Domain to look up"}
                },
                "required": ["domain"],
            },
        ),
        Tool(
            name="save_scraping_instructions",
            description="Save learned scraping instructions for a domain",
            inputSchema={
                "type": "object",
                "properties": {
                    "domain": {"type": "string"},
                    "strategy": {
                        "type": "object",
                        "description": "Scraping strategy configuration",
                    },
                },
                "required": ["domain", "strategy"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "scrape_page":
        url = arguments["url"]
        product_query = arguments.get("product_query", "")
        async with get_browser() as browser:
            products = await scrape_page(browser, url, product_query)
        products_data = [p.model_dump() for p in products]
        return [TextContent(type="text", text=json.dumps({"products": products_data, "status": "ok"}))]

    elif name == "get_scraping_instructions":
        domain = arguments["domain"]
        strategy = await get_cached_strategy(domain)
        if strategy:
            return [TextContent(type="text", text=json.dumps({"strategy": json.loads(strategy.to_json()), "status": "found"}))]
        return [TextContent(type="text", text=json.dumps({"strategy": None, "status": "not_found"}))]

    elif name == "save_scraping_instructions":
        domain = arguments["domain"]
        strategy_data = arguments["strategy"]
        strategy = ScrapingStrategy(**strategy_data)
        await save_strategy(domain, strategy)
        return [TextContent(type="text", text=json.dumps({"status": "saved"}))]

    raise ValueError(f"Unknown tool: {name}")
