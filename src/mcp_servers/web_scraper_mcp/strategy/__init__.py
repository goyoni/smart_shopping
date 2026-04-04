"""Scraping strategy model and adaptive discovery.

Re-exports all public symbols so existing imports continue to work:
  from src.mcp_servers.web_scraper_mcp.strategy import ScrapingStrategy, discover_strategy, ...
"""

from .model import ScrapingStrategy, _get_scraper_llm_model
from .discovery import discover_strategy
from .navigation import find_product_url
from .helpers import _looks_like_price, _detect_currency
from .llm_discovery import _build_dom_snapshot, _discover_via_llm

__all__ = [
    "ScrapingStrategy",
    "_get_scraper_llm_model",
    "discover_strategy",
    "find_product_url",
    "_looks_like_price",
    "_detect_currency",
    "_build_dom_snapshot",
    "_discover_via_llm",
]
