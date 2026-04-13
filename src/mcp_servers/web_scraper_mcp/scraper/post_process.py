"""Post-processing: validation, merging, and caching."""

from __future__ import annotations

from src.mcp_servers.web_scraper_mcp.db_cache import save_strategy
from src.mcp_servers.web_scraper_mcp.diagnostics import ExtractionResult
from src.mcp_servers.web_scraper_mcp.strategy import ScrapingStrategy
from src.shared.logging import get_logger
from src.shared.models import ProductResult, Seller

from .helpers import _MAX_PRODUCTS_PER_SITE, _pipeline_event

logger = get_logger(__name__)


def _post_process(
    products: list[ProductResult],
    product_query: str,
    domain: str,
) -> list[ProductResult]:
    """Merge comparison sellers and apply pagination limits."""
    initial_count = len(products)
    if products and product_query:
        products = _merge_comparison_sellers(products, product_query, domain)

    if product_query and products:
        query_lower = product_query.lower()
        query_tokens = [t for t in query_lower.split() if len(t) >= 3]

        # Tokens like "pro", "max", "air" match too many unrelated products.
        # Significant = length >= 4, or all-caps (acronyms like "IPL").
        _GENERIC_TOKENS = {
            "pro", "max", "air", "new", "mini", "plus", "one", "lite",
            "neo", "duo", "the", "for", "and", "with",
        }
        significant = [
            t for t in query_tokens
            if len(t) >= 4 or t.upper() == t or t not in _GENERIC_TOKENS
        ]
        generic = [t for t in query_tokens if t not in significant]

        def _is_relevant(p: ProductResult) -> bool:
            name_l = p.name.lower()
            mid_l = p.model_id.lower() if p.model_id else ""
            text = f"{name_l} {mid_l}"
            if query_lower in text:
                return True
            sig_hits = sum(1 for t in significant if t in text)
            if sig_hits >= 2:
                return True
            if sig_hits >= 1:
                gen_hits = sum(1 for t in generic if t in text)
                if gen_hits >= 1 or len(significant) <= 1:
                    return True
            return False

        matched = [p for p in products if _is_relevant(p)]
        if matched:
            filtered_count = len(products) - len(matched)
            if filtered_count > 0:
                _pipeline_event(domain, "post_process",
                    f"Relevance filter: {len(matched)}/{len(products)} match query '{product_query}', dropped {filtered_count} unrelated",
                    matched_count=len(matched), filtered_count=filtered_count)
            products = matched
        elif len(products) == 1 and "|" not in products[0].name:
            _pipeline_event(domain, "post_process",
                f"Single product kept despite no name match (likely different language/script)")
        else:
            _pipeline_event(domain, "post_process",
                f"All {len(products)} products dropped: none match query '{product_query}'")
            products = []

    final = products[:_MAX_PRODUCTS_PER_SITE]
    _pipeline_event(domain, "post_process",
        f"Post-processing: {initial_count} input -> {len(final)} output",
        input_count=initial_count, output_count=len(final))
    return final


async def _cache_success(
    domain: str,
    page_type: str,
    result: ExtractionResult,
    url: str = "",
) -> None:
    """Cache the winning access+extraction method for future use."""
    if result.access_method in ("httpx", "curl_cffi"):
        strategy = ScrapingStrategy(
            product_container="",
            discovery_method=result.extraction_method,
            access_method=result.access_method,
            extraction_method=result.extraction_method,
            last_successful_url=url,
            consecutive_failures=0,
            validation_failures=0,
            block_type="",
            blocked_at="",
        )
        await save_strategy(domain, strategy, page_type)
        logger.info(
            "Cached strategy for %s: %s/%s",
            domain, result.access_method, result.extraction_method,
        )


def _merge_comparison_sellers(
    products: list[ProductResult],
    product_query: str,
    domain: str,
) -> list[ProductResult]:
    """Merge multiple single-seller products into one multi-seller product.

    On price-comparison pages each extracted "product" is really one seller
    row for the same product.
    """
    if len(products) < 2:
        return products

    if not all(len(p.sellers) == 1 and p.sellers[0].price is not None for p in products):
        _pipeline_event(domain, "merge_sellers",
            f"Skip merge: not all {len(products)} products have exactly 1 priced seller")
        return products

    query_lower = product_query.lower()
    has_query_match = any(
        query_lower in p.name.lower() or
        (p.model_id and query_lower in p.model_id.lower())
        for p in products
    )
    if has_query_match:
        _pipeline_event(domain, "merge_sellers",
            f"Skip merge: query '{product_query}' found in product names (distinct products, not comparison rows)")
        return products

    names = [p.name for p in products]
    unique_names = set(names)
    all_same = len(unique_names) == 1
    all_unique = len(unique_names) == len(names)

    if not (all_same or all_unique):
        _pipeline_event(domain, "merge_sellers",
            f"Skip merge: names are neither all-same nor all-unique ({len(unique_names)} unique out of {len(names)})")
        return products

    # On comparison pages, "names" are short seller/store names (median ~10
    # chars).  On search result pages, names are long product descriptions
    # (median ~47 chars).  Use median name length to distinguish.
    _MAX_SELLER_NAME_LENGTH = 25
    if all_unique and not has_query_match:
        median_len = sorted(len(n) for n in names)[len(names) // 2]
        if median_len > _MAX_SELLER_NAME_LENGTH:
            _pipeline_event(domain, "merge_sellers",
                f"Skip merge: median name length {median_len} > {_MAX_SELLER_NAME_LENGTH} "
                f"indicates product descriptions, not seller names")
            return products

    sellers: list[Seller] = []
    for p in products:
        seller = p.sellers[0]
        if all_unique:
            seller.name = p.name
        sellers.append(seller)

    merged = ProductResult(
        name=product_query,
        model_id=product_query,
        brand=products[0].brand,
        image_url=products[0].image_url,
        sellers=sellers,
    )

    pattern = "all-same-name" if all_same else "all-unique-names (seller rows)"
    _pipeline_event(domain, "merge_sellers",
        f"Merged {len(products)} comparison rows into 1 product with {len(sellers)} sellers (pattern: {pattern})",
        merged_count=len(products), seller_count=len(sellers))

    logger.info(
        "Merged %d comparison rows into 1 product with %d sellers for %s",
        len(products), len(sellers), domain,
    )
    return [merged]
