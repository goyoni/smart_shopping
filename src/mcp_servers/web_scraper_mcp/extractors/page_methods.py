"""Playwright page-based extraction methods."""

from __future__ import annotations

import hashlib

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.shared.logging import get_logger
from src.shared.market_config import get_default_currency_for_domain
from src.shared.models import ProductResult, Seller

from .api_extract import extract_from_api_responses
from .css_strategy_extract import extract_with_strategy
from .helpers import (
    _MAX_SANE_PRICE,
    _extract_domain,
    _extract_model_from_text,
    _extraction_event,
)
from .llm_extract import _extract_product_via_llm
from .soup_methods import extract_all_from_soup
from .validation import _is_low_quality_batch

logger = get_logger(__name__)


async def extract_all_from_page(
    page: Page,
    url: str,
    domain: str,
    product_query: str,
    captured_responses: list[dict],
    criteria: dict[str, dict] | None = None,
) -> list[tuple[str, list[ProductResult]]]:
    """Run all extraction methods on a Playwright-rendered page.

    Returns a list of ``(method_name, products)`` pairs.
    """
    from src.mcp_servers.web_scraper_mcp.strategy import ScrapingStrategy, discover_strategy

    results: list[tuple[str, list[ProductResult]]] = []

    # 1. CSS strategy discovery + extraction
    strategy = await discover_strategy(page, product_query, criteria=criteria)
    if strategy:
        _extraction_event("css_strategy", f"Strategy found: container='{strategy.product_container}' method={strategy.discovery_method}")
        products = await extract_with_strategy(page, strategy, url, criteria=criteria)
        if products:
            if not _is_low_quality_batch(products):
                _extraction_event("css_strategy", f"{len(products)} products extracted", product_count=len(products))
                results.append(("css_strategy", products))
            else:
                _extraction_event("css_strategy", f"Rejected {len(products)} products as low-quality (nav/UI elements)", product_count=len(products))
        else:
            _extraction_event("css_strategy", "Strategy matched but extracted 0 products")
    else:
        _extraction_event("css_strategy", "No strategy discovered (all methods failed)")

    # 2. API response interception
    _extraction_event("api_intercept", f"{len(captured_responses)} API responses captured", response_count=len(captured_responses))
    if captured_responses:
        api_products = extract_from_api_responses(
            captured_responses, url, domain, product_query,
        )
        _extraction_event("api_intercept", f"{len(api_products)} products from API responses", product_count=len(api_products))
        if api_products:
            results.append(("api_intercept", api_products))

    # 3. In-page JSON-LD / microdata / OG via JS evaluation
    fallback = await _extract_jsonld_product_js(page, url, domain)
    _extraction_event("jsonld_js", f"found: {fallback.name[:60]}" if fallback else "not found", product_count=1 if fallback else 0)
    if fallback:
        results.append(("jsonld_js", [fallback]))

    # 4. Structured data from rendered HTML
    try:
        rendered_html = await page.content()
        if len(rendered_html) > 1000:
            _extraction_event("rendered_html", f"Parsing {len(rendered_html)} chars of rendered HTML")
            soup = BeautifulSoup(rendered_html, "lxml")
            soup_results = extract_all_from_soup(soup, url, domain, product_query)
            for method, products in soup_results:
                if method == "jsonld" and any(m == "jsonld_js" for m, _ in results):
                    continue
                results.append((f"{method}_rendered", products))
    except Exception:
        pass

    # 5. LLM direct extraction -- last resort when no method found a price
    _has_priced = any(
        any(s.price is not None and s.price > 0 for s in p.sellers)
        for _, prods in results for p in prods
    )
    if not _has_priced and product_query:
        _extraction_event("llm_extract", "No priced products found, trying LLM extraction")
        llm_product = await _extract_product_via_llm(page, product_query, url, domain)
        if llm_product:
            price_info = f"price={llm_product.sellers[0].price}" if llm_product.sellers else "no price"
            _extraction_event("llm_extract", f"LLM extracted: '{llm_product.name[:60]}' ({price_info})", product_count=1)
            results.append(("llm_extract", [llm_product]))
        else:
            _extraction_event("llm_extract", "LLM extraction also returned nothing")

    # Summary
    methods_with_results = [(m, len(p)) for m, p in results]
    _extraction_event("page_summary",
        f"Playwright extraction on {domain}: {methods_with_results if methods_with_results else 'all methods returned 0'}",
        winning_methods=str(methods_with_results),
        has_priced=_has_priced or any(
            any(s.price is not None and s.price > 0 for s in p.sellers)
            for _, prods in results for p in prods
        ),
    )

    return results


