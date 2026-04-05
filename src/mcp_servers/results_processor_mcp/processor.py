"""Results processing: validation, seller aggregation, and formatting."""

from __future__ import annotations

import re
from collections import defaultdict
from urllib.parse import urlparse

from opentelemetry import trace as otel_trace

from src.shared.models import QueryAttribute
from src.shared.logging import get_logger
from src.shared.models import CrossSeller, ProductResult, Seller

logger = get_logger(__name__)


def _processor_event(step: str, detail: str, **attrs: object) -> None:
    """Record a processing step on the active OTEL span."""
    span = otel_trace.get_current_span()
    if span and span.is_recording():
        span.add_event(f"processor.{step}", {"detail": detail, **attrs})

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

    invalid_count = 0
    warning_counts: dict[str, int] = {}
    for product in results:
        warnings: list[str] = []
        valid = True

        if not product.name:
            warnings.append("missing_name")
            valid = False

        has_priced_seller = any(s.price is not None for s in product.sellers)
        if not has_priced_seller:
            warnings.append("no_price")
            valid = False

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

        if not valid:
            invalid_count += 1
        for w in warnings:
            warning_counts[w] = warning_counts.get(w, 0) + 1

        validated.append({
            "product": product,
            "valid": valid,
            "completeness": completeness,
            "warnings": warnings,
        })

    valid_count = len(results) - invalid_count
    _processor_event("validate",
        f"{len(results)} products: {valid_count} valid, {invalid_count} invalid. Warnings: {warning_counts}",
        valid_count=valid_count,
        invalid_count=invalid_count,
        warning_counts=str(warning_counts),
    )

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

    _processor_event("aggregate",
        f"{len(results)} products -> {len(merged)} deduplicated ({len(groups)} by model_id, {len(fuzzy_groups)} by fuzzy name). "
        f"Largest group: {max(len(g) for g in list(groups.values()) + fuzzy_groups) if groups or fuzzy_groups else 0} products",
        input_count=len(results),
        output_count=len(merged),
        model_id_groups=len(groups),
        fuzzy_groups=len(fuzzy_groups),
    )

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


def _attribute_sort_key(
    product: ProductResult,
    attributes: list[QueryAttribute],
) -> tuple[int, float]:
    """Build a sort key based on how well a product matches user attributes.

    Returns ``(missing_count, price)`` so products with more matching
    criteria rank first, with price as tie-breaker.

    For numeric criteria values:
    - ``direction="low"``: lower values score better (fewer missing points)
    - ``direction="high"``: higher values score better
    Non-numeric criteria values count as present (0 missing points).
    """
    missing = 0
    for attr in attributes:
        key = attr.criterion_key
        if key == "price":
            # Price direction handled via price tie-break
            continue
        value = product.criteria.get(key)
        if not value:
            missing += 1

    _, price = _best_price(product)
    # If user wants low price, sort ascending (default). If high, invert.
    price_wants_high = any(
        a.criterion_key == "price" and a.direction == "high" for a in attributes
    )
    price_key = -price if price_wants_high else price

    return (missing, price_key)


