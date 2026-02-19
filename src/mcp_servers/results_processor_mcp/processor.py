"""Results processing: validation, seller aggregation, and formatting."""

from __future__ import annotations

import re
from collections import defaultdict
from urllib.parse import urlparse

from src.shared.logging import get_logger
from src.shared.models import ProductResult, Seller

logger = get_logger(__name__)

_MAX_RESULTS = 20


def _extract_domain(url: str) -> str:
    """Extract domain from URL, stripping 'www.' prefix."""
    if not url:
        return ""
    parsed = urlparse(url)
    domain = parsed.hostname or ""
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _normalize_name(name: str) -> str:
    """Normalize a product name for fuzzy comparison."""
    text = name.lower().strip()
    # Remove common brand prefixes and filler words
    text = re.sub(r"\s+", " ", text)
    # Remove special characters except spaces
    text = re.sub(r"[^\w\s]", "", text)
    return text.strip()


def _names_match(a: str, b: str) -> bool:
    """Check if two product names are similar enough to be the same product.

    Uses token overlap: if 60%+ of tokens from the shorter name appear
    in the longer name, they're considered a match.
    """
    norm_a = _normalize_name(a)
    norm_b = _normalize_name(b)

    if norm_a == norm_b:
        return True

    tokens_a = set(norm_a.split())
    tokens_b = set(norm_b.split())

    if not tokens_a or not tokens_b:
        return False

    shorter = tokens_a if len(tokens_a) <= len(tokens_b) else tokens_b
    longer = tokens_b if len(tokens_a) <= len(tokens_b) else tokens_a

    if len(shorter) < 2:
        return False

    overlap = len(shorter & longer)
    return overlap / len(shorter) >= 0.6


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_results(
    results: list[ProductResult],
    criteria: dict[str, dict] | None = None,
) -> list[dict]:
    """Validate product results and score completeness.

    Returns a list of dicts with the original product plus validation metadata:
    - ``valid``: bool - has name and at least one seller with a price
    - ``completeness``: float - fraction of criteria fields populated
    - ``warnings``: list of warning strings
    """
    validated: list[dict] = []

    criteria_keys = set(criteria.keys()) if criteria else set()

    for product in results:
        warnings: list[str] = []
        valid = True

        if not product.name:
            warnings.append("missing_name")
            valid = False

        has_priced_seller = any(s.price is not None for s in product.sellers)
        if not has_priced_seller:
            warnings.append("no_price")

        has_seller_url = any(s.url for s in product.sellers)
        if not has_seller_url:
            warnings.append("no_seller_url")

        # Score completeness against criteria
        completeness = 0.0
        if criteria_keys:
            matched = 0
            for key in criteria_keys:
                if key == "price" and has_priced_seller:
                    matched += 1
                elif key in product.criteria and product.criteria[key]:
                    matched += 1
                elif key == "dimensions" and product.criteria.get("dimensions"):
                    matched += 1
            completeness = matched / len(criteria_keys)

        validated.append({
            "product": product,
            "valid": valid,
            "completeness": completeness,
            "warnings": warnings,
        })

    return validated


def aggregate_sellers(results: list[ProductResult]) -> list[ProductResult]:
    """Merge products that appear to be the same item from different sites.

    Groups by ``model_id`` (when non-hash) or by fuzzy name matching.
    Merges seller lists, deduplicates by domain+price.
    """
    if not results:
        return []

    groups: dict[str, list[ProductResult]] = defaultdict(list)
    ungrouped: list[ProductResult] = []

    # First pass: group by model_id (if it looks like a real MPN, not a hash)
    for product in results:
        mid = product.model_id or ""
        # Hash-based IDs are 12-char hex — skip those for grouping
        if mid and not re.fullmatch(r"[0-9a-f]{12}", mid):
            groups[mid].append(product)
        else:
            ungrouped.append(product)

    # Second pass: fuzzy-match ungrouped products
    fuzzy_groups: list[list[ProductResult]] = []
    for product in ungrouped:
        matched = False
        for group in fuzzy_groups:
            if _names_match(product.name, group[0].name):
                group.append(product)
                matched = True
                break
        if not matched:
            fuzzy_groups.append([product])

    # Merge each group into a single ProductResult
    merged: list[ProductResult] = []

    for group_products in list(groups.values()) + fuzzy_groups:
        merged.append(_merge_group(group_products))

    return merged


