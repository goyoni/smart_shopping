"""Failure taxonomy and structured results for scraping attempts."""

from __future__ import annotations

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

    if status_code == 403:
        return FailureType.WAF_BLOCKED
    if status_code in (503, 429):
        return FailureType.HTTP_BLOCKED

    return FailureType.HTTP_BLOCKED


def classify_playwright_failure(
    title: str,
    body_length: int,
    error: Exception | None = None,
) -> FailureType:
    """Determine why a Playwright-rendered page yielded no products."""
    if error is not None:
        return FailureType.NAVIGATION_FAILED

    title_lower = title.lower()
    if "just a moment" in title_lower:
        return FailureType.CLOUDFLARE_JS
    if "attention required" in title_lower:
        return FailureType.CLOUDFLARE_CAPTCHA
    if body_length < 1000:
        return FailureType.EMPTY_PAGE

    return FailureType.NO_PRODUCTS_FOUND
