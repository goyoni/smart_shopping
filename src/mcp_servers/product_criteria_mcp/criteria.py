"""Product criteria research and management.

Provides pre-defined criteria catalogs for common product categories,
web-based criteria research, and user criteria merging.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.shared.logging import get_logger

logger = get_logger(__name__)


@dataclass
class CriterionSpec:
    """Specification for a single product criterion."""

    display_name: str
    unit: str = ""
    importance: str = "medium"
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "display_name": self.display_name,
            "unit": self.unit,
            "importance": self.importance,
            "description": self.description,
        }


# ---------------------------------------------------------------------------
# Category name normalization
# ---------------------------------------------------------------------------

_HEBREW_TO_ENGLISH: dict[str, str] = {
    "מקרר": "refrigerator",
    "מקררים": "refrigerator",
    "מיקרוגל": "microwave",
    "תנור": "oven",
    "תנורים": "oven",
    "כיריים": "stove",
    "מכונת כביסה": "washing_machine",
    "מדיח כלים": "dishwasher",
    "טלוויזיה": "tv",
    "מחשב נייד": "laptop",
    "מחשב": "laptop",
    "אוזניות": "headphones",
    "מזגן": "air_conditioner",
    "שואב אבק": "vacuum",
}

_ARABIC_TO_ENGLISH: dict[str, str] = {
    "ثلاجة": "refrigerator",
    "ميكروويف": "microwave",
    "فرن": "oven",
    "غسالة": "washing_machine",
    "غسالة صحون": "dishwasher",
    "تلفزيون": "tv",
    "حاسوب محمول": "laptop",
    "سماعات": "headphones",
    "مكيف": "air_conditioner",
    "مكنسة كهربائية": "vacuum",
}

_ENGLISH_ALIASES: dict[str, str] = {
    "fridge": "refrigerator",
    "fridges": "refrigerator",
    "refrigerators": "refrigerator",
    "microwaves": "microwave",
    "microwave oven": "microwave",
    "ovens": "oven",
    "stoves": "stove",
    "cooktop": "stove",
    "cooktops": "stove",
    "washing machine": "washing_machine",
    "washing machines": "washing_machine",
    "washer": "washing_machine",
    "washers": "washing_machine",
    "dryer": "dryer",
    "dryers": "dryer",
    "dishwashers": "dishwasher",
    "television": "tv",
    "televisions": "tv",
    "tvs": "tv",
    "laptops": "laptop",
    "notebook": "laptop",
    "notebooks": "laptop",
    "headphone": "headphones",
    "earbuds": "headphones",
    "earphones": "headphones",
    "air conditioner": "air_conditioner",
    "air conditioners": "air_conditioner",
    "ac": "air_conditioner",
    "vacuum cleaner": "vacuum",
    "vacuum cleaners": "vacuum",
    "vacuums": "vacuum",
    "robot vacuum": "vacuum",
}


def normalize_category(raw: str) -> str:
    """Normalize a product category string to a canonical English key.

    Handles Hebrew, Arabic, English aliases, plural stripping, and lowercasing.
    """
    text = raw.strip().lower()

    # Try Hebrew mapping
    for he_term, eng in _HEBREW_TO_ENGLISH.items():
        if he_term in text:
            return eng

    # Try Arabic mapping
    for ar_term, eng in _ARABIC_TO_ENGLISH.items():
        if ar_term in text:
            return eng

    # Try English aliases (longest match first to handle multi-word)
    for alias, canonical in sorted(_ENGLISH_ALIASES.items(), key=lambda x: -len(x[0])):
        if alias in text:
            return canonical

    # Strip trailing 's' for simple plural
    stripped = re.sub(r"s$", "", text)
    if stripped in _CRITERIA_CATALOG:
        return stripped

    # If the text itself is a known category, return it
    if text in _CRITERIA_CATALOG:
        return text

    return text


# ---------------------------------------------------------------------------
# Pre-defined criteria catalog
# ---------------------------------------------------------------------------

_CRITERIA_CATALOG: dict[str, dict[str, CriterionSpec]] = {
    "refrigerator": {
        "noise_level": CriterionSpec("Noise Level", "dB", "high", "Operating noise in decibels"),
        "energy_rating": CriterionSpec("Energy Rating", "", "high", "Energy efficiency class"),
        "capacity": CriterionSpec("Capacity", "L", "high", "Internal volume in liters"),
        "price": CriterionSpec("Price", "", "high", ""),
        "dimensions": CriterionSpec("Dimensions", "cm", "medium", "Height x Width x Depth"),
        "weight": CriterionSpec("Weight", "kg", "low", "Total weight"),
        "freezer_type": CriterionSpec("Freezer Type", "", "medium", "Top, bottom, or side-by-side"),
        "frost_free": CriterionSpec("Frost Free", "", "medium", "No-frost technology"),
        "warranty": CriterionSpec("Warranty", "years", "medium", "Manufacturer warranty period"),
    },
    "microwave": {
        "power": CriterionSpec("Power", "W", "high", "Microwave power in watts"),
        "capacity": CriterionSpec("Capacity", "L", "high", "Internal volume"),
        "price": CriterionSpec("Price", "", "high", ""),
        "dimensions": CriterionSpec("Dimensions", "cm", "medium", "External dimensions"),
        "weight": CriterionSpec("Weight", "kg", "low", "Total weight"),
        "grill": CriterionSpec("Grill Function", "", "medium", "Has grill capability"),
        "energy_rating": CriterionSpec("Energy Rating", "", "medium", "Energy efficiency"),
        "warranty": CriterionSpec("Warranty", "years", "low", ""),
    },
    "oven": {
        "capacity": CriterionSpec("Capacity", "L", "high", "Internal volume"),
        "energy_rating": CriterionSpec("Energy Rating", "", "high", "Energy efficiency"),
        "price": CriterionSpec("Price", "", "high", ""),
        "max_temperature": CriterionSpec("Max Temperature", "°C", "medium", "Maximum heating temperature"),
        "cooking_modes": CriterionSpec("Cooking Modes", "", "medium", "Number of cooking programs"),
        "self_cleaning": CriterionSpec("Self Cleaning", "", "medium", "Pyrolytic or catalytic cleaning"),
        "dimensions": CriterionSpec("Dimensions", "cm", "medium", ""),
        "warranty": CriterionSpec("Warranty", "years", "low", ""),
    },
    "stove": {
        "burners": CriterionSpec("Burners", "", "high", "Number of burners"),
        "fuel_type": CriterionSpec("Fuel Type", "", "high", "Gas, electric, or induction"),
        "price": CriterionSpec("Price", "", "high", ""),
        "dimensions": CriterionSpec("Dimensions", "cm", "medium", ""),
        "power": CriterionSpec("Power", "kW", "medium", "Maximum burner power"),
        "safety_features": CriterionSpec("Safety Features", "", "medium", "Gas shutoff, child lock"),
        "warranty": CriterionSpec("Warranty", "years", "low", ""),
    },
    "washing_machine": {
        "capacity": CriterionSpec("Capacity", "kg", "high", "Load capacity in kg"),
        "spin_speed": CriterionSpec("Spin Speed", "RPM", "high", "Maximum spin speed"),
        "energy_rating": CriterionSpec("Energy Rating", "", "high", "Energy efficiency"),
        "noise_level": CriterionSpec("Noise Level", "dB", "medium", "Operating noise"),
        "price": CriterionSpec("Price", "", "high", ""),
        "water_consumption": CriterionSpec("Water Consumption", "L", "medium", "Per cycle"),
        "programs": CriterionSpec("Programs", "", "medium", "Number of wash programs"),
        "dimensions": CriterionSpec("Dimensions", "cm", "medium", ""),
        "warranty": CriterionSpec("Warranty", "years", "low", ""),
    },
    "dishwasher": {
        "place_settings": CriterionSpec("Place Settings", "", "high", "Number of place settings"),
        "energy_rating": CriterionSpec("Energy Rating", "", "high", "Energy efficiency"),
        "noise_level": CriterionSpec("Noise Level", "dB", "high", "Operating noise"),
        "price": CriterionSpec("Price", "", "high", ""),
        "water_consumption": CriterionSpec("Water Consumption", "L", "medium", "Per cycle"),
        "programs": CriterionSpec("Programs", "", "medium", "Number of wash programs"),
        "dimensions": CriterionSpec("Dimensions", "cm", "medium", ""),
        "warranty": CriterionSpec("Warranty", "years", "low", ""),
    },
    "tv": {
        "screen_size": CriterionSpec("Screen Size", "inches", "high", "Diagonal screen size"),
        "resolution": CriterionSpec("Resolution", "", "high", "4K, 8K, Full HD"),
        "panel_type": CriterionSpec("Panel Type", "", "high", "OLED, QLED, LED, Mini-LED"),
        "price": CriterionSpec("Price", "", "high", ""),
        "refresh_rate": CriterionSpec("Refresh Rate", "Hz", "medium", "Screen refresh rate"),
        "smart_tv": CriterionSpec("Smart TV", "", "medium", "OS and smart features"),
        "hdmi_ports": CriterionSpec("HDMI Ports", "", "low", "Number of HDMI inputs"),
        "hdr": CriterionSpec("HDR", "", "medium", "HDR support type"),
        "warranty": CriterionSpec("Warranty", "years", "low", ""),
    },
    "laptop": {
        "processor": CriterionSpec("Processor", "", "high", "CPU model"),
        "ram": CriterionSpec("RAM", "GB", "high", "Memory capacity"),
        "storage": CriterionSpec("Storage", "GB", "high", "SSD/HDD capacity"),
        "screen_size": CriterionSpec("Screen Size", "inches", "high", "Display diagonal"),
        "price": CriterionSpec("Price", "", "high", ""),
        "battery_life": CriterionSpec("Battery Life", "hours", "medium", "Estimated battery hours"),
        "weight": CriterionSpec("Weight", "kg", "medium", "Device weight"),
        "gpu": CriterionSpec("GPU", "", "medium", "Graphics card"),
        "resolution": CriterionSpec("Resolution", "", "medium", "Display resolution"),
        "warranty": CriterionSpec("Warranty", "years", "low", ""),
    },
    "headphones": {
        "driver_size": CriterionSpec("Driver Size", "mm", "medium", "Speaker driver diameter"),
        "noise_cancelling": CriterionSpec("Noise Cancelling", "", "high", "ANC support"),
        "battery_life": CriterionSpec("Battery Life", "hours", "high", "Playback time"),
        "price": CriterionSpec("Price", "", "high", ""),
        "connectivity": CriterionSpec("Connectivity", "", "high", "Bluetooth, wired, etc."),
        "weight": CriterionSpec("Weight", "g", "medium", "Device weight"),
        "water_resistance": CriterionSpec("Water Resistance", "", "low", "IP rating"),
        "warranty": CriterionSpec("Warranty", "years", "low", ""),
    },
    "air_conditioner": {
        "cooling_capacity": CriterionSpec("Cooling Capacity", "BTU", "high", "Cooling power"),
        "energy_rating": CriterionSpec("Energy Rating", "", "high", "Energy efficiency"),
        "noise_level": CriterionSpec("Noise Level", "dB", "high", "Indoor unit noise"),
        "price": CriterionSpec("Price", "", "high", ""),
        "coverage_area": CriterionSpec("Coverage Area", "m²", "high", "Recommended room size"),
        "inverter": CriterionSpec("Inverter", "", "medium", "Inverter technology"),
        "heating": CriterionSpec("Heating", "", "medium", "Heat pump capability"),
        "warranty": CriterionSpec("Warranty", "years", "low", ""),
    },
    "vacuum": {
        "suction_power": CriterionSpec("Suction Power", "W", "high", "Suction strength"),
        "battery_life": CriterionSpec("Battery Life", "min", "high", "Runtime for cordless"),
        "price": CriterionSpec("Price", "", "high", ""),
        "weight": CriterionSpec("Weight", "kg", "medium", "Device weight"),
        "dust_capacity": CriterionSpec("Dust Capacity", "L", "medium", "Dustbin volume"),
        "noise_level": CriterionSpec("Noise Level", "dB", "medium", "Operating noise"),
        "filtration": CriterionSpec("Filtration", "", "medium", "HEPA or other filter type"),
        "warranty": CriterionSpec("Warranty", "years", "low", ""),
    },
    "dryer": {
        "capacity": CriterionSpec("Capacity", "kg", "high", "Load capacity"),
        "energy_rating": CriterionSpec("Energy Rating", "", "high", "Energy efficiency"),
        "price": CriterionSpec("Price", "", "high", ""),
        "dryer_type": CriterionSpec("Dryer Type", "", "high", "Condenser, heat pump, vented"),
        "noise_level": CriterionSpec("Noise Level", "dB", "medium", "Operating noise"),
        "programs": CriterionSpec("Programs", "", "medium", "Number of drying programs"),
        "dimensions": CriterionSpec("Dimensions", "cm", "medium", ""),
        "warranty": CriterionSpec("Warranty", "years", "low", ""),
    },
}

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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_criteria(category: str) -> dict[str, dict]:
    """Return pre-defined criteria for a known product category.

    Returns an empty dict for unknown categories.
    """
    normalized = normalize_category(category)
    specs = _CRITERIA_CATALOG.get(normalized, {})
    return {key: spec.to_dict() for key, spec in specs.items()}


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

    result = dict(base_criteria)
    for key in discovered_keys:
        if key not in result:
            # Try to find a display name from any catalog entry
            display_name = key.replace("_", " ").title()
            for cat_specs in _CRITERIA_CATALOG.values():
                if key in cat_specs:
                    display_name = cat_specs[key].display_name
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
