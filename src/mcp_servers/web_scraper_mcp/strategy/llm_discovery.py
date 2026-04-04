"""LLM-based strategy discovery — last-resort fallback."""

from __future__ import annotations

import json
import re

import litellm
from playwright.async_api import Page

from src.shared.config import settings
from src.shared.logging import get_logger, get_tracer, operation_span, set_span_token_counts

from .helpers import _detect_currency, _discover_criteria_selectors
from .model import ScrapingStrategy, _get_scraper_llm_model

logger = get_logger(__name__)
_tracer = get_tracer(__name__)


_DOM_SNAPSHOT_JS = """() => {
    const EXCLUDED_TAGS = new Set([
        'script', 'style', 'noscript', 'svg', 'path', 'meta', 'link',
        'br', 'hr', 'iframe', 'video', 'audio', 'canvas', 'map',
    ]);
    const EXCLUDED_REGIONS = new Set(['nav', 'header', 'footer']);

    function isVisible(el) {
        if (!el.offsetParent && el.tagName !== 'BODY' && el.tagName !== 'HTML')
            return false;
        const s = getComputedStyle(el);
        return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
    }

    function inExcludedRegion(el) {
        let node = el;
        while (node) {
            const tag = (node.tagName || '').toLowerCase();
            const cls = (node.className || '').toString().toLowerCase();
            if (EXCLUDED_REGIONS.has(tag)) return true;
            if (cls.includes('nav') || cls.includes('menu') ||
                cls.includes('sidebar') || cls.includes('footer') ||
                cls.includes('header') || cls.includes('cookie') ||
                cls.includes('banner') || cls.includes('modal'))
                return true;
            node = node.parentElement;
        }
        return false;
    }

    function describeEl(el, depth) {
        const tag = el.tagName.toLowerCase();
        if (EXCLUDED_TAGS.has(tag)) return null;
        if (depth > 6) return null;
        if (!isVisible(el)) return null;

        const cls = el.className
            ? (typeof el.className === 'string' ? el.className : '').split(/\\s+/).slice(0, 3).join(' ')
            : '';
        const dataAttrs = {};
        for (const attr of el.attributes) {
            if (attr.name.startsWith('data-') && attr.value.length < 60)
                dataAttrs[attr.name] = attr.value.substring(0, 40);
        }
        const text = el.childNodes.length === 1 && el.childNodes[0].nodeType === 3
            ? el.childNodes[0].textContent.trim().substring(0, 40)
            : '';

        const children = [];
        const childGroups = {};
        for (const child of el.children) {
            const childTag = child.tagName.toLowerCase();
            if (EXCLUDED_TAGS.has(childTag)) continue;
            const childCls = child.className
                ? (typeof child.className === 'string' ? child.className : '').split(/\\s+/).slice(0, 2).join(' ')
                : '';
            const key = `${childTag}.${childCls}`;
            if (!childGroups[key]) childGroups[key] = [];
            childGroups[key].push(child);
        }

        for (const [key, group] of Object.entries(childGroups)) {
            if (group.length > 3) {
                for (const child of group.slice(0, 2)) {
                    const desc = describeEl(child, depth + 1);
                    if (desc) children.push(desc);
                }
                children.push(`... ×${group.length - 2} more <${key}>`);
            } else {
                for (const child of group) {
                    const desc = describeEl(child, depth + 1);
                    if (desc) children.push(desc);
                }
            }
        }

        let repr = `<${tag}`;
        if (cls) repr += ` class="${cls}"`;
        for (const [k, v] of Object.entries(dataAttrs)) repr += ` ${k}="${v}"`;
        repr += '>';
        if (text) repr += text;

        if (children.length === 0 && !text) return null;

        const result = { repr, children: children.filter(Boolean) };
        return result;
    }

    const main = document.querySelector('main, [role="main"], #content, .content, #main')
        || document.body;
    if (inExcludedRegion(main)) return JSON.stringify({ error: 'main is in excluded region' });

    const snapshot = describeEl(main, 0);

    function flatten(node, indent) {
        if (typeof node === 'string') return ' '.repeat(indent) + node;
        if (!node) return '';
        let lines = [' '.repeat(indent) + node.repr];
        for (const child of (node.children || [])) {
            const line = flatten(child, indent + 1);
            if (line) lines.push(line);
        }
        return lines.join('\\n');
    }

    const text = flatten(snapshot, 0);
    return text.substring(0, 6000);
}"""


_LLM_SYSTEM_PROMPT = """\
You are a web scraping expert. Given a simplified DOM snapshot of a product listing or search results page, \
identify the CSS selectors needed to extract product data.

Rules:
- The "container" is the repeating element that wraps each individual product card/row.
- Prefer class-based selectors. Use the most specific class that uniquely identifies product items.
- For compound classes, use the CSS format: tag.class1.class2
- Selectors must be valid CSS. Do not use XPath.
- If you cannot identify a selector, use an empty string.

Return ONLY a valid JSON object (no markdown, no explanation):
{"container": "...", "name": "...", "price": "...", "image": "...", "url": "...", "currency": "..."}

Where:
- container: CSS selector for the repeating product wrapper element
- name: CSS selector (relative to container) for the product name/title
- price: CSS selector (relative to container) for the price
- image: CSS selector (relative to container) for the product image
- url: CSS selector (relative to container) for the link to the product page
- currency: the currency code detected (e.g. "EUR", "USD", "ILS") or empty string"""