def _merge_group(products: list[ProductResult]) -> ProductResult:
    """Merge a group of same-product results into one."""
    if len(products) == 1:
        return products[0]

    # Use the first product as base (most fields)
    base = products[0]
    all_sellers: list[Seller] = []
    seen_sellers: set[tuple[str, float | None]] = set()

    best_image = base.image_url
    best_brand = base.brand
    merged_criteria = dict(base.criteria)

    for product in products:
        for seller in product.sellers:
            domain = _extract_domain(seller.url or "")
            key = (domain, seller.price)
            if key not in seen_sellers:
                seen_sellers.add(key)
                all_sellers.append(seller)

        # Prefer non-None values
        if not best_image and product.image_url:
            best_image = product.image_url
        if not best_brand and product.brand:
            best_brand = product.brand

        # Merge criteria
        for k, v in product.criteria.items():
            if k not in merged_criteria and v:
                merged_criteria[k] = v

    # Sort sellers by price (None-priced last)
    all_sellers.sort(key=lambda s: (s.price is None, s.price or 0))

    return ProductResult(
        name=base.name,
        model_id=base.model_id,
        brand=best_brand,
        product_type=base.product_type,
        category=base.category,
        criteria=merged_criteria,
        sellers=all_sellers,
        image_url=best_image,
    )


def format_results(
    results: list[ProductResult],
    format_type: str = "single_product",
) -> dict:
    """Format results for display.

    Returns a dict with formatted results and metadata.

    Format types:
    - ``single_product``: Sort by best price, include source count
    - ``multi_product``: Group by category/type
    - ``price_comparison``: Sort by price across all sellers
    - ``matched_set``: Group matched pairs (future)
    """
    # Count distinct seller domains across all results
    all_domains: set[str] = set()
    for product in results:
        for seller in product.sellers:
            domain = _extract_domain(seller.url or "") or seller.name
            if domain:
                all_domains.add(domain)

    capped = results[:_MAX_RESULTS]

    if format_type == "price_comparison":
        capped.sort(key=lambda p: _best_price(p))
    elif format_type == "multi_product":
        capped.sort(key=lambda p: (p.category or "", p.brand or "", _best_price(p)))
    else:
        # single_product / matched_set: sort by best price
        capped.sort(key=lambda p: _best_price(p))

    formatted_products: list[dict] = []
    for product in capped:
        seller_domains = set()
        for s in product.sellers:
            d = _extract_domain(s.url or "") or s.name
            if d:
                seller_domains.add(d)

        formatted_products.append({
            "product": product.model_dump(),
            "source_count": len(seller_domains),
            "best_price": _best_price_value(product),
            "best_currency": _best_currency(product),
        })

    return {
        "products": formatted_products,
        "total_count": len(results),
        "displayed_count": len(capped),
        "source_count": len(all_domains),
        "format_type": format_type,
    }


def _best_price(product: ProductResult) -> tuple[bool, float]:
    """Sort key: (has_no_price, price). Products without prices sort last."""
    for seller in product.sellers:
        if seller.price is not None:
            return (False, seller.price)
    return (True, 0.0)


def _best_price_value(product: ProductResult) -> float | None:
    """Return the lowest price across sellers, or None."""
    prices = [s.price for s in product.sellers if s.price is not None]
    return min(prices) if prices else None


def _best_currency(product: ProductResult) -> str:
    """Return the currency of the best-priced seller."""
    for seller in product.sellers:
        if seller.price is not None:
            return seller.currency
    return "USD"
