"""E-commerce site detection and classification.

Uses heuristic signals from search results (URL path patterns, title/snippet
keywords, market TLD matching) to score URLs.  No hardcoded domain list —
the web search engine is trusted to surface relevant sites for any market.

Domain learning (record_domain_hit / record_domain_success) persists
observed ecommerce domains to DB for analytics; it does NOT influence scoring.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from urllib.parse import urlparse

from sqlalchemy import select

from opentelemetry import trace as otel_trace

from src.shared.logging import get_logger
from src.shared.market_config import get_market_tld, get_marketplace_domain_map

logger = get_logger(__name__)

_NON_ECOMMERCE_DOMAINS: set[str] = {
    "youtube.com", "wikipedia.org", "reddit.com", "facebook.com",
    "twitter.com", "x.com", "instagram.com", "linkedin.com",
    "tiktok.com", "pinterest.com", "quora.com", "medium.com",
    "github.com", "stackoverflow.com", "bbc.com", "cnn.com",
    # Manual / documentation sites
    "manualslib.com", "manuals.co.uk", "manua.ls",
}

_ECOMMERCE_PATH_PATTERNS: list[str] = [
    "/products/", "/product/", "/shop/", "/store/",
    "/dp/", "/item/", "/buy/", "/p/", "/catalog/",
    "/collections/", "/listing/",
]

# Universal ecommerce keywords — work across languages via common patterns
# found in URLs, structured data, and page content.
_ECOMMERCE_KEYWORDS: list[str] = [
    # English (lingua franca of the web)
    "price", "buy", "shop", "add to cart", "in stock",
    "free shipping", "delivery", "order",
    # Common patterns that appear in any language (currency symbols, numbers)
    "€", "$", "₪", "£", "¥",
]


async def record_domain_hit(domain: str, market: str, source: str = "search") -> None:
    """Record that a domain was seen in search results for a market.

    Increments hit_count if it exists, otherwise creates a new entry.
    """
    try:
        from src.backend.db.engine import async_session
        from src.backend.db.models import KnownEcommerceDomain

        async with async_session() as session:
            stmt = select(KnownEcommerceDomain).where(
                KnownEcommerceDomain.domain == domain,
                KnownEcommerceDomain.market == market,
            )
            result = await session.execute(stmt)
            record = result.scalar_one_or_none()

            if record:
                record.hit_count += 1
                if source != "search":
                    record.source = source
            else:
                session.add(KnownEcommerceDomain(
                    domain=domain,
                    market=market,
                    source=source,
                    hit_count=1,
                    success_count=0,
                ))
            await session.commit()
    except Exception:
        logger.debug("Failed to record domain hit for %s", domain)


async def record_domain_success(domain: str, market: str) -> None:
    """Record that a domain yielded priced products (scrape success)."""
    try:
        from src.backend.db.engine import async_session
        from src.backend.db.models import KnownEcommerceDomain

        async with async_session() as session:
            stmt = select(KnownEcommerceDomain).where(
                KnownEcommerceDomain.domain == domain,
                KnownEcommerceDomain.market == market,
            )
            result = await session.execute(stmt)
            record = result.scalar_one_or_none()

            if record:
                record.success_count += 1
                await session.commit()
            else:
                # Domain not yet tracked — create with a success
                session.add(KnownEcommerceDomain(
                    domain=domain,
                    market=market,
                    source="scrape",
                    hit_count=1,
                    success_count=1,
                ))
                await session.commit()
    except Exception:
        logger.debug("Failed to record domain success for %s", domain)


@dataclass
class EcommerceSignal:
    url: str
    domain: str
    is_ecommerce: bool
    confidence: float
    signals: list[str] = field(default_factory=list)


def extract_domain(url: str) -> str:
    """Extract domain from URL, stripping 'www.' prefix."""
    parsed = urlparse(url)
    domain = parsed.hostname or ""
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def detect_ecommerce(
    url: str,
    title: str = "",
    snippet: str = "",
) -> EcommerceSignal:
    """Score a URL for e-commerce likelihood using heuristic signals.

    Signals: URL path patterns, title/snippet keywords.
    Market TLD boosting is applied separately in identify_ecommerce_sites().

    Returns an EcommerceSignal with confidence score and contributing signals.
    Threshold for is_ecommerce: 0.3
    """
    domain = extract_domain(url)
    confidence = 0.0
    signals: list[str] = []

    # Fast rejection for known non-ecommerce
    for non_ec in _NON_ECOMMERCE_DOMAINS:
        if domain == non_ec or domain.endswith(f".{non_ec}"):
            return EcommerceSignal(
                url=url, domain=domain, is_ecommerce=False,
                confidence=0.0, signals=["known_non_ecommerce"],
            )

    # Reject manufacturer / brand sites (rarely have prices or add-to-cart)
    if _is_manufacturer_domain(domain):
        return EcommerceSignal(
            url=url, domain=domain, is_ecommerce=False,
            confidence=0.0, signals=["manufacturer_site"],
        )

    # URL path patterns
    path = urlparse(url).path.lower()
    for pattern in _ECOMMERCE_PATH_PATTERNS:
        if pattern in path:
            confidence += 0.4
            signals.append(f"path_pattern:{pattern.strip('/')}")
            break

    # Keyword analysis in title and snippet
    combined_text = f"{title} {snippet}".lower()
    keyword_score = 0.0
    matched_keywords: list[str] = []

    for keyword in _ECOMMERCE_KEYWORDS:
        if keyword in combined_text:
            keyword_score += 0.15
            matched_keywords.append(keyword)
            if keyword_score >= 0.6:
                break

    if matched_keywords:
        confidence += min(keyword_score, 0.6)
        signals.append(f"keywords:{','.join(matched_keywords[:3])}")

    is_ecommerce = confidence >= 0.3

    return EcommerceSignal(
        url=url, domain=domain, is_ecommerce=is_ecommerce,
        confidence=round(confidence, 2), signals=signals,
    )


def _is_manufacturer_domain(domain: str) -> bool:
    """Detect manufacturer/brand sites that aren't retail stores."""
    # Match brand.TLD or brand.country (e.g. braun.hu, samsung.com, lg.com)
    # but not brand stores like apple.com which also sell directly.
    # We use a short blocklist of common patterns.
    _MANUFACTURER_PATTERNS = (
        "braun.", "philips.", "samsung.", "lg.", "bosch.",
        "siemens.", "panasonic.", "sony.", "dyson.",
    )
    for pattern in _MANUFACTURER_PATTERNS:
        if domain.startswith(pattern) or f".{pattern}" in domain:
            return True
    return False


