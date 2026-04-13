"""Quality validation for extraction results."""

from __future__ import annotations

from src.shared.market_config import get_garbage_names
from src.shared.models import ProductResult

from .helpers import _MAX_SANE_PRICE, _extraction_event


def validate_results(
    products: list[ProductResult],
    product_query: str,
    domain: str,
) -> list[ProductResult]:
    """Filter out low-quality extraction results."""
    garbage = get_garbage_names()
    valid: list[ProductResult] = []
    rejection_reasons: dict[str, int] = {}

    def _reject(reason: str) -> None:
        rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

    for p in products:
        if not p.name or len(p.name) < 3:
            _reject("name_too_short")
            continue
        # Normalize: collapse newlines/tabs to spaces
        p.name = " ".join(p.name.split())
        stripped_name = p.name.strip()
        if stripped_name in garbage:
            _reject("garbage_name")
            continue
        if stripped_name.endswith(":"):
            _reject("heading_name")
            continue
        if len(p.name) < 5 and not p.model_id:
            _reject("generic_short_name")
            continue

        if p.sellers and all(
            s.price is not None and (s.price > _MAX_SANE_PRICE or s.price < 0)
            for s in p.sellers
        ):
            _reject("insane_price")
            continue

        has_price = any(s.price is not None and s.price > 0 for s in p.sellers)
        has_url = any(s.url for s in p.sellers)
        if not has_price and not has_url:
            _reject("no_price_or_url")
            continue

        valid.append(p)

    if _is_low_quality_batch(valid):
        _extraction_event("validate", f"Batch rejected as low-quality (all {len(valid)} products share same name with no prices)", rejection_reasons=str(rejection_reasons))
        return []

    if len(valid) > 3:
        names = [p.name for p in valid]
        most_common = max(set(names), key=names.count)
        if names.count(most_common) > len(names) * 0.5:
            if len(most_common) < 30 and not any(
                s.price is not None and s.price > 0
                for p in valid for s in p.sellers
            ):
                _extraction_event("validate", f"Batch rejected: >50% share name '{most_common[:40]}' with no prices")
                return []

    _extraction_event("validate",
        f"{len(products)} input -> {len(valid)} valid, rejected: {rejection_reasons}" if rejection_reasons else f"All {len(valid)} products passed validation",
        input_count=len(products),
        valid_count=len(valid),
        rejection_reasons=str(rejection_reasons),
    )

    return valid


def _is_low_quality_batch(products: list[ProductResult]) -> bool:
    """Check if a batch of products looks like nav/UI elements."""
    if len(products) < 2:
        return False
    names = [p.name for p in products]
    most_common = max(set(names), key=names.count)
    if names.count(most_common) >= len(products) * 0.5:
        has_any_price = any(
            s.price is not None
            for p in products for s in p.sellers
        )
        if not has_any_price:
            return True
    return False
