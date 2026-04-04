"""LLM-based product extraction (last-resort for product pages)."""

from __future__ import annotations

import json
import re

from playwright.async_api import Page

from src.shared.logging import get_logger, get_tracer, operation_span, set_span_token_counts
from src.shared.market_config import get_default_currency_for_domain
from src.shared.models import ProductResult, Seller

from .helpers import _parse_price

logger = get_logger(__name__)
_tracer = get_tracer(__name__)


_LLM_PRODUCT_EXTRACT_PROMPT = """\
You are a product data extractor. Given a DOM snapshot of a product page, \
extract the main product's name and price.

Return ONLY a valid JSON object (no markdown, no explanation):
{"name": "full product name", "price": 123.45, "currency": "EUR", "model_id": "ABC-123"}

Rules:
- Extract the MAIN product on the page (not accessories or related products).
- Use the actual selling price (not crossed-out/original/was prices).
- price is a number (no currency symbol). Set to null if not visible.
- currency is a 3-letter code (EUR, USD, ILS, GBP, etc.).
- model_id is the SKU/model number if visible, otherwise empty string.
- Do not invent data — only extract what is visible."""


async def _extract_product_via_llm(
    page: Page,
    product_query: str,
    url: str,
    domain: str,
) -> ProductResult | None:
    """Use LLM to extract the main product from a product page."""
    import litellm

    from src.mcp_servers.web_scraper_mcp.strategy import _build_dom_snapshot
    from src.shared.config import settings

    model = settings.scraper_llm_model or settings.llm_model
    api_key = settings.llm_api_key
    is_local = model.startswith("ollama/")
    if not api_key and not is_local:
        return None

    snapshot = await _build_dom_snapshot(page)
    if not snapshot:
        return None

    logger.info("LLM product extraction for '%s' on %s (model=%s)", product_query, domain, model)

    with operation_span(
        _tracer, "llm_product_extract",
        input=product_query,
    ) as span:
        span.set_attribute("llm.model", model)
        span.set_attribute("domain", domain)
        span.set_attribute("snapshot_length", len(snapshot))

        user_prompt = (
            f"Product query: {product_query}\n"
            f"Page URL: {url}\n\n"
            f"DOM snapshot:\n{snapshot}"
        )
        span.set_attribute("llm.system_prompt", _LLM_PRODUCT_EXTRACT_PROMPT)
        span.set_attribute("llm.user_prompt", user_prompt[:2000])

        try:
            llm_kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": _LLM_PRODUCT_EXTRACT_PROMPT},
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
            logger.warning("LLM product extraction failed", exc_info=True)
            span.set_attribute("summary", f"LLM call failed: {type(exc).__name__}: {str(exc)[:200]}")
            return None

        span.set_attribute("llm.raw_response", raw[:1000])

        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("LLM extraction returned invalid JSON: %s", raw[:200])
            span.set_attribute("summary", "LLM returned invalid JSON")
            return None

        if not isinstance(item, dict):
            span.set_attribute("summary", "LLM returned non-dict JSON")
            return None

        name = (item.get("name") or "").strip()
        if not name or len(name) < 5:
            span.set_attribute("summary", f"LLM extracted name too short: '{name}'")
            return None

        price = item.get("price")
        if isinstance(price, str):
            price = _parse_price(price)
        if isinstance(price, (int, float)) and price <= 0:
            price = None

        default_currency = get_default_currency_for_domain(domain)
        currency = (item.get("currency") or default_currency or "").strip().upper()
        model_id = (item.get("model_id") or "").strip()

        logger.info("LLM extracted: name='%s' price=%s %s from %s", name[:60], price, currency, domain)
        span.set_attribute("summary",
            f"Extracted '{name[:60]}' price={price} {currency} model={model_id} from {domain}")

        return ProductResult(
            name=name,
            model_id=model_id,
            brand="",
            image_url="",
            sellers=[Seller(
                name=domain,
                price=price,
                currency=currency,
                url=url,
            )],
        )
