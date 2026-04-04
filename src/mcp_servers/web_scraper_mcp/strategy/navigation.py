"""Listing -> product page navigation."""

from __future__ import annotations

from opentelemetry import trace as otel_trace
from playwright.async_api import Page

from src.shared.logging import get_logger

from .candidates import PRODUCT_LINK_CANDIDATES
from .llm_discovery import _build_dom_snapshot
from .model import _get_scraper_llm_model

logger = get_logger(__name__)


async def find_product_url(
    page: Page,
    product_query: str,
    base_url: str,
    cached_selector: str = "",
) -> str | None:
    """Find a product link on a listing/search page that matches the query.

    Tries cached selector first, then CSS candidates, then LLM fallback.
    Returns the URL of the best matching product, or None.
    """
    span = otel_trace.get_current_span()
    query_lower = product_query.lower()
    query_tokens = [t for t in query_lower.split() if len(t) >= 3]

    # Try cached selector first
    if cached_selector:
        url = await _match_product_link(page, cached_selector, query_lower, query_tokens, base_url)
        if url:
            logger.info("[nav] found product via cached selector '%s': %s", cached_selector, url[:100])
            if span and span.is_recording():
                span.add_event("nav.found", {"method": "cached_selector", "selector": cached_selector, "url": url[:200]})
            return url

    # Try CSS candidates
    for selector in PRODUCT_LINK_CANDIDATES:
        url = await _match_product_link(page, selector, query_lower, query_tokens, base_url)
        if url:
            logger.info("[nav] found product via css candidate '%s': %s", selector, url[:100])
            if span and span.is_recording():
                span.add_event("nav.found", {"method": "css_candidate", "selector": selector, "url": url[:200]})
            return url

    # LLM fallback
    url, selector = await _find_product_url_via_llm(page, product_query, base_url)
    if url:
        logger.info("[nav] found product via LLM (selector='%s'): %s", selector, url[:100])
        if span and span.is_recording():
            span.add_event("nav.found", {"method": "llm", "selector": selector, "url": url[:200]})
        return url

    logger.warning("[nav] could not find product link for '%s' on %s", product_query, base_url[:80])
    if span and span.is_recording():
        span.add_event("nav.not_found", {"query": product_query, "base_url": base_url[:200]})
    return None


async def _match_product_link(
    page: Page,
    selector: str,
    query_lower: str,
    query_tokens: list[str],
    base_url: str,
) -> str | None:
    """Try to find a link matching the query using the given selector."""
    from urllib.parse import urljoin

    try:
        links = await page.query_selector_all(selector)
    except Exception:
        return None

    if not links:
        return None

    best_url: str | None = None
    best_score = 0

    for link in links[:30]:
        try:
            href = await link.get_attribute("href")
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue

            text = (await link.inner_text()).strip().lower()
            if not text:
                try:
                    parent = await link.evaluate_handle("el => el.closest('[class*=\"product\"], [class*=\"Product\"], li, article')")
                    if parent:
                        text = (await parent.inner_text() if hasattr(parent, 'inner_text') else "").strip().lower()
                except Exception:
                    pass
            if not text:
                continue

            score = 0
            if query_lower in text:
                score += 10
            if query_lower in href.lower():
                score += 5
            for tok in query_tokens:
                if tok in text:
                    score += 2
                if tok in href.lower():
                    score += 1

            if score > best_score:
                best_score = score
                full_url = urljoin(base_url, href)
                best_url = full_url
        except Exception:
            continue

    if best_score >= 2:
        return best_url
    return None


_LLM_NAV_SYSTEM = """\
You are a web scraping expert. Given a DOM snapshot of a search/listing page, \
identify the CSS selector for product links that lead to individual product pages.

Return ONLY a valid JSON object (no markdown, no explanation):
{"product_link_selector": "CSS selector for product links", "best_match_text": "visible text of the link matching the query"}

Rules:
- The selector must target <a> elements with href attributes.
- Prefer selectors that target product title/name links.
- If you cannot identify product links, return {"product_link_selector": "", "best_match_text": ""}"""


async def _find_product_url_via_llm(
    page: Page,
    product_query: str,
    base_url: str,
) -> tuple[str | None, str]:
    """Use LLM to find a product link on a listing page."""
    import json
    import re
    from urllib.parse import urljoin

    from src.shared.config import settings
    from src.shared.logging import get_tracer, operation_span, set_span_token_counts
    import litellm

    _tracer = get_tracer(__name__)

    model = _get_scraper_llm_model()
    api_key = settings.llm_api_key
    is_local = model.startswith("ollama/")
    if not api_key and not is_local:
        return None, ""

    snapshot = await _build_dom_snapshot(page)
    if not snapshot:
        return None, ""

    logger.info("[nav] LLM navigation discovery for '%s' (model=%s)", product_query, model)

    with operation_span(
        _tracer, "nav_llm_discovery",
        input=product_query,
    ) as span:
        span.set_attribute("llm.model", model)
        span.set_attribute("base_url", base_url[:200])

        user_prompt = (
            f"Search query: {product_query}\n"
            f"Page URL: {base_url}\n\n"
            f"DOM snapshot:\n{snapshot}"
        )
        span.set_attribute("llm.system_prompt", _LLM_NAV_SYSTEM)
        span.set_attribute("llm.user_prompt", user_prompt[:2000])

        try:
            llm_kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": _LLM_NAV_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.0,
            }
            if api_key:
                llm_kwargs["api_key"] = api_key
            response = await litellm.acompletion(**llm_kwargs)
            raw = (response.choices[0].message.content or "").strip()
            usage = response.get("usage") or {}
            set_span_token_counts(
                span,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            )
        except Exception as exc:
            logger.warning("[nav] LLM navigation call failed", exc_info=True)
            span.set_attribute("summary", f"LLM call failed: {type(exc).__name__}")
            return None, ""

        span.set_attribute("llm.raw_response", raw[:500])

        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[nav] LLM returned invalid JSON: %s", raw[:200])
            span.set_attribute("summary", f"LLM returned invalid JSON: {raw[:200]}")
            return None, ""

        selector = (data.get("product_link_selector") or "").strip()
        if not selector:
            span.set_attribute("summary", "LLM returned empty selector")
            return None, ""

        span.set_attribute("discovered_selector", selector)

        query_lower = product_query.lower()
        query_tokens = [t for t in query_lower.split() if len(t) >= 3]
        url = await _match_product_link(page, selector, query_lower, query_tokens, base_url)
        if url:
            span.set_attribute("summary", f"Found product URL via LLM selector '{selector}': {url[:150]}")
        else:
            span.set_attribute("summary", f"LLM selector '{selector}' found no matching product link")
        return url, selector
