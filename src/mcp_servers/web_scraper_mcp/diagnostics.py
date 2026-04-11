"""Failure taxonomy and structured results for scraping attempts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from src.shared.models import ProductResult


class FailureType(Enum):
    """Classifies why a scraping attempt failed."""

    HTTP_BLOCKED = "http_blocked"
    CLOUDFLARE_JS = "cloudflare_js"
    CLOUDFLARE_CAPTCHA = "cloudflare_captcha"
    WAF_BLOCKED = "waf_blocked"
    EMPTY_PAGE = "empty_page"
    JS_SPA_NO_DATA = "js_spa_no_data"
    NO_PRODUCTS_FOUND = "no_products"
    LOW_QUALITY = "low_quality"
    NAVIGATION_FAILED = "nav_failed"


@dataclass
class ExtractionResult:
    """Outcome of a single (access_method, extraction_method) attempt."""

    products: list[ProductResult] = field(default_factory=list)
    access_method: str = ""
    extraction_method: str = ""
    failure_type: FailureType | None = None
    failure_detail: str = ""
    domain: str = ""
    page_type: str = ""

    @property
    def success(self) -> bool:
        return bool(self.products) and self.failure_type is None

    @property
    def is_blocked(self) -> bool:
        """True when the failure suggests a proxy might help."""
        return self.failure_type in _PROXY_WORTHY_FAILURES


_PROXY_WORTHY_FAILURES = frozenset({
    FailureType.HTTP_BLOCKED,
    FailureType.CLOUDFLARE_JS,
    FailureType.CLOUDFLARE_CAPTCHA,
    FailureType.WAF_BLOCKED,
})


# ── Language-agnostic block/captcha page detection ──────────────────

_BLOCK_KEYWORDS = (
    # English
    "access denied", "access blocked", "you have been blocked",
    "automated access", "bot detection", "security check",
    "verify you are human", "are you a robot",
    # Hebrew
    "הגישה נחסמה", "מזהה חסימה", "נרצה רק לאמת",
    "אינכם רובוט", "גישה נדחתה",
    # Arabic
    "تم حظر الوصول", "التحقق من أنك لست روبوت",
)

_CAPTCHA_WORDS = (
    "captcha", "verify", "challenge",
    "אמת", "רובוט",
    "التحقق",
)

_BLOCK_ID_RE = re.compile(
    r"(?:block[- ]?id|ray[- ]?id|מזהה חסימה)[:\s]*[a-f0-9-]{8,}",
    re.IGNORECASE,
)

_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

_PRODUCT_SIGNALS = (
    "price", "מחיר", "₪", "$", "€", "cart", "סל", "add to", "הוסף",
)


_SCRIPT_STYLE_RE = re.compile(
    r"<(?:script|style|noscript)[^>]*>.*?</(?:script|style|noscript)>",
    re.DOTALL | re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_SPACE_RE = re.compile(r"\s{2,}")


def _visible_text(html: str, limit: int = 4000) -> str:
    """Strip HTML to visible text for keyword matching.

    Block pages often embed large base64 assets (fonts, images) inside
    <style> blocks that inflate raw HTML to 20KB–1MB while having only
    a few hundred characters of actual text.
    """
    # Remove script/style blocks first (they contain base64, CSS, JS)
    cleaned = _SCRIPT_STYLE_RE.sub(" ", html)
    # Remove remaining tags
    cleaned = _TAG_RE.sub(" ", cleaned)
    # Collapse whitespace
    cleaned = _MULTI_SPACE_RE.sub(" ", cleaned).lower().strip()
    return cleaned[:limit]


def looks_like_block_page(body: str) -> FailureType | None:
    """Detect block/captcha pages via structural + keyword heuristics.

    Returns the appropriate FailureType if the page looks like a
    block/captcha page, or None if it looks like a real page.
    """
    # Extract visible text — this collapses large inline assets
    text = _visible_text(body)
    text_length = len(text.strip())

    # Short visible text is a strong signal of a block page.
    # Real e-commerce product pages have thousands of chars of text.
    # Block pages rarely exceed ~500 chars of visible text.
    is_sparse = text_length < 2000

    # Keyword match — check visible text, not raw HTML
    keyword_hits = sum(1 for kw in _BLOCK_KEYWORDS if kw in text)
    if keyword_hits >= 1 and is_sparse:
        if any(w in text for w in _CAPTCHA_WORDS):
            return FailureType.CLOUDFLARE_CAPTCHA
        return FailureType.WAF_BLOCKED

    # Block ID / Ray ID pattern in visible text
    if _BLOCK_ID_RE.search(text) and is_sparse:
        return FailureType.WAF_BLOCKED

    # Very sparse page with IP address and no product signals
    if text_length < 500 and _IP_RE.search(text):
        if not any(sig in text for sig in _PRODUCT_SIGNALS):
            return FailureType.WAF_BLOCKED

    return None


def classify_http_failure(
    status_code: int | None,
    body: str,
    error: Exception | None = None,
) -> FailureType:
    """Determine why an HTTP request failed."""
    if error is not None:
        return FailureType.NAVIGATION_FAILED

    if status_code is None:
        return FailureType.NAVIGATION_FAILED

    body_lower = body[:4000].lower() if body else ""

    if status_code == 200:
        if len(body) < 1000:
            return FailureType.EMPTY_PAGE
        return FailureType.JS_SPA_NO_DATA

    if "just a moment" in body_lower or "challenge-platform" in body_lower:
        return FailureType.CLOUDFLARE_JS
    if "attention required" in body_lower or "cf-error" in body_lower:
        return FailureType.CLOUDFLARE_CAPTCHA

    # Language-agnostic block detection
    block_type = looks_like_block_page(body)
    if block_type:
        return block_type

    if status_code == 403:
        return FailureType.WAF_BLOCKED
    if status_code in (503, 429):
        return FailureType.HTTP_BLOCKED

    return FailureType.HTTP_BLOCKED


def classify_playwright_failure(
    title: str,
    body_length: int,
    error: Exception | None = None,
    body_text: str = "",
) -> FailureType:
    """Determine why a Playwright-rendered page yielded no products."""
    if error is not None:
        return FailureType.NAVIGATION_FAILED

    title_lower = title.lower()
    if "just a moment" in title_lower:
        return FailureType.CLOUDFLARE_JS
    if "attention required" in title_lower:
        return FailureType.CLOUDFLARE_CAPTCHA

    # Language-agnostic block detection
    if body_text:
        block_type = looks_like_block_page(body_text)
        if block_type:
            return block_type

    if body_length < 1000:
        return FailureType.EMPTY_PAGE

    return FailureType.NO_PRODUCTS_FOUND
