"""Unified product extraction pipeline.

Re-exports all public symbols so existing imports continue to work:
  from src.mcp_servers.web_scraper_mcp.extractors import extract_all_from_soup, ...
"""

from .api_extract import extract_from_api_responses
from .css_strategy_extract import extract_with_strategy
from .page_methods import extract_all_from_page
from .soup_methods import (
    _extract_from_data_attrs,
    _extract_jsonld_from_soup,
    _extract_microdata_from_soup,
    _extract_og_product_from_soup,
    _extract_page_product_name,
    extract_all_from_soup,
)
from .validation import validate_results

__all__ = [
    "extract_all_from_soup",
    "extract_all_from_page",
    "extract_from_api_responses",
    "extract_with_strategy",
    "validate_results",
    # Exported for tests
    "_extract_from_data_attrs",
    "_extract_jsonld_from_soup",
    "_extract_microdata_from_soup",
    "_extract_og_product_from_soup",
    "_extract_page_product_name",
]
