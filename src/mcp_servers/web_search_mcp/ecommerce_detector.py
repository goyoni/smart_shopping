"""E-commerce site detection and classification.

Uses a default-allow approach: the search engine is trusted to surface
relevant results for a shopping query, so most URLs are assumed to be
potential e-commerce sites.  Only known non-commerce categories (social
media, news, manufacturer homepages, forums, blogs) are rejected.

Positive signals (ecommerce path patterns, keywords) boost confidence
but are not required — an unknown site passes by default and the scraper
decides whether it actually has products.

Domain learning (record_domain_hit / record_domain_success) persists
observed ecommerce domains to DB for analytics; it does NOT influence scoring.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlparse

from sqlalchemy import select

from opentelemetry import trace as otel_trace

from src.shared.logging import get_logger
from src.shared.market_config import (
    get_all_market_codes, get_market_tld, get_marketplace_domain_map,
)

logger = get_logger(__name__)

# ── Rejection lists ──────────────────────────────────────────────────────

_NON_ECOMMERCE_DOMAINS: set[str] = {
    # Social media
    "youtube.com", "facebook.com", "twitter.com", "x.com",
    "instagram.com", "linkedin.com", "tiktok.com", "pinterest.com",
    # Knowledge / reference
    "wikipedia.org", "reddit.com", "quora.com", "medium.com",
    "stackoverflow.com", "github.com", "zhihu.com",
    # News / media
    "bbc.com", "cnn.com",
    # Manuals / documentation
    "manualslib.com", "manuals.co.uk", "manua.ls",
    # Medical / industrial (not consumer e-commerce)
    "bbraun.com", "bbraun.cz", "bbraunshop.cz",
}

_MANUFACTURER_PATTERNS: tuple[str, ...] = (
    "braun.", "philips.", "samsung.", "lg.", "bosch.",
    "siemens.", "panasonic.", "sony.", "dyson.",
)

_NON_ECOMMERCE_PATH_PATTERNS: tuple[str, ...] = (
    "/blog/", "/news/", "/article/", "/articles/",
    "/forum/", "/thread/", "/wiki/",
)

# ── Boost signals ────────────────────────────────────────────────────────

_ECOMMERCE_PATH_PATTERNS: list[str] = [
    "/products/", "/product/", "/shop/", "/store/",
    "/dp/", "/item/", "/items/", "/buy/", "/p/", "/catalog",
    "/collections/", "/listing/",
]

_BASE_CONFIDENCE = 0.5


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


def _is_manufacturer_domain(domain: str) -> bool:
    """Detect manufacturer/brand sites that aren't retail stores."""
    for pattern in _MANUFACTURER_PATTERNS:
        if domain.startswith(pattern) or f".{pattern}" in domain:
            return True
    return False


def detect_ecommerce(
    url: str,
    title: str = "",
    snippet: str = "",
) -> EcommerceSignal:
    """Score a URL for e-commerce likelihood.

    Default-allow: starts at base confidence and only rejects known
    non-commerce categories.  Positive signals boost confidence but
    are not required.  The scraper is the real filter.

    Returns an EcommerceSignal with confidence score and contributing signals.
    """
    domain = extract_domain(url)
    confidence = _BASE_CONFIDENCE
    signals: list[str] = ["default_allow"]

    # ── Hard rejections ──────────────────────────────────────────────
    for non_ec in _NON_ECOMMERCE_DOMAINS:
        if domain == non_ec or domain.endswith(f".{non_ec}"):
            return EcommerceSignal(
                url=url, domain=domain, is_ecommerce=False,
                confidence=0.0, signals=["known_non_ecommerce"],
            )

    if _is_manufacturer_domain(domain):
        return EcommerceSignal(
            url=url, domain=domain, is_ecommerce=False,
            confidence=0.0, signals=["manufacturer_site"],
        )

    # ── Penalties (hard reject for non-commerce content paths) ──────
    path = urlparse(url).path.lower()
    for pattern in _NON_ECOMMERCE_PATH_PATTERNS:
        if pattern in path:
            return EcommerceSignal(
                url=url, domain=domain, is_ecommerce=False,
                confidence=0.0,
                signals=[f"non_ecommerce_path:{pattern.strip('/')}"],
            )

    # ── Boosts (optional, not required) ──────────────────────────────
    for pattern in _ECOMMERCE_PATH_PATTERNS:
        if pattern in path:
            confidence += 0.4
            signals = [f"path_pattern:{pattern.strip('/')}"]
            break

    is_ecommerce = confidence > 0.0

    return EcommerceSignal(
        url=url, domain=domain, is_ecommerce=is_ecommerce,
        confidence=round(confidence, 2), signals=signals,
    )


@lru_cache(maxsize=1)
def _foreign_market_tlds() -> dict[str, str]:
    """Build mapping of country TLD -> market code for all configured markets.

    E.g. {".co.il": "il", ".de": "de", ".fr": "fr", ...}
    """
    mapping: dict[str, str] = {}
    for code in get_all_market_codes():
        tld = get_market_tld(code)
        if tld:
            mapping[tld] = code
    return mapping


def _is_foreign_market_tld(domain: str, market: str, market_tld: str | None) -> bool:
    """Check if domain has a TLD belonging to a different configured market."""
    # Don't penalize if it matches our own market TLD
    if market_tld and domain.endswith(market_tld):
        return False
    for tld, tld_market in _foreign_market_tlds().items():
        if tld_market != market and domain.endswith(tld):
            return True
    return False


async def identify_ecommerce_sites(
    urls_data: list[dict[str, str]],
    market: str | None = None,
) -> list[EcommerceSignal]:
    """Filter and sort URLs by e-commerce confidence.

    Default-allow approach: trusts the search engine to surface relevant
    sites.  Only rejects known non-commerce categories and foreign-market
    domains.

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
                # Penalize domains whose TLD belongs to a different market
                elif _is_foreign_market_tld(signal.domain, market, market_tld):
                    signal.confidence *= 0.15
                    signal.signals.append("foreign_market_tld")
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
