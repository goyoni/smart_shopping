"""E-commerce site detection and classification."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_KNOWN_ECOMMERCE_DOMAINS: set[str] = {
    # Global
    "amazon.com", "amazon.co.uk", "amazon.de", "amazon.fr",
    "ebay.com", "ebay.co.uk", "ebay.de",
    "aliexpress.com", "walmart.com", "target.com",
    "bestbuy.com", "newegg.com", "etsy.com",
    # Israel
    "zap.co.il", "ksp.co.il", "bug.co.il", "ivory.co.il",
    "lastprice.co.il", "wisebuy.co.il", "machsanei-hashmal.co.il",
    "next.co.il", "shufersal.co.il",
}

_NON_ECOMMERCE_DOMAINS: set[str] = {
    "youtube.com", "wikipedia.org", "reddit.com", "facebook.com",
    "twitter.com", "x.com", "instagram.com", "linkedin.com",
    "tiktok.com", "pinterest.com", "quora.com", "medium.com",
    "github.com", "stackoverflow.com", "bbc.com", "cnn.com",
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
) -> list[EcommerceSignal]:
    """Filter and sort URLs by e-commerce confidence.

    Args:
        urls_data: List of dicts with 'url', optionally 'title' and 'snippet'.

    Returns:
        E-commerce URLs sorted by confidence descending.
    """
    results: list[EcommerceSignal] = []

    for item in urls_data:
        url = item.get("url", "")
        title = item.get("title", "")
        snippet = item.get("snippet", "")
        signal = detect_ecommerce(url, title, snippet)
        if signal.is_ecommerce:
            results.append(signal)

    results.sort(key=lambda s: s.confidence, reverse=True)
    return results