def format_results(
    results: list[ProductResult],
    format_type: str = "single_product",
    user_attributes: list[QueryAttribute] | None = None,
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

    if user_attributes:
        # Sort by attribute preference score when user intent is available
        capped.sort(key=lambda p: _attribute_sort_key(p, user_attributes))
    elif format_type == "price_comparison":
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

    result = {
        "products": formatted_products,
        "total_count": len(results),
        "displayed_count": len(capped),
        "source_count": len(all_domains),
        "format_type": format_type,
    }

    sort_method = "attribute_preference" if user_attributes else format_type
    _processor_event("format",
        f"Formatted {len(capped)}/{len(results)} products (sort={sort_method}, sources={len(all_domains)})",
        displayed_count=len(capped),
        total_count=len(results),
        sort_method=sort_method,
    )

    return result


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


def find_missing_models(
    products: list[ProductResult],
    model_ids: list[str],
) -> dict[str, list[str]]:
    """Identify which models are missing per seller domain.

    Builds a seller→models map from existing products and returns a dict
    mapping each seller domain to the list of model IDs it does NOT carry.
    Only returns sellers that carry at least one model (potential cross-sellers).

    Returns:
        ``{seller_domain: [missing_model_ids]}``
    """
    # Build map: domain → set of model IDs this seller carries
    domain_models: dict[str, set[str]] = defaultdict(set)
    # Also track which URL we have per domain (for reference)
    domain_urls: dict[str, str] = {}

    model_set = set(m.lower() for m in model_ids)

    for product in products:
        ptype = (product.product_type or "").lower()
        if ptype not in model_set:
            continue
        for seller in product.sellers:
            domain = _extract_domain(seller.url or "") or seller.name
            if not domain:
                continue
            domain_models[domain].add(ptype)
            if seller.url and domain not in domain_urls:
                domain_urls[domain] = seller.url

    # For each seller, find which models are missing
    missing: dict[str, list[str]] = {}
    for domain, carried in domain_models.items():
        missing_ids = [mid for mid in model_ids if mid.lower() not in carried]
        if missing_ids:
            missing[domain] = missing_ids

    _processor_event("find_missing",
        f"{len(domain_models)} sellers found. {len(missing)} have gaps: " +
        ", ".join(f"{d}: missing {ids}" for d, ids in list(missing.items())[:5]),
        seller_count=len(domain_models),
        sellers_with_gaps=len(missing),
    )

    return missing


_MAX_CROSS_SELLERS = 10


def find_cross_sellers(
    products: list[ProductResult],
) -> list[CrossSeller]:
    """Identify sellers that carry multiple products from a multi-model search.

    Groups products by ``product_type`` (the model ID), then finds seller
    domains appearing across 2+ groups.  Returns up to 10 cross-sellers
    sorted by number of products carried (descending), then by total price.
    """
    # Group products by model (product_type)
    by_model: dict[str, list[ProductResult]] = defaultdict(list)
    for p in products:
        key = p.product_type or p.model_id or p.name
        by_model[key].append(p)

    if len(by_model) < 2:
        return []

    # For each seller domain, track which models it carries and best price per model
    # seller_domain -> { model_id -> (best_price, currency, seller) }
    domain_models: dict[str, dict[str, tuple[float | None, str, Seller]]] = defaultdict(dict)

    for model_key, model_products in by_model.items():
        for product in model_products:
            for seller in product.sellers:
                domain = _extract_domain(seller.url or "") or seller.name
                if not domain:
                    continue
                existing = domain_models[domain].get(model_key)
                # Keep the seller with the best (lowest) price for this model
                if existing is None or (
                    seller.price is not None
                    and (existing[0] is None or seller.price < existing[0])
                ):
                    domain_models[domain][model_key] = (seller.price, seller.currency, seller)

    # Filter to domains carrying 2+ models
    cross: list[CrossSeller] = []
    for domain, models in domain_models.items():
        if len(models) < 2:
            continue

        product_names = list(models.keys())
        prices: dict[str, float] = {}
        total: float = 0.0
        all_priced = True
        currency = "USD"
        # Pick contact info from the first seller with data
        url: str | None = None
        phone: str | None = None
        email: str | None = None

        for model_key, (price, cur, seller) in models.items():
            currency = cur
            if price is not None:
                prices[model_key] = price
                total += price
            else:
                all_priced = False
            if not url and seller.url:
                url = seller.url
            if not phone and seller.phone:
                phone = seller.phone
            if not email and seller.email:
                email = seller.email

        cross.append(CrossSeller(
            name=domain,
            domain=domain,
            url=url,
            phone=phone,
            email=email,
            products=product_names,
            prices=prices,
            total_price=total if all_priced else None,
            currency=currency,
        ))

    # Sort: most products first, then lowest total price
    cross.sort(key=lambda c: (-len(c.products), c.total_price or float("inf")))

    _processor_event("cross_sellers",
        f"Found {len(cross)} cross-sellers from {len(domain_models)} seller domains. "
        f"Top: {', '.join(f'{c.domain}({len(c.products)} products)' for c in cross[:5])}",
        cross_seller_count=len(cross),
    )

    return cross[:_MAX_CROSS_SELLERS]
