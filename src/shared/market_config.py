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
        # Build a best-effort region code from the market code itself
        # e.g. "gr" -> "gr-en", "jp" -> "jp-en"
        if len(market) == 2 and market.isalpha():
            return f"{market}-en"
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


def get_contact_paths(market: str | None = None) -> list[str]:
    """Return contact page URL paths for a market's language.

    Always includes universal English paths. Adds language-specific
    paths when a market is provided.
    """
    base = ["/contact", "/contact-us", "/about", "/about-us", "/contactus"]
    if not market:
        return base

    lang = get_market_language(market)
    if not lang:
        return base

    langs = _load_languages()
    info = langs.get("languages", {}).get(lang, {})
    extra = info.get("contact_paths", [])
    return base + extra


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


@lru_cache(maxsize=1)
def _build_query_market_map() -> dict[str, str]:
    """Build a mapping of country name/alias (lowercased) -> market code.

    Used for detecting market from query text like "in israel".
    """
    mapping: dict[str, str] = {}
    for code in get_all_market_codes():
        try:
            m = _load_market(code)
            name = m.get("name", "").lower()
            if name:
                mapping[name] = code
            # Also map the code itself
            mapping[code] = code
        except (FileNotFoundError, KeyError):
            continue
    return mapping


def detect_market_from_query(query: str) -> str | None:
    """Extract market from query text like 'in israel' or 'in germany'.

    Returns the market code (e.g. 'il') or None if no market is detected.
    """
    import re

    text = query.lower().strip()
    market_map = _build_query_market_map()

    # Match "in <country>" at the end or as a phrase within the query
    for country_name, code in sorted(market_map.items(), key=lambda x: -len(x[0])):
        pattern = rf"\bin\s+{re.escape(country_name)}\b"
        if re.search(pattern, text):
            return code

    return None


def get_market_tld(market: str) -> str | None:
    """Return the country TLD for a market code (e.g. 'il' -> '.co.il')."""
    try:
        return _load_market(market).get("tld")
    except (FileNotFoundError, KeyError):
        return None


@lru_cache(maxsize=1)
def get_marketplace_domain_map() -> dict[str, str]:
    """Build a mapping of marketplace domain -> market code from all market configs.

    E.g. {"amazon.de": "de", "ebay.co.uk": "uk", ...}
    Used to penalize marketplace domains that don't match the target market.
    """
    mapping: dict[str, str] = {}
    for code in get_all_market_codes():
        try:
            m = _load_market(code)
            for domain, market_code in m.get("marketplace_domains", {}).items():
                mapping[domain] = market_code
        except (FileNotFoundError, KeyError):
            continue
    return mapping


def get_default_currency_for_domain(domain: str) -> str:
    """Resolve a default currency code from a domain's TLD.

    Falls back to "USD" if no market matches.
    """
    for code in get_all_market_codes():
        try:
            m = _load_market(code)
            tld = m.get("tld", "")
            if tld and domain.endswith(tld):
                return m.get("currency", {}).get("code", "USD")
        except (FileNotFoundError, KeyError):
            continue
    return "USD"


def get_garbage_names() -> set[str]:
    """Load UI/navigation garbage names from all languages in languages.json."""
    langs = _load_languages()
    names: set[str] = set()
    for _lang, name_list in langs.get("garbage_names", {}).items():
        names.update(name_list)
    return names


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


def get_browser_timezone(market: str) -> str:
    """Return the IANA timezone for a market (e.g. 'il' -> 'Asia/Jerusalem')."""
    try:
        return _load_market(market).get("timezone", "UTC")
    except (FileNotFoundError, KeyError):
        return "UTC"


def get_browser_geolocation(market: str) -> dict[str, float] | None:
    """Return geolocation dict with latitude/longitude for a market."""
    try:
        return _load_market(market).get("geolocation")
    except (FileNotFoundError, KeyError):
        return None


def get_browser_languages(market: str) -> list[str]:
    """Return preferred browser language list for a market."""
    try:
        langs = _load_market(market).get("browser_languages")
        if langs:
            return langs
    except (FileNotFoundError, KeyError):
        pass
    # Derive from market language + locale
    lang = get_market_language(market)
    if lang:
        return [f"{lang}-{market.upper()}", lang, "en-US", "en"]
    return ["en-US", "en"]


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
