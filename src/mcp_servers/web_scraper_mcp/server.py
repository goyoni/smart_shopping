"""Web Scraper MCP Server implementation."""

from __future__ import annotations

import json

from mcp.server import Server
from mcp.types import TextContent, Tool

from src.mcp_servers.web_scraper_mcp.db_cache import get_cached_strategy, get_domain_health, save_strategy
from src.mcp_servers.web_scraper_mcp.health_check import check_all_strategies, get_stale_strategies
from src.mcp_servers.web_scraper_mcp.scraper import scrape_page
from src.mcp_servers.web_scraper_mcp.seller_contact import get_cached_contact, scrape_seller_contact
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
                    "market": {
                        "type": "string",
                        "description": "Two-letter market code for geo-proxy routing (e.g. il, us, gr)",
                        "default": "us",
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": "Maximum additional pages to follow via pagination (0 to disable)",
                        "default": 3,
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
        Tool(
            name="domain_health",
            description=(
                "Get health status for all tracked domains. Returns each domain's "
                "status (healthy/degraded/blocked), success rate, failure history, "
                "and cached strategy info."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_seller_contact",
            description=(
                "Get cached contact info (phone, email, WhatsApp) for a seller domain, "
                "or scrape the seller's website if not cached."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Seller domain to look up"},
                    "market": {
                        "type": "string",
                        "description": "Two-letter market code",
                        "default": "us",
                    },
                },
                "required": ["domain"],
            },
        ),
        Tool(
            name="check_strategy_health",
            description=(
                "Run health checks on stale or degraded scraping strategies. "
                "Probes each domain's last successful URL to verify the cached "
                "strategy still works. Use dry_run=true to list what would be checked."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, list stale strategies without probing them",
                        "default": False,
                    },
                },
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "scrape_page":
        url = arguments["url"]
        product_query = arguments.get("product_query", "")
        market = arguments.get("market", "us")
        max_pages = arguments.get("max_pages", 3)
        async with get_browser() as browser:
            products = await scrape_page(browser, url, product_query, market=market, max_pages=max_pages)
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

    elif name == "get_seller_contact":
        domain = arguments["domain"]
        market = arguments.get("market", "us")
        cached = await get_cached_contact(domain)
        if cached and cached.has_contact:
            return [TextContent(type="text", text=json.dumps({
                "phone": cached.phone, "email": cached.email,
                "whatsapp_url": cached.whatsapp_url, "source": cached.source,
                "status": "cached",
            }))]
        async with get_browser() as browser:
            contact = await scrape_seller_contact(browser, domain, market)
        return [TextContent(type="text", text=json.dumps({
            "phone": contact.phone, "email": contact.email,
            "whatsapp_url": contact.whatsapp_url, "source": contact.source,
            "status": "scraped" if contact.has_contact else "not_found",
        }))]

    elif name == "domain_health":
        health = await get_domain_health()
        return [TextContent(type="text", text=json.dumps({"domains": health, "count": len(health)}))]

    elif name == "check_strategy_health":
        dry_run = arguments.get("dry_run", False)
        if dry_run:
            stale = await get_stale_strategies()
            return [TextContent(type="text", text=json.dumps({"stale": stale, "count": len(stale), "dry_run": True}))]
        async with get_browser() as browser:
            results = await check_all_strategies(browser)
        return [TextContent(type="text", text=json.dumps({"results": results, "count": len(results), "dry_run": False}))]

    raise ValueError(f"Unknown tool: {name}")