async def _build_dom_snapshot(page: Page) -> str | None:
    """Build a compact DOM snapshot of the main content area."""
    try:
        snapshot = await page.evaluate(_DOM_SNAPSHOT_JS)
        if snapshot and len(snapshot) > 100:
            return snapshot
    except Exception:
        logger.debug("DOM snapshot extraction failed")
    return None


async def _discover_via_llm(
    page: Page,
    product_query: str = "",
    criteria: dict[str, dict] | None = None,
) -> ScrapingStrategy | None:
    """Use an LLM to analyse the rendered DOM and infer CSS selectors."""
    model = _get_scraper_llm_model()
    api_key = settings.llm_api_key
    is_local = model.startswith("ollama/")
    if not api_key and not is_local:
        logger.debug("LLM strategy discovery skipped -- no API key configured")
        return None

    snapshot = await _build_dom_snapshot(page)
    if not snapshot:
        return None

    logger.info("Attempting LLM strategy discovery (model=%s, snapshot=%d chars)", model, len(snapshot))

    with operation_span(
        _tracer, "strategy_llm_discovery",
        input=product_query,
    ) as span:
        span.set_attribute("llm.model", model)
        span.set_attribute("snapshot_length", len(snapshot))

        user_prompt = f"Product query: {product_query}\n\nDOM snapshot:\n{snapshot}"
        span.set_attribute("llm.system_prompt", _LLM_SYSTEM_PROMPT)
        span.set_attribute("llm.user_prompt", user_prompt[:2000])

        try:
            llm_kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": _LLM_SYSTEM_PROMPT},
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
            logger.warning("LLM strategy discovery call failed", exc_info=True)
            span.set_attribute("summary", f"LLM call failed: {type(exc).__name__}: {str(exc)[:200]}")
            return None

        span.set_attribute("llm.raw_response", raw[:1000])

        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            selectors = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("LLM returned invalid JSON: %s", raw[:200])
            span.set_attribute("summary", f"LLM returned invalid JSON: {raw[:200]}")
            return None

        container_sel = selectors.get("container", "")
        name_sel = selectors.get("name", "")
        price_sel = selectors.get("price", "")
        image_sel = selectors.get("image", "")
        url_sel = selectors.get("url", "")
        currency_hint = selectors.get("currency", "")

        span.set_attribute("llm.parsed_selectors", json.dumps(selectors))

        if not container_sel:
            logger.warning("LLM returned empty container selector")
            span.set_attribute("summary", "LLM returned empty container selector")
            return None

        # --- Validate against the live page ---
        try:
            containers = await page.query_selector_all(container_sel)
        except Exception:
            logger.warning("LLM container selector '%s' is invalid CSS", container_sel)
            span.set_attribute("summary", f"LLM container '{container_sel}' is invalid CSS")
            return None

        span.set_attribute("container_match_count", len(containers))

        if len(containers) < 2:
            logger.warning(
                "LLM container selector '%s' matched %d elements (need >=2)",
                container_sel, len(containers),
            )
            span.set_attribute("summary", f"LLM container '{container_sel}' matched only {len(containers)} elements (need >=2)")
            return None

        # Verify at least one container has a name element
        has_name = False
        for probe in containers[:3]:
            try:
                el = await probe.query_selector(name_sel) if name_sel else None
                if el:
                    text = (await el.inner_text()).strip()
                    if text and 2 <= len(text) <= 300:
                        has_name = True
                        break
            except Exception:
                continue

        if not has_name:
            for fallback in ["a", "h2", "h3", "[class*='name']", "[class*='title']"]:
                for probe in containers[:3]:
                    try:
                        el = await probe.query_selector(fallback)
                        if el:
                            text = (await el.inner_text()).strip()
                            if text and 2 <= len(text) <= 300:
                                name_sel = fallback
                                has_name = True
                                break
                    except Exception:
                        continue
                if has_name:
                    break

        if not has_name:
            logger.warning("LLM strategy: no valid name element found in containers")
            span.set_attribute("summary", f"LLM container '{container_sel}' has {len(containers)} elements but no valid name text found")
            return None

        if not currency_hint and price_sel:
            for probe in containers[:3]:
                try:
                    price_el = await probe.query_selector(price_sel)
                    if price_el:
                        price_text = await price_el.inner_text()
                        currency_hint = _detect_currency(price_text)
                        if currency_hint:
                            break
                except Exception:
                    continue

        criteria_sels = await _discover_criteria_selectors(containers[0], criteria)

        logger.info(
            "LLM discovered strategy: container='%s' name='%s' price='%s' (%d containers)",
            container_sel, name_sel, price_sel, len(containers),
        )
        span.set_attribute("summary",
            f"LLM SUCCESS: container='{container_sel}' ({len(containers)} elements), name='{name_sel}', price='{price_sel}', currency={currency_hint}")

        return ScrapingStrategy(
            product_container=container_sel,
            name_selector=name_sel,
            price_selector=price_sel,
            image_selector=image_sel,
            url_selector=url_sel,
            currency_hint=currency_hint,
            discovery_method="llm",
            criteria_selectors=criteria_sels,
        )
