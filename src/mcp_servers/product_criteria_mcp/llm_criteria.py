"""LLM-powered criteria discovery for unknown product categories."""

from __future__ import annotations

import json
import re

import litellm

from src.mcp_servers.product_criteria_mcp.criteria import QueryAttribute
from src.shared.config import settings
from src.shared.logging import get_logger, get_tracer

logger = get_logger(__name__)
_tracer = get_tracer(__name__)

# Few-shot examples seeded from _CRITERIA_CATALOG
_FEW_SHOT_EXAMPLES = [
    {
        "category": "refrigerator",
        "criteria": {
            "noise_level": {"display_name": "Noise Level", "unit": "dB", "importance": "high", "description": "Operating noise in decibels"},
            "energy_rating": {"display_name": "Energy Rating", "unit": "", "importance": "high", "description": "Energy efficiency class"},
            "capacity": {"display_name": "Capacity", "unit": "L", "importance": "high", "description": "Internal volume in liters"},
            "price": {"display_name": "Price", "unit": "", "importance": "high", "description": ""},
            "dimensions": {"display_name": "Dimensions", "unit": "cm", "importance": "medium", "description": "Height x Width x Depth"},
            "weight": {"display_name": "Weight", "unit": "kg", "importance": "low", "description": "Total weight"},
            "freezer_type": {"display_name": "Freezer Type", "unit": "", "importance": "medium", "description": "Top, bottom, or side-by-side"},
            "frost_free": {"display_name": "Frost Free", "unit": "", "importance": "medium", "description": "No-frost technology"},
            "warranty": {"display_name": "Warranty", "unit": "years", "importance": "medium", "description": "Manufacturer warranty period"},
        },
    },
    {
        "category": "laptop",
        "criteria": {
            "processor": {"display_name": "Processor", "unit": "", "importance": "high", "description": "CPU model"},
            "ram": {"display_name": "RAM", "unit": "GB", "importance": "high", "description": "Memory capacity"},
            "storage": {"display_name": "Storage", "unit": "GB", "importance": "high", "description": "SSD/HDD capacity"},
            "screen_size": {"display_name": "Screen Size", "unit": "inches", "importance": "high", "description": "Display diagonal"},
            "price": {"display_name": "Price", "unit": "", "importance": "high", "description": ""},
            "battery_life": {"display_name": "Battery Life", "unit": "hours", "importance": "medium", "description": "Estimated battery hours"},
            "weight": {"display_name": "Weight", "unit": "kg", "importance": "medium", "description": "Device weight"},
        },
    },
    {
        "category": "headphones",
        "criteria": {
            "driver_size": {"display_name": "Driver Size", "unit": "mm", "importance": "medium", "description": "Speaker driver diameter"},
            "noise_cancelling": {"display_name": "Noise Cancelling", "unit": "", "importance": "high", "description": "ANC support"},
            "battery_life": {"display_name": "Battery Life", "unit": "hours", "importance": "high", "description": "Playback time"},
            "price": {"display_name": "Price", "unit": "", "importance": "high", "description": ""},
            "connectivity": {"display_name": "Connectivity", "unit": "", "importance": "high", "description": "Bluetooth, wired, etc."},
            "weight": {"display_name": "Weight", "unit": "g", "importance": "medium", "description": "Device weight"},
        },
    },
]

_SYSTEM_PROMPT = """\
You are a product comparison expert. Given a product category, return 5-12 \
criteria that shoppers typically compare when purchasing this product.

Always include "price" as a criterion. Each criterion must have:
- display_name: Human-readable name
- unit: Measurement unit (empty string if N/A)
- importance: "high", "medium", or "low"
- description: Brief description

Use snake_case keys. Return ONLY a JSON object mapping criterion keys to \
their specification objects. No markdown, no explanation.\
"""


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences from LLM output."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _validate_criteria_structure(data: object) -> dict[str, dict] | None:
    """Validate that LLM output has the expected criteria structure."""
    if not isinstance(data, dict):
        return None
    result: dict[str, dict] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        if "display_name" not in value:
            continue
        result[key] = {
            "display_name": value.get("display_name", key.replace("_", " ").title()),
            "unit": str(value.get("unit", "")),
            "importance": value.get("importance", "medium") if value.get("importance") in ("high", "medium", "low") else "medium",
            "description": str(value.get("description", "")),
        }
    if not result:
        return None
    return result


