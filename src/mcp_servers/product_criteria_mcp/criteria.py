"""Product criteria research and management.

Provides pre-defined criteria catalogs for common product categories,
web-based criteria research, and user criteria merging.

All market-specific data (category aliases, translations, criteria catalog)
is loaded from config/markets/ JSON files via src.shared.market_config.
"""

from __future__ import annotations

import re

from src.shared.logging import get_logger
from src.shared.market_config import (
    get_all_category_aliases,
    get_criteria_catalog,
    get_query_attribute_map,
)
from src.shared.models import CriterionSpec, QueryAttribute

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Category name normalization
# ---------------------------------------------------------------------------

def normalize_category(raw: str) -> str:
    """Normalize a product category string to a canonical English key.

    Handles Hebrew, Arabic, English aliases, plural stripping, and lowercasing.
    All alias mappings are loaded from config/markets/languages.json.
    """
    text = raw.strip().lower()

    all_aliases = get_all_category_aliases()

    # Try aliases (longest match first to handle multi-word)
    for alias, canonical in sorted(all_aliases.items(), key=lambda x: -len(x[0])):
        if alias in text:
            return canonical

    # Strip trailing 's' for simple plural
    catalog = get_criteria_catalog()
    stripped = re.sub(r"s$", "", text)
    if stripped in catalog:
        return stripped

    if text in catalog:
        return text

    return text


# Attribute patterns used when researching criteria from web snippets
_ATTRIBUTE_PATTERNS: list[tuple[str, str]] = [
    (r"\b(\d+)\s*db\b", "noise_level"),
    (r"\bnoise\b", "noise_level"),
    (r"\benergy\s*(rating|class|efficiency)\b", "energy_rating"),
    (r"\bcapacit", "capacity"),
    (r"\bliter|litre|\bL\b", "capacity"),
    (r"\bdimension", "dimensions"),
    (r"\bweight\b", "weight"),
    (r"\bwarrant", "warranty"),
    (r"\bpower\b", "power"),
    (r"\bwatt|W\b", "power"),
    (r"\bbtu\b", "cooling_capacity"),
    (r"\bsuction\b", "suction_power"),
    (r"\bbattery\b", "battery_life"),
    (r"\bscreen\s*size\b", "screen_size"),
    (r"\bresolution\b", "resolution"),
    (r"\bprocessor|cpu\b", "processor"),
    (r"\bram\b", "ram"),
    (r"\bstorage|ssd|hdd\b", "storage"),
    (r"\brefresh\s*rate\b", "refresh_rate"),
    (r"\bnoise.cancel", "noise_cancelling"),
    (r"\bspin\s*speed\b", "spin_speed"),
    (r"\bwater\s*consumption\b", "water_consumption"),
    (r"\bself.clean", "self_cleaning"),
    (r"\bfrost.free\b", "frost_free"),
    (r"\binverter\b", "inverter"),
    (r"\bhepa\b", "filtration"),
]


def extract_query_attributes(query: str) -> list[QueryAttribute]:
    """Extract user-intent attributes from a search query.

    Scans the query text for keywords (longest-first to handle multi-word
    phrases like "low noise" before "low") and returns matched attributes
    with their preference direction.

    Keyword mappings are loaded from config/markets/languages.json.
    """
    text = query.lower().strip()
    matched: list[QueryAttribute] = []
    seen_keys: set[str] = set()

    attr_map = get_query_attribute_map()
    for keyword, attr_data in sorted(attr_map.items(), key=lambda x: -len(x[0])):
        if keyword in text and attr_data["criterion_key"] not in seen_keys:
            matched.append(
                QueryAttribute(
                    criterion_key=attr_data["criterion_key"],
                    direction=attr_data["direction"],
                    display_label=keyword,
                )
            )
            seen_keys.add(attr_data["criterion_key"])

    return matched


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_criteria(category: str) -> dict[str, dict]:
    """Return pre-defined criteria for a known product category.

    Returns an empty dict for unknown categories.
    """
    normalized = normalize_category(category)
    catalog = get_criteria_catalog()
    return dict(catalog.get(normalized, {}))


def research_criteria(snippets: list[str], base_criteria: dict[str, dict]) -> dict[str, dict]:
    """Discover additional criteria by matching attribute patterns in web snippets.

    Scans the provided search result snippets for known attribute patterns
    and adds any newly found criteria to the base set.

    Returns the merged criteria dict (base + newly discovered).
    """
    discovered_keys: set[str] = set()

    combined_text = " ".join(snippets).lower()
    for pattern, attr_key in _ATTRIBUTE_PATTERNS:
        if re.search(pattern, combined_text, re.IGNORECASE):
            discovered_keys.add(attr_key)

    catalog = get_criteria_catalog()
    result = dict(base_criteria)
    for key in discovered_keys:
        if key not in result:
            # Try to find a display name from any catalog entry
            display_name = key.replace("_", " ").title()
            for cat_specs in catalog.values():
                if key in cat_specs:
                    display_name = cat_specs[key]["display_name"]
                    break
            result[key] = {
                "display_name": display_name,
                "unit": "",
                "importance": "low",
                "description": "Discovered from web research",
            }

    return result


def merge_criteria(
    base_criteria: dict[str, dict],
    user_criteria: dict[str, dict | str | None],
) -> dict[str, dict]:
    """Merge user-specified criteria onto a base criteria set.

    - If a user value is ``None``, the criterion is removed.
    - If a user value is a ``str``, it overrides the importance.
    - If a user value is a ``dict``, it is merged field-by-field.
    """
    result = dict(base_criteria)

    for key, value in user_criteria.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, str):
            if key in result:
                result[key] = {**result[key], "importance": value}
            else:
                result[key] = {
                    "display_name": key.replace("_", " ").title(),
                    "unit": "",
                    "importance": value,
                    "description": "",
                }
        elif isinstance(value, dict):
            if key in result:
                result[key] = {**result[key], **value}
            else:
                result[key] = {
                    "display_name": value.get("display_name", key.replace("_", " ").title()),
                    "unit": value.get("unit", ""),
                    "importance": value.get("importance", "medium"),
                    "description": value.get("description", ""),
                }

    return result
