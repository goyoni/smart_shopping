"""LLM-based extraction from static HTML (BeautifulSoup).

Used as a last resort when all structured extraction methods (JSON-LD,
microdata, data-attrs, OG meta) return 0 products but the HTML body is
large enough to likely contain product data rendered in plain HTML/CSS.

Unlike ``llm_extract.py`` (Playwright-only, single product page), this
module works on raw HTML and supports search/listing pages with multiple
products.
"""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from src.shared.config import settings
from src.shared.logging import get_logger, get_tracer, operation_span, set_span_token_counts
from src.shared.market_config import get_default_currency_for_domain
from src.shared.models import ProductResult, Seller

from .helpers import _extraction_event, _parse_price

logger = get_logger(__name__)
_tracer = get_tracer(__name__)

_MAX_SNAPSHOT_CHARS = 6000

_LLM_LISTING_EXTRACT_PROMPT = """\
You are a product data extractor. Given a text snapshot of a search results \
or product listing page, extract ALL products with their names and prices.

Return ONLY a valid JSON array (no markdown, no explanation):
[{"name": "full product name", "price": 123.45, "currency": "ILS", "model_id": "ABC-123", "url": "/product/abc"}]

Rules:
- Extract ALL products visible on the page, not just the first one.
- price is a number (no currency symbol). Set to null if not visible.
- currency is a 3-letter code (EUR, USD, ILS, GBP, etc.).
- model_id is the SKU/model number if visible, otherwise empty string.
- url is the product link (relative or absolute) if visible, otherwise empty string.
- Do not invent data — only extract what is visible.
- Return an empty array [] if no products are found."""


def _build_soup_snapshot(soup: BeautifulSoup) -> str:
    """Build a compact text representation of the HTML for LLM analysis.

    Strips scripts, styles, navs, footers, and collapses whitespace.
    Keeps element structure hints (tag names, key attributes) to help
    the LLM understand the page layout.
    """
    # Work on a copy to avoid mutating the caller's soup
    soup = BeautifulSoup(str(soup), "lxml")

    # Remove non-content elements
    for tag in soup.find_all(["script", "style", "noscript", "svg", "iframe",
                               "nav", "footer", "header"]):
        tag.decompose()

    # Build a structured text snapshot
    lines: list[str] = []
    # Focus on the main content area if identifiable
    main = (
        soup.find("main")
        or soup.find(id=re.compile(r"content|results|products|search", re.I))
        or soup.find(class_=re.compile(r"content|results|products|search", re.I))
        or soup.body
        or soup
    )

    for el in main.descendants:
        if not hasattr(el, "name") or el.name is None:
            # Text node
            text = str(el).strip()
            if text and len(text) > 1:
                lines.append(text)
        elif el.name in ("a",):
            href = el.get("href", "")
            text = el.get_text(strip=True)
            if text and href:
                lines.append(f"[link:{href[:100]}] {text[:150]}")
            elif text:
                lines.append(text[:150])
        elif el.name in ("img",):
            alt = el.get("alt", "")
            if alt:
                lines.append(f"[img] {alt[:80]}")

    snapshot = "\n".join(lines)

    # Truncate to fit LLM context
    if len(snapshot) > _MAX_SNAPSHOT_CHARS:
        snapshot = snapshot[:_MAX_SNAPSHOT_CHARS] + "\n... (truncated)"

    return snapshot


async def extract_products_via_llm_soup(
    soup: BeautifulSoup,
    url: str,
    domain: str,
    product_query: str,
) -> list[ProductResult]:
    """Use LLM to extract products from static HTML when other methods fail.

    Returns a list of ProductResult, or empty list on failure.
    """
    import litellm

    model = settings.scraper_llm_model or settings.llm_model
    is_local = model.startswith("ollama/")
    if not settings.llm_api_key and not is_local:
        return []

    snapshot = _build_soup_snapshot(soup)
    if len(snapshot) < 100:
        _extraction_event("llm_soup", "snapshot too short, skipping")
        return []

    with operation_span(
        _tracer, "llm_soup_extract",
        input=product_query,
    ) as span:
        span.set_attribute("llm.model", model)
        span.set_attribute("domain", domain)
        span.set_attribute("snapshot_length", len(snapshot))

        user_prompt = (
            f"Product query: {product_query}\n"
            f"Page URL: {url}\n"
            f"Domain: {domain}\n\n"
            f"Page content:\n{snapshot}"
        )

        try:
            llm_kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": _LLM_LISTING_EXTRACT_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.0,
            }
            if settings.llm_api_key:
                llm_kwargs["api_key"] = settings.llm_api_key

            response = await litellm.acompletion(**llm_kwargs)
            raw = (response.choices[0].message.content or "").strip()
            usage = response.get("usage") or {}
            set_span_token_counts(
                span,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            )
        except Exception as exc:
            logger.warning("LLM soup extraction failed for %s: %s", domain, exc)
            span.set_attribute("summary", f"LLM call failed: {type(exc).__name__}")
            _extraction_event("llm_soup", f"LLM call failed: {type(exc).__name__}")
            return []

        span.set_attribute("llm.raw_response", raw[:1000])

        # Parse response
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            _extraction_event("llm_soup", "invalid JSON response")
            span.set_attribute("summary", "Invalid JSON from LLM")
            return []

        if not isinstance(items, list):
            items = [items] if isinstance(items, dict) else []

        default_currency = get_default_currency_for_domain(domain)
        products: list[ProductResult] = []

        for item in items:
            if not isinstance(item, dict):
                continue

            name = (item.get("name") or "").strip()
            if not name or len(name) < 5:
                continue

            price = item.get("price")
            if isinstance(price, str):
                price = _parse_price(price)
            if isinstance(price, (int, float)) and price <= 0:
                price = None

            currency = (item.get("currency") or default_currency or "").strip().upper()
            model_id = (item.get("model_id") or "").strip()
            product_url = (item.get("url") or "").strip()

            # Resolve relative URLs
            if product_url and not product_url.startswith("http"):
                from urllib.parse import urljoin
                product_url = urljoin(url, product_url)
            if not product_url:
                product_url = url

            products.append(ProductResult(
                name=name,
                model_id=model_id,
                sellers=[Seller(
                    name=domain,
                    price=price,
                    currency=currency,
                    url=product_url,
                )],
            ))

        _extraction_event("llm_soup",
            f"{len(products)} products extracted via LLM",
            product_count=len(products))
        span.set_attribute("summary",
            f"Extracted {len(products)} products from {domain} via LLM soup")

        return products