async def identify_ecommerce_sites(
    urls_data: list[dict[str, str]],
    market: str | None = None,
) -> list[EcommerceSignal]:
    """Filter and sort URLs by e-commerce confidence.

    Uses heuristic signals (path patterns, keywords) and market TLD boosting.
    No hardcoded domain list — trusts the search engine to surface relevant sites.

    Args:
        urls_data: List of dicts with 'url', optionally 'title' and 'snippet'.
        market: Target market code (e.g. 'il', 'gr').

    Returns:
        E-commerce URLs sorted by confidence descending.
    """
    results: list[EcommerceSignal] = []
    market_tld = get_market_tld(market) if market else None
    marketplace_domains = get_marketplace_domain_map()

    for item in urls_data:
        url = item.get("url", "")
        title = item.get("title", "")
        snippet = item.get("snippet", "")
        signal = detect_ecommerce(url, title, snippet)
        if signal.is_ecommerce:
            # Penalize country-specific marketplace domains that don't match
            # the target market (e.g. amazon.dk when searching in Israel)
            if market:
                domain_market = marketplace_domains.get(signal.domain)
                if domain_market and domain_market != market:
                    signal.confidence *= 0.1
                    signal.signals.append(f"market_mismatch:{signal.domain}!={market}")
                # Boost domains matching the target market TLD
                elif market_tld and signal.domain.endswith(market_tld):
                    signal.confidence = min(signal.confidence + 0.5, 1.5)
                    signal.signals.append(f"market_match:{market_tld}")
            results.append(signal)

    results.sort(key=lambda s: s.confidence, reverse=True)

    # Record domain hits in background (learn from search results)
    if market and results:
        async def _batch_record_hits() -> None:
            for signal in results:
                await record_domain_hit(signal.domain, market)

        asyncio.ensure_future(_batch_record_hits())

    # Record summary on current span
    span = otel_trace.get_current_span()
    if span and span.is_recording():
        classified = [(s.domain, s.confidence, s.signals) for s in results[:8]]
        rejected = [extract_domain(item.get("url", "")) for item in urls_data
                     if extract_domain(item.get("url", "")) not in {s.domain for s in results}]
        span.add_event("ecommerce.classification", {
            "input_urls": len(urls_data),
            "ecommerce_count": len(results),
            "rejected_count": len(rejected),
            "top_ecommerce": str(classified),
            "rejected_domains": str(rejected[:10]),
            "summary": f"{len(results)}/{len(urls_data)} URLs classified as ecommerce. Top: {', '.join(s.domain for s in results[:5])}",
        })

    return results
