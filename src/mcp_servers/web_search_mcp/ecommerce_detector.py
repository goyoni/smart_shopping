"""E-commerce site detection and classification."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from opentelemetry import trace as otel_trace

from src.shared.logging import get_logger
from src.shared.market_config import get_market_tld, get_marketplace_domain_map

logger = get_logger(__name__)

_KNOWN_ECOMMERCE_DOMAINS: set[str] = {
    # Global
    "amazon.com", "amazon.co.uk", "amazon.de", "amazon.fr",
    "ebay.com", "ebay.co.uk", "ebay.de",
    "aliexpress.com", "walmart.com", "target.com",
    "bestbuy.com", "newegg.com", "etsy.com",
    "ikea.com",
    # Israel
    "zap.co.il", "ksp.co.il", "bug.co.il", "ivory.co.il",
    "lastprice.co.il", "wisebuy.co.il", "machsanei-hashmal.co.il",
    "next.co.il", "shufersal.co.il", "homecenter.co.il",
    "ace.co.il", "hamashbir.co.il", "terminal-x.com",
    "mega.co.il", "rami-levy.co.il",
    # Germany
    "otto.de", "mediamarkt.de", "saturn.de",
    # France
    "fnac.com", "cdiscount.com", "darty.com",
}

# Country-code commercial TLDs that are *almost always* e-commerce or
# business sites.  A site using one of these suffixes gets a moderate
# confidence boost even if it is not in the known-domain list.
_COMMERCIAL_CC_TLDS: set[str] = {
    ".co.il", ".co.uk", ".com.au", ".com.br", ".co.jp",
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

_ECOMMERCE_KEYWORDS_EN: list[str] = [
    "price", "buy", "shop", "add to cart", "in stock",
    "free shipping", "delivery", "order",
]

_ECOMMERCE_KEYWORDS_HE: list[str] = [
    "מחיר", "קנה", "קנייה", "חנות", "הוסף לסל",
    "משלוח", "הזמנה", "במלאי",
]

_ECOMMERCE_KEYWORDS_AR: list[str] = [
    "سعر", "شراء", "متجر", "أضف إلى السلة",
    "شحن", "طلب",
]


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


def detect_ecommerce(url: str, title: str = "", snippet: str = "") -> EcommerceSignal:
    """Score a URL for e-commerce likelihood using multiple signals.

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

    # Known e-commerce domain
    for ec_domain in _KNOWN_ECOMMERCE_DOMAINS:
        if domain == ec_domain or domain.endswith(f".{ec_domain}"):
            confidence += 0.8
            signals.append(f"known_ecommerce:{ec_domain}")
            break

    # Commercial country-code TLD heuristic (e.g. .co.il, .co.uk)
    if not signals:  # only if not already matched as known domain
        for tld in _COMMERCIAL_CC_TLDS:
            if domain.endswith(tld):
                confidence += 0.4
                signals.append(f"commercial_tld:{tld}")
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

    all_keywords = _ECOMMERCE_KEYWORDS_EN + _ECOMMERCE_KEYWORDS_HE + _ECOMMERCE_KEYWORDS_AR
    matched_keywords: list[str] = []

    for keyword in all_keywords:
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


def identify_ecommerce_sites(
    urls_data: list[dict[str, str]],
    market: str | None = None,
) -> list[EcommerceSignal]:
    """Filter and sort URLs by e-commerce confidence.

    Args:
        urls_data: List of dicts with 'url', optionally 'title' and 'snippet'.
        market: Target market code (e.g. 'il'). When provided,
            country-specific marketplace variants (Amazon.dk, eBay.it, etc.)
            that don't match the target market are penalized.

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
                    signal.confidence = min(signal.confidence + 0.2, 1.0)
                    signal.signals.append(f"market_match:{market_tld}")
            results.append(signal)

    results.sort(key=lambda s: s.confidence, reverse=True)

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
