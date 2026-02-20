"""Dynamic extraction pattern builder for product specifications.

Builds regex patterns at runtime from criteria dicts (provided by the
product criteria MCP) instead of using a hardcoded pattern list.  Each
criterion's ``unit`` field drives pattern generation; criteria with
non-standard units use a special-case registry.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Unit-based pattern templates
# ---------------------------------------------------------------------------
# Maps a CriterionSpec.unit string to (regex_template, capture_group_index).
# The template must contain at least one capturing group for the value.

_UNIT_PATTERN_TEMPLATES: dict[str, tuple[str, int]] = {
    "dB": (r"(\d+)\s*db\b", 0),
    "L": (r"(\d+)\s*(?:liters?|litres?|L)\b", 0),
    "kg": (r"(\d+(?:\.\d+)?)\s*kg\b", 0),
    "g": (r"(\d+)\s*g\b", 0),
    "W": (r"(\d+)\s*W\b", 0),
    "BTU": (r"(\d[\d,]*)\s*BTU\b", 0),
    "RPM": (r"(\d+)\s*RPM\b", 0),
    "Hz": (r"(\d+)\s*Hz\b", 0),
    "inches": (r'(\d{2,3})\s*["\u2033]\s*|(\d{2,3})\s*inch', 0),
    "GB": (r"(\d+)\s*GB\b", 0),
    "TB": (r"(\d+)\s*TB\b", 0),
    "mm": (r"(\d+)\s*mm\b", 0),
    "cm": (r"(\d+)\s*cm\b", 0),
    "hours": (r"(\d+)\s*(?:hours?|hrs?)\b", 0),
    "min": (r"(\d+)\s*(?:minutes?|mins?)\b", 0),
    "m\u00b2": (r"(\d+)\s*m[²2]\b", 0),
    "years": (r"(\d+)\s*(?:years?|yrs?)\b", 0),
    "\u00b0C": (r"(\d+)\s*°?C\b", 0),
    "kW": (r"(\d[\d.]*)\s*kW\b", 0),
}

# ---------------------------------------------------------------------------
# Special patterns for criteria that cannot be matched by unit alone
# ---------------------------------------------------------------------------

_SPECIAL_PATTERNS: dict[str, tuple[str, int]] = {
    "resolution": (r"\b(4K|8K|UHD|Full\s*HD|FHD|QHD|1080p|2160p)\b", 0),
    "panel_type": (r"\b(OLED|QLED|Mini.?LED|Neo\s*QLED|LED|IPS|VA|TN)\b", 0),
    "energy_rating": (r"\b(A\+{0,3}|[A-G])\s*energy\b", 1),
    "processor": (
        r"\b(i[3579][-\s]?\d{4,5}\w*|Ryzen\s*\d\s*\d{4}\w*"
        r"|M[1-4]\s*(?:Pro|Max|Ultra)?)\b",
        0,
    ),
    "ram": (r"(\d+)\s*GB\s*RAM\b", 0),
    "storage": (r"(\d+)\s*(?:GB|TB)\s*(?:SSD|HDD|storage)\b", 0),
    "noise_cancelling": (r"\b(ANC|active\s*noise\s*cancell?(?:ing|ation))\b", 0),
    "frost_free": (r"\bfrost[\s-]*free\b", 0),
    "inverter": (r"\binverter\b", 0),
    "filtration": (r"\b(HEPA|H1[0-4])\b", 0),
}

# ---------------------------------------------------------------------------
# Default criteria – used when no criteria dict is provided.
# Covers the same keys as the original hardcoded _SPEC_PATTERNS.
# ---------------------------------------------------------------------------

_DEFAULT_CRITERIA: dict[str, dict] = {
    "noise_level": {"unit": "dB"},
    "capacity": {"unit": "L"},
    "weight": {"unit": "kg"},
    "power": {"unit": "W"},
    "cooling_capacity": {"unit": "BTU"},
    "spin_speed": {"unit": "RPM"},
    "screen_size": {"unit": "inches"},
    "resolution": {"unit": ""},
    "panel_type": {"unit": ""},
    "refresh_rate": {"unit": "Hz"},
    "energy_rating": {"unit": ""},
    "processor": {"unit": ""},
    "ram": {"unit": "GB"},
    "storage": {"unit": ""},
    "noise_cancelling": {"unit": ""},
    "frost_free": {"unit": ""},
    "inverter": {"unit": ""},
    "filtration": {"unit": ""},
}


def build_extraction_patterns(
    criteria: dict[str, dict] | None = None,
) -> list[tuple[re.Pattern, str, int]]:
    """Build regex extraction patterns from a criteria dict.

    For each criterion key the function checks (in order):
    1. ``_SPECIAL_PATTERNS`` – hand-tuned patterns for complex criteria.
    2. ``_UNIT_PATTERN_TEMPLATES`` – auto-generated from the criterion's
       ``unit`` field (e.g. ``"dB"`` -> ``r"(\\d+)\\s*dB\\b"``).

    If *criteria* is ``None``, a default set is used that covers the same
    keys as the original hardcoded ``_SPEC_PATTERNS``.

    Returns a list of ``(compiled_pattern, criterion_key, group_index)``
    tuples ready for use with ``re.search``.
    """
    source = criteria if criteria is not None else _DEFAULT_CRITERIA
    patterns: list[tuple[re.Pattern, str, int]] = []
    seen_keys: set[str] = set()

    for key, spec in source.items():
        if key in seen_keys or key == "price":
            continue
        seen_keys.add(key)

        # 1) Check special patterns first
        if key in _SPECIAL_PATTERNS:
            template, group_idx = _SPECIAL_PATTERNS[key]
            patterns.append((re.compile(template, re.IGNORECASE), key, group_idx))
            continue

        # 2) Check unit-based templates
        unit = spec.get("unit", "") if isinstance(spec, dict) else ""
        if unit and unit in _UNIT_PATTERN_TEMPLATES:
            template, group_idx = _UNIT_PATTERN_TEMPLATES[unit]
            patterns.append((re.compile(template, re.IGNORECASE), key, group_idx))

    return patterns
