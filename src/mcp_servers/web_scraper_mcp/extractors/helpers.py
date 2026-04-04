"""Shared helpers for product extraction."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from opentelemetry import trace as otel_trace

_MAX_SANE_PRICE = 1_000_000
_MAX_PRODUCTS_PER_SITE = 50


def _extraction_event(method: str, detail: str, **attrs: object) -> None:
    """Record an extraction step as a span event on the active OTEL span."""
    span = otel_trace.get_current_span()
    if span and span.is_recording():
        span.add_event(f"extraction.{method}", {"detail": detail, **attrs})


def _parse_price(text: str) -> float | None:
    if not text:
        return None
    cleaned = re.sub(r"[^\d.,]", "", text.strip())
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rindex(",") > cleaned.rindex("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts[-1]) == 2:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value > _MAX_SANE_PRICE:
        return None
    return value


def _extract_model_from_text(text: str) -> str | None:
    match = re.search(r'\b([A-Z]{2,}(?=[A-Z\d-]*\d)[A-Z\d-]{3,}[A-Z\d])\b', text)
    return match.group(1) if match else None


def _detect_currency_from_text(text: str) -> str:
    if "₪" in text:
        return "ILS"
    if "€" in text:
        return "EUR"
    if "£" in text:
        return "GBP"
    if "$" in text:
        return "USD"
    return ""


def _extract_price_from_text(text: str, default_currency: str = "USD") -> tuple[float | None, str]:
    if not text:
        return None, default_currency
    patterns = [
        (r"₪\s*([\d,]+(?:\.\d{1,2})?)", "ILS"),
        (r"([\d,]+(?:\.\d{1,2})?)\s*₪", "ILS"),
        (r"\$\s*([\d,]+(?:\.\d{1,2})?)", "USD"),
        (r"([\d,]+(?:\.\d{1,2})?)\s*\$", "USD"),
        (r"€\s*([\d,]+(?:\.\d{1,2})?)", "EUR"),
        (r"([\d,]+(?:\.\d{1,2})?)\s*€", "EUR"),
        (r"£\s*([\d,]+(?:\.\d{1,2})?)", "GBP"),
        (r"(?:ILS|NIS)\s*([\d,]+(?:\.\d{1,2})?)", "ILS"),
    ]
    for pattern, cur in patterns:
        match = re.search(pattern, text)
        if match:
            price = _parse_price(match.group(1))
            if price is not None and price > 0:
                return price, cur
    return None, default_currency


def _extract_domain(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.hostname or ""
    if domain.startswith("www."):
        domain = domain[4:]
    return domain
