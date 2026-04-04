"""E-commerce site detection and classification.

Uses a combination of:
- DB-backed known domains (seeded from defaults, grows via search results and scrape outcomes)
- URL path patterns
- Universal ecommerce keywords (language-agnostic where possible)
- Market TLD matching
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from opentelemetry import trace as otel_trace
from sqlalchemy import select

from src.shared.logging import get_logger
from src.shared.market_config import get_market_tld, get_marketplace_domain_map

logger = get_logger(__name__)

# Seed domains — loaded into DB on first access, then DB is authoritative.
_SEED_ECOMMERCE_DOMAINS: dict[str, str] = {
    # Global (market="global")
    "amazon.com": "global", "amazon.co.uk": "global", "amazon.de": "global",
    "amazon.fr": "global", "ebay.com": "global", "ebay.co.uk": "global",
    "ebay.de": "global", "aliexpress.com": "global", "walmart.com": "global",
    "target.com": "global", "bestbuy.com": "global", "newegg.com": "global",
    "etsy.com": "global", "ikea.com": "global",
    # Israel
    "zap.co.il": "il", "ksp.co.il": "il", "bug.co.il": "il",
    "ivory.co.il": "il", "lastprice.co.il": "il", "wisebuy.co.il": "il",
    "machsanei-hashmal.co.il": "il", "next.co.il": "il",
    "shufersal.co.il": "il", "homecenter.co.il": "il",
    "ace.co.il": "il", "hamashbir.co.il": "il", "terminal-x.com": "il",
    "mega.co.il": "il", "rami-levy.co.il": "il",
    # Germany
    "otto.de": "de", "mediamarkt.de": "de", "saturn.de": "de",
    # France
    "fnac.com": "fr", "cdiscount.com": "fr", "darty.com": "fr",
}

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


# -- In-memory cache for DB-backed known domains --
_known_domains_cache: dict[str, set[str]] = {}  # market -> set of domains
_cache_timestamp: float = 0.0
_CACHE_TTL = 300.0  # 5 minutes
_seeded = False


def _reset_cache() -> None:
    """Reset module-level caches (for testing)."""
    global _seeded, _known_domains_cache, _cache_timestamp
    _seeded = False
    _known_domains_cache.clear()
    _cache_timestamp = 0.0


async def _seed_known_domains() -> None:
    """Seed the DB with default known ecommerce domains (once)."""
    global _seeded
    if _seeded:
        return
    _seeded = True

    from src.backend.db.engine import async_session
    from src.backend.db.models import KnownEcommerceDomain

    async with async_session() as session:
        result = await session.execute(
            select(KnownEcommerceDomain.domain).limit(1)
        )
        if result.scalar_one_or_none() is not None:
            return  # Already seeded

        for domain, market in _SEED_ECOMMERCE_DOMAINS.items():
            session.add(KnownEcommerceDomain(
                domain=domain,
                market=market,
                source="seed",
                hit_count=10,  # High initial count so seeds are always trusted
                success_count=5,
            ))
        await session.commit()
        logger.info("Seeded %d known ecommerce domains", len(_SEED_ECOMMERCE_DOMAINS))


async def _load_known_domains(market: str | None = None) -> set[str]:
    """Load known ecommerce domains from DB (cached in memory).

    Falls back to seed domains if the DB is unavailable.
    """
    global _known_domains_cache, _cache_timestamp

    now = time.monotonic()
    cache_key = market or "__all__"
    if cache_key in _known_domains_cache and (now - _cache_timestamp) < _CACHE_TTL:
        return _known_domains_cache[cache_key]

    try:
        await _seed_known_domains()

        from src.backend.db.engine import async_session
        from src.backend.db.models import KnownEcommerceDomain

        async with async_session() as session:
            stmt = select(KnownEcommerceDomain.domain).where(
                # Domains with hit_count >= 2 or success_count >= 1 are trusted
                (KnownEcommerceDomain.hit_count >= 2) | (KnownEcommerceDomain.success_count >= 1)
            )
            if market:
                # Include both market-specific and global domains
                stmt = stmt.where(
                    KnownEcommerceDomain.market.in_([market, "global"])
                )
            result = await session.execute(stmt)
            domains = {row[0] for row in result.all()}
    except Exception:
        logger.debug("DB unavailable for known domains, using seed list")
        domains = set(_SEED_ECOMMERCE_DOMAINS.keys())

    _known_domains_cache[cache_key] = domains
    _cache_timestamp = now
    return domains


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
    known_domains: set[str] | None = None,
) -> EcommerceSignal:
    """Score a URL for e-commerce likelihood using multiple signals.

    Args:
        known_domains: Pre-loaded set of known ecommerce domains from DB.
            If None, only heuristic signals are used.

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

    # Known e-commerce domain (from DB)
    if known_domains:
        for ec_domain in known_domains:
            if domain == ec_domain or domain.endswith(f".{ec_domain}"):
                confidence += 0.8
                signals.append(f"known_ecommerce:{ec_domain}")
                break

    # URL path patterns
    path = urlparse(url).path.lower()
    for pattern in _ECOMMERCE_PATH_PATTERNS:
        if pattern in path:
            confidence += 0.3
            signals.append(f"path_pattern:{pattern.strip('/')}")
            break

    # Keyword analysis in title and snippet
    combined_text = f"{title} {snippet}".lower()
    keyword_score = 0.0
    matched_keywords: list[str] = []

    for keyword in _ECOMMERCE_KEYWORDS:
        if keyword in combined_text:
            keyword_score += 0.1
            matched_keywords.append(keyword)
            if keyword_score >= 0.4:
                break

    if matched_keywords:
        confidence += min(keyword_score, 0.4)
        signals.append(f"keywords:{','.join(matched_keywords[:3])}")

    is_ecommerce = confidence >= 0.3

    return EcommerceSignal(
        url=url, domain=domain, is_ecommerce=is_ecommerce,
        confidence=round(confidence, 2), signals=signals,
    )


async def identify_ecommerce_sites(
    urls_data: list[dict[str, str]],
    market: str | None = None,
) -> list[EcommerceSignal]:
    """Filter and sort URLs by e-commerce confidence.

    Loads known domains from DB, then applies heuristic scoring.
    After classification, records hits for ecommerce domains.

    Args:
        urls_data: List of dicts with 'url', optionally 'title' and 'snippet'.
        market: Target market code (e.g. 'il').

    Returns:
        E-commerce URLs sorted by confidence descending.
    """
    known_domains = await _load_known_domains(market)

    results: list[EcommerceSignal] = []
    market_tld = get_market_tld(market) if market else None
    marketplace_domains = get_marketplace_domain_map()

    for item in urls_data:
        url = item.get("url", "")
        title = item.get("title", "")
        snippet = item.get("snippet", "")
        signal = detect_ecommerce(url, title, snippet, known_domains=known_domains)
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
                    signal.confidence = min(signal.confidence + 0.2, 1.0)
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
            "known_domains_count": len(known_domains),
            "summary": f"{len(results)}/{len(urls_data)} URLs classified as ecommerce. Top: {', '.join(s.domain for s in results[:5])}",
        })

    return results
