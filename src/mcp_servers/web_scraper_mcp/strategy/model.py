"""ScrapingStrategy dataclass and LLM model configuration."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from src.shared.config import settings


def _get_scraper_llm_model() -> str:
    """Return the LLM model to use for scraper strategy discovery."""
    return settings.scraper_llm_model or settings.llm_model


@dataclass
class ScrapingStrategy:
    product_container: str
    name_selector: str = ""
    price_selector: str = ""
    image_selector: str = ""
    url_selector: str = ""
    model_selector: str = ""
    brand_selector: str = ""
    mpn_selector: str = ""
    currency_hint: str = ""
    version: int = 1
    discovery_method: str = "css_candidates"
    access_method: str = ""
    extraction_method: str = ""
    # Failure tracking
    block_type: str = ""          # "captcha", "waf", "" (not blocked)
    blocked_at: str = ""          # ISO timestamp when block was detected
    last_failure_type: str = ""   # FailureType.value from last failure
    last_failure_at: str = ""     # ISO timestamp of last failure
    consecutive_failures: int = 0
    validation_failures: int = 0  # Consecutive validation-quality failures
    last_successful_url: str = "" # Probe URL for health checks
    criteria_selectors: dict[str, str] = field(default_factory=dict)
    # Data-attribute extraction: when set, read these attributes from the
    # container element itself instead of using sub-selector + inner_text.
    name_attr: str = ""
    price_attr: str = ""
    # Navigation: CSS selector for product links on listing/search pages.
    product_link_selector: str = ""
    # Pagination: how to navigate to next pages on listing/search pages.
    next_page_selector: str = ""   # CSS selector for "next page" link/button
    pagination_type: str = ""      # "link" | "load_more" | "url_param" | ""
    pagination_param: str = ""     # URL parameter name (e.g. "page", "p", "offset")

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> ScrapingStrategy:
        parsed = json.loads(data)
        valid_fields = {k: v for k, v in parsed.items() if k in cls.__dataclass_fields__}
        return cls(**valid_fields)