async def _extract_jsonld_product_js(
    page: Page,
    url: str,
    domain: str,
) -> ProductResult | None:
    """Extract product data from JSON-LD, microdata, or OG meta via JS evaluate."""
    try:
        data = await page.evaluate("""() => {
            // Try JSON-LD first
            const scripts = document.querySelectorAll('script[type="application/ld+json"]');
            for (const s of scripts) {
                try {
                    let obj = JSON.parse(s.textContent);
                    if (obj['@graph']) {
                        for (const item of obj['@graph']) {
                            if (item['@type'] === 'Product') { obj = item; break; }
                        }
                    }
                    if (obj['@type'] === 'Product') {
                        const offers = obj.offers || {};
                        const offer = Array.isArray(offers) ? offers[0] : offers;
                        return {
                            name: obj.name || '',
                            brand: (obj.brand && obj.brand.name) || obj.brand || '',
                            price: parseFloat(offer.price || offer.lowPrice || '0') || null,
                            currency: offer.priceCurrency || '',
                            image: obj.image || (Array.isArray(obj.image) ? obj.image[0] : ''),
                            mpn: obj.mpn || obj.sku || obj.gtin13 || '',
                        };
                    }
                } catch {}
            }
            // Fallback: microdata
            const priceEl = document.querySelector('[itemprop="price"]');
            if (priceEl) {
                const rawPrice = priceEl.getAttribute('content') || priceEl.textContent || '';
                const parsedPrice = parseFloat(rawPrice.replace(/[^0-9.]/g, ''));
                if (parsedPrice > 0 && parsedPrice < 1000000) {
                    const currEl = document.querySelector('[itemprop="priceCurrency"]');
                    const nameEl = document.querySelector('[itemprop="name"]');
                    const brandEl = document.querySelector('[itemprop="brand"]');
                    const skuEl = document.querySelector('[itemprop="sku"]');
                    const mpnEl = document.querySelector('[itemprop="mpn"]');
                    let brandName = '';
                    if (brandEl) {
                        const inner = brandEl.querySelector('[itemprop="name"]');
                        brandName = (inner || brandEl).getAttribute('content')
                                 || (inner || brandEl).textContent || '';
                    }
                    const getName = (sel) => {
                        const el = document.querySelector(sel);
                        return el ? el.getAttribute('content') || '' : '';
                    };
                    return {
                        name: (nameEl ? (nameEl.getAttribute('content') || nameEl.textContent) : '')
                              || getName('meta[property="og:title"]')
                              || (document.querySelector('h1') || {}).textContent || '',
                        brand: brandName.trim(),
                        price: parsedPrice,
                        currency: (currEl ? (currEl.getAttribute('content') || currEl.textContent) : '').trim(),
                        image: getName('meta[property="og:image"]'),
                        mpn: (skuEl ? (skuEl.getAttribute('content') || skuEl.textContent) : '').trim()
                           || (mpnEl ? (mpnEl.getAttribute('content') || mpnEl.textContent) : '').trim(),
                    };
                }
            }

            // Fallback: OG meta + DOM price
            const getName = (sel) => {
                const el = document.querySelector(sel);
                return el ? el.getAttribute('content') || '' : '';
            };
            const ogTitle = getName('meta[property="og:title"]');
            let domPrice = null;
            let domCurrency = '';
            const pricePattern = /[$\\u20aa\\u20ac\\u00a3]\\s*([\\d,]+(?:\\.?\\d{1,2})?)|(\\d[\\d,]*(?:\\.?\\d{1,2})?)\\s*[$\\u20aa\\u20ac\\u00a3]/;
            const priceSelectors = [
                '[class*="price"][class*="current"]',
                '[class*="price"][class*="final"]',
                '[class*="price"][class*="sale"]',
                '[data-price]', '[data-product-price]',
                '[class*="product-price"]', '[class*="ProductPrice"]',
                '[class*="price"]', '[class*="Price"]',
                'span[class*="amount"]',
            ];
            const excludePatterns = /old|was|original|before|regular|compare|discount|save|shipping|delivery|installment|payment|monthly/i;
            for (const sel of priceSelectors) {
                const els = document.querySelectorAll(sel);
                for (const el of els) {
                    const cls = (el.className || '') + ' ' + (el.id || '');
                    if (excludePatterns.test(cls)) continue;
                    const parentCls = (el.parentElement?.className || '') + ' ' + (el.parentElement?.id || '');
                    if (excludePatterns.test(parentCls)) continue;
                    const dataPrice = el.getAttribute('data-price') || el.getAttribute('data-product-price');
                    if (dataPrice) {
                        const dp = parseFloat(dataPrice);
                        if (dp > 0 && dp < 1000000) { domPrice = dp; break; }
                    }
                    const text = (el.textContent || '').trim();
                    const m = text.match(pricePattern);
                    if (m) {
                        const p = parseFloat((m[1] || m[2]).replace(/,/g, ''));
                        if (p > 0 && p < 1000000) {
                            domPrice = p;
                            if (text.includes('\\u20aa')) domCurrency = 'ILS';
                            else if (text.includes('$')) domCurrency = 'USD';
                            else if (text.includes('\\u20ac')) domCurrency = 'EUR';
                            break;
                        }
                    }
                }
                if (domPrice) break;
            }
            if (ogTitle) {
                return {
                    name: ogTitle, brand: '',
                    price: domPrice, currency: domCurrency,
                    image: getName('meta[property="og:image"]'), mpn: '',
                };
            }
            return null;
        }""")
    except Exception:
        return None

    if not data or not data.get("name"):
        return None

    name = data["name"].strip()
    if len(name) < 8:
        return None

    price = data.get("price")
    if price is not None and price > _MAX_SANE_PRICE:
        price = None
    currency = data.get("currency") or ""
    if not currency and price:
        currency = get_default_currency_for_domain(domain)

    model_id = data.get("mpn") or None
    if not model_id:
        model_id = _extract_model_from_text(name)
    if not model_id:
        key = f"{data.get('brand', '')}{name}".lower().strip()
        model_id = hashlib.md5(key.encode()).hexdigest()[:12]

    return ProductResult(
        name=name,
        model_id=model_id,
        brand=data.get("brand") or None,
        image_url=data.get("image") or None,
        sellers=[Seller(name=domain, price=price, currency=currency, url=url)],
    )