async def discover_criteria_via_llm(
    category: str,
    snippets: list[str] | None = None,
) -> dict[str, dict]:
    """Discover product criteria for an unknown category using an LLM.

    Returns an empty dict when the LLM API key is not configured or on any
    failure, ensuring graceful degradation.
    """
    if not settings.llm_api_key:
        return {}

    with _tracer.start_as_current_span(
        "llm_discover_criteria",
        attributes={"category": category},
    ) as span:
        try:
            examples_text = "\n\n".join(
                f"Category: {ex['category']}\n{json.dumps(ex['criteria'], indent=2)}"
                for ex in _FEW_SHOT_EXAMPLES
            )

            user_message = f"Category: {category}"
            if snippets:
                snippet_text = "\n".join(snippets[:5])
                user_message += f"\n\nHere are some web snippets about this product to help you identify relevant criteria:\n{snippet_text}"

            response = await litellm.acompletion(
                model=settings.llm_model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": f"Here are examples:\n\n{examples_text}"},
                    {"role": "assistant", "content": "I understand the format. Please give me a category and I'll return criteria as JSON."},
                    {"role": "user", "content": user_message},
                ],
                temperature=settings.llm_temperature,
                api_key=settings.llm_api_key,
            )

            raw_text = response.choices[0].message.content or ""
            span.set_attribute("raw_response_length", len(raw_text))

            cleaned = _strip_markdown_fences(raw_text)
            parsed = json.loads(cleaned)

            criteria = _validate_criteria_structure(parsed)
            if criteria is None:
                logger.warning("LLM returned invalid criteria structure for '%s'", category)
                span.set_attribute("result", "invalid_structure")
                return {}

            # Ensure price is always present
            if "price" not in criteria:
                criteria["price"] = {
                    "display_name": "Price",
                    "unit": "",
                    "importance": "high",
                    "description": "",
                }

            span.set_attribute("criteria_count", len(criteria))
            span.set_attribute("criteria_keys", json.dumps(list(criteria.keys())))
            span.set_attribute("result", "success")
            logger.info("LLM discovered %d criteria for '%s'", len(criteria), category)
            return criteria

        except Exception:
            logger.warning("LLM criteria discovery failed for '%s'", category, exc_info=True)
            span.set_attribute("result", "error")
            return {}


async def extract_query_attributes_via_llm(
    query: str,
    criteria: dict[str, dict],
) -> list[QueryAttribute]:
    """Extract user-intent attributes from a query using an LLM.

    Given the search query and available criteria keys, the LLM returns
    which criteria the user cares about and in which direction (low/high).

    Returns an empty list when the API key is not configured or on failure.
    """
    if not settings.llm_api_key:
        return []

    with _tracer.start_as_current_span(
        "llm_extract_attributes",
        attributes={"query": query},
    ) as span:
        try:
            criteria_desc = "\n".join(
                f"- {key}: {spec.get('display_name', key)} ({spec.get('unit', '')})"
                for key, spec in criteria.items()
            )

            response = await litellm.acompletion(
                model=settings.llm_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You extract user shopping preferences from a search query. "
                            "Given a query and a list of available criteria, return a JSON array "
                            "of objects with: criterion_key (must be from the list), direction "
                            '("low" or "high"), and display_label (short human-readable label). '
                            "Only include criteria the user clearly cares about. "
                            "Return ONLY the JSON array, no markdown."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Query: {query}\n\nAvailable criteria:\n{criteria_desc}"
                        ),
                    },
                ],
                temperature=settings.llm_temperature,
                api_key=settings.llm_api_key,
            )

            raw_text = response.choices[0].message.content or ""
            cleaned = _strip_markdown_fences(raw_text)
            parsed = json.loads(cleaned)

            if not isinstance(parsed, list):
                span.set_attribute("result", "invalid_format")
                return []

            attributes: list[QueryAttribute] = []
            seen_keys: set[str] = set()
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                key = item.get("criterion_key", "")
                direction = item.get("direction", "")
                label = item.get("display_label", "")
                # Only accept keys that exist in the criteria dict
                if key not in criteria:
                    continue
                if direction not in ("low", "high"):
                    continue
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                attributes.append(QueryAttribute(
                    criterion_key=key,
                    direction=direction,
                    display_label=label or criteria[key].get("display_name", key),
                ))

            span.set_attribute("attribute_count", len(attributes))
            span.set_attribute("result", "success")
            return attributes

        except Exception:
            logger.warning("LLM attribute extraction failed for '%s'", query, exc_info=True)
            span.set_attribute("result", "error")
            return []
