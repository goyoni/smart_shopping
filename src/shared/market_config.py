"""Market configuration loader.

Loads market-specific data from config/markets/ JSON files.
All market-varying behavior is driven by this module — no inline
conditionals for country/currency/language should exist elsewhere.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "markets"


@lru_cache(maxsize=1)
def _load_languages() -> dict[str, Any]:
    path = _CONFIG_DIR / "languages.json"
    with open(path) as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _load_criteria_catalog() -> dict[str, dict[str, dict]]:
    path = _CONFIG_DIR / "criteria_catalog.json"
    with open(path) as f:
        return json.load(f)


@lru_cache(maxsize=16)
def _load_market(code: str) -> dict[str, Any]:
    path = _CONFIG_DIR / f"{code}.json"
    with open(path) as f:
        return json.load(f)


def get_all_market_codes() -> list[str]:
    """Return all available market codes."""
    return [p.stem for p in _CONFIG_DIR.glob("*.json")
            if p.stem not in ("languages", "criteria_catalog")]


def get_market(code: str) -> dict[str, Any]:
    """Load a market config by code. Raises FileNotFoundError if unknown."""
    return _load_market(code)


def get_market_name(code: str) -> str:
    try:
        return _load_market(code)["name"]
    except (FileNotFoundError, KeyError):
        return code.upper()


def get_google_domain(market: str) -> str:
    try:
        return _load_market(market)["google_domain"]
    except (FileNotFoundError, KeyError):
        return "google.com"


def get_region_code(market: str) -> str:
    try:
        return _load_market(market)["region_code"]
    except (FileNotFoundError, KeyError):
        return "us-en"


def get_market_language(market: str) -> str | None:
    """Return the dominant language for a market, or None if unknown."""
    try:
        return _load_market(market)["language"]
    except (FileNotFoundError, KeyError):
        return None


def get_buy_online_suffix(language: str | None = None, market: str | None = None) -> str:
    """Get the 'buy online' suffix.

    Uses the language-based suffix by default.  When a market has a
    non-English dominant language, the market's suffix overrides to
    surface local sellers (e.g. Hebrew suffix for Israeli market
    even when the user searches in English).
    """
    # Try market-specific override for non-English markets
    if market:
        try:
            m = _load_market(market)
            market_lang = m.get("language")
            # Only override for non-English markets — they have localized
            # shopping terms that surface local sellers.
            if market_lang and market_lang != "en":
                return m["buy_online_suffix"]
        except (FileNotFoundError, KeyError):
            pass

    # Default: use the query language suffix
    langs = _load_languages()
    return langs.get("buy_online_suffixes", {}).get(language or "en", "buy online")


def get_language_name(code: str) -> str:
    langs = _load_languages()
    info = langs.get("languages", {}).get(code, {})
    return info.get("name", code)


def get_rtl_languages() -> list[str]:
    """Return language codes with RTL direction."""
    langs = _load_languages()
    return [code for code, info in langs.get("languages", {}).items()
            if info.get("direction") == "rtl"]


def get_lang_to_market_map() -> dict[str, str]:
    """Build a language-to-market mapping for languages tied to one country.

    Skips 'en' since it's global. Used for market detection fallback.
    """
    mapping: dict[str, str] = {}
    for code in get_all_market_codes():
        try:
            m = _load_market(code)
            lang = m.get("language", "")
            if lang and lang != "en" and lang not in mapping:
                mapping[lang] = code
        except (FileNotFoundError, KeyError):
            continue
    return mapping


def get_currency_symbols() -> dict[str, str]:
    """Return a mapping of currency code to symbol from all markets."""
    symbols: dict[str, str] = {}
    for code in get_all_market_codes():
        try:
            m = _load_market(code)
            cur = m.get("currency", {})
            if cur.get("code") and cur.get("symbol"):
                symbols[cur["code"]] = cur["symbol"]
        except (FileNotFoundError, KeyError):
            continue
    return symbols


# ---------------------------------------------------------------------------
# Category aliases (loaded from languages.json)
# ---------------------------------------------------------------------------

def get_all_category_aliases() -> dict[str, str]:
    """Return a merged dict of all language category aliases -> canonical key."""
    langs = _load_languages()
    merged: dict[str, str] = {}
    for _lang, aliases in langs.get("category_aliases", {}).items():
        merged.update(aliases)
    return merged


def get_category_aliases_by_language(language: str) -> dict[str, str]:
    langs = _load_languages()
    return langs.get("category_aliases", {}).get(language, {})


# ---------------------------------------------------------------------------
# Query attributes (loaded from languages.json)
# ---------------------------------------------------------------------------

def get_query_attribute_map() -> dict[str, dict[str, str]]:
    """Return the keyword -> {criterion_key, direction} mapping."""
    langs = _load_languages()
    return langs.get("query_attributes", {})


# ---------------------------------------------------------------------------
# Criteria catalog
# ---------------------------------------------------------------------------

def get_criteria_catalog() -> dict[str, dict[str, dict]]:
    return _load_criteria_catalog()


# ---------------------------------------------------------------------------
# Default aggregators (for DB seeding)
# ---------------------------------------------------------------------------

def get_default_aggregators() -> list[dict]:
    """Collect aggregator entries from all market configs."""
    aggregators: list[dict] = []
    for code in get_all_market_codes():
        try:
            m = _load_market(code)
            for agg in m.get("aggregators", []):
                aggregators.append({
                    "domain": agg["domain"],
                    "url_template": agg["url_template"],
                    "market": code,
                    "categories": agg.get("categories", []),
                })
        except (FileNotFoundError, KeyError):
            continue
    return aggregators
