"""Core scraping orchestration with adaptive strategy pipeline.

Re-exports all public symbols so existing imports continue to work:
  from src.mcp_servers.web_scraper_mcp.scraper import scrape_page, ...
"""

from .helpers import (
    _find_next_page_url,
    _is_safe_url,
    extract_domain,
    extract_specs_from_text,
    parse_price,
)
from .pipeline import scrape_page
from .post_process import _merge_comparison_sellers

__all__ = [
    "scrape_page",
    "extract_domain",
    "parse_price",
    "extract_specs_from_text",
    # Exported for tests
    "_is_safe_url",
    "_find_next_page_url",
    "_merge_comparison_sellers",
]
