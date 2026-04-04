"""Query parsing utilities and agent state for the main agent."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from src.shared.market_config import get_all_category_aliases
from src.shared.models import CrossSeller, ProductResult, SearchStatus

StatusCallback = Callable[[str, str], Awaitable[None]]

_MAX_SITES_TO_SCRAPE = 8


def extract_category(query: str) -> str | None:
    """Extract a product category from a search query using keyword matching.

    Returns the canonical category key or None if no category is detected.
    Checks longer keywords first to handle multi-word matches.
    Category aliases are loaded from config/markets/languages.json.
    """
    text = query.lower().strip()
    aliases = get_all_category_aliases()
    for keyword, category in sorted(aliases.items(), key=lambda x: -len(x[0])):
        if keyword in text:
            return category
    return None


def detect_model_ids(query: str) -> list[str]:
    """Detect comma/space-separated model IDs in a query.

    Returns a list of model ID strings, or an empty list if the query
    is a natural-language search rather than a model-based lookup.

    Model IDs are alphanumeric tokens containing both letters and digits
    (e.g. 'A1234', 'WH-1000XM5', 'LG-GR-F501ELDZ').

    Works with single model IDs (e.g. "BFL523MB1F") as well as
    multiple (e.g. "BFL523MB1F, HBG578EB3").
    """
    # Strip common prefixes
    cleaned = re.sub(
        r"^(find|search|compare|look\s+up|price\s+(for|of|check))\s+",
        "",
        query.strip(),
        flags=re.IGNORECASE,
    )

    # Strip location suffixes like "in israel", "in germany"
    cleaned = re.sub(
        r"\s+in\s+\w+$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    # Split on comma, semicolon, " and ", or " vs "
    parts = re.split(r"[,;]\s*|\s+(?:and|vs\.?)\s+", cleaned)
    parts = [p.strip() for p in parts if p.strip()]

    # Each part should look like a model ID: alphanumeric token with both
    # letters and digits, no spaces (model IDs are single tokens like
    # "BFL523MB1F" or "WH-1000XM5", not phrases).
    model_ids: list[str] = []
    for part in parts:
        token = part.strip()
        # Model IDs don't contain spaces (except hyphens/underscores)
        if " " in token:
            # Multi-word part -> not a model ID
            continue
        has_letter = any(c.isalpha() for c in token)
        has_digit = any(c.isdigit() for c in token)
        if has_letter and has_digit and len(token) >= 3:
            model_ids.append(token)

    # Accept single model IDs too -- if all parts parsed as model IDs
    if not model_ids:
        return []
    # If there's extra non-model text, it's a natural language query
    if len(model_ids) < len(parts):
        return []
    return model_ids


def _build_locale(language: str, market: str) -> str:
    """Build a browser locale string from language and market codes."""
    return f"{language}-{market.upper()}"


@dataclass
class AgentState:
    """Tracks the current state of an agent session."""

    session_id: str
    status: SearchStatus = SearchStatus.PENDING
    query: str = ""
    language: str = "en"
    results: list[ProductResult] = field(default_factory=list)
    cross_sellers: list[CrossSeller] = field(default_factory=list)
    conversation_history: list[dict[str, str]] = field(default_factory=list)
    status_messages: list[str] = field(default_factory=list)
