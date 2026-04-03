"""Unified product extraction pipeline.

All extraction methods (JSON-LD, microdata, OG meta, data-attributes,
CSS strategy, API interception) are collected here so the same set of
methods can run regardless of how the HTML was obtained (httpx, curl_cffi,
or Playwright).
"""

from __future__ import annotations

import hashlib
import json as _json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.shared.logging import get_logger
from src.shared.market_config import get_default_currency_for_domain, get_garbage_names
from src.shared.models import ProductResult, Seller

logger = get_logger(__name__)

_MAX_SANE_PRICE = 1_000_000

# ---------------------------------------------------------------------------
# Field-name candidate sets for JSON API extraction
# ---------------------------------------------------------------------------
_NAME_FIELDS = {"name", "title", "product_name", "productName", "item_name",
                "itemName", "ProductName", "Name", "Title", "HeadLine",
                "headline", "label", "Label", "displayName"}
_PRICE_FIELDS = {"price", "Price", "cost", "amount", "min_price", "minPrice",
                 "lowPrice", "finalPrice", "priceValue", "ProductPrice",
                 "MinPrice", "SalePrice", "salePrice", "OriginalPrice",
                 "originalPrice", "unitPrice", "PriceValue"}
_MODEL_FIELDS = {"sku", "model", "mpn", "model_id", "modelId", "product_id",
                 "productId", "SKU", "MPN", "Sku", "Model", "catalogNumber",
                 "CatalogNumber", "barcode", "Barcode", "sku_id", "skuId"}
_IMAGE_FIELDS = {"image", "img", "imageUrl", "image_url", "thumbnail",
                 "photo", "ImageUrl", "ProductImage", "mainImage",
                 "MainImage", "pictureUrl", "PictureUrl"}
_BRAND_FIELDS = {"brand", "manufacturer", "Brand", "BrandName", "brandName",
                 "Manufacturer", "vendorName", "VendorName"}
_CURRENCY_FIELDS = {"currency", "currencyCode", "currency_code",
                    "priceCurrency", "CurrencyCode"}
_SELLER_ARRAY_FIELDS = {"sellers", "stores", "shops", "offers", "prices",
                        "vendors", "Stores", "Sellers", "Offers", "Prices",
                        "StoreOffers", "storeOffers"}
_SELLER_NAME_FIELDS = {"name", "storeName", "store_name", "shopName",
                       "shop_name", "sellerName", "seller_name", "Name",
                       "StoreName", "ShopName"}
_SELLER_URL_FIELDS = {"url", "link", "storeUrl", "store_url", "shopUrl",
                      "Url", "Link", "StoreUrl"}

_MAX_PRODUCTS_PER_SITE = 50


# ===================================================================
# Public API — unified extraction from static HTML (BeautifulSoup)
# ===================================================================


def extract_all_from_soup(
    soup: BeautifulSoup,
    url: str,
    domain: str,
    product_query: str,
) -> list[tuple[str, list[ProductResult]]]:
    """Run all HTML-based extraction methods on *soup*.

    Returns a list of ``(method_name, products)`` pairs.  Only non-empty
    results are included.  The caller picks the first (highest-priority)
    non-empty result.
    """
    results: list[tuple[str, list[ProductResult]]] = []

    page_product_name = _extract_page_product_name(soup)

    # 1. Data-attribute extraction (price-comparison aggregators)
    products = _extract_from_data_attrs(soup, url, domain, page_product_name)
    if products:
        results.append(("data_attrs", products))

    # 2. JSON-LD (single Product)
    product = _extract_jsonld_from_soup(soup, url, domain)
    if product:
        results.append(("jsonld", [product]))

    # 3. JSON-LD ItemList (product listings / search results)
    itemlist_products = _extract_jsonld_itemlist_from_soup(soup, url, domain)
    if itemlist_products:
        results.append(("jsonld_itemlist", itemlist_products))

    # 4. Microdata (itemprop)
    product = _extract_microdata_from_soup(soup, url, domain)
    if product:
        results.append(("microdata", [product]))

    # 5. OG meta tags
    product = _extract_og_product_from_soup(soup, url, domain)
    if product:
        results.append(("og_meta", [product]))

    # 6. CSS listing cards (search/category pages with visible product cards)
    listing_products = _extract_listing_cards_from_soup(soup, url, domain)
    if listing_products:
        results.append(("css_listing", listing_products))

    return results


# ===================================================================
# Public API — unified extraction from Playwright page
# ===================================================================


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
        products = await extract_with_strategy(page, strategy, url, criteria=criteria)
        if products:
            # Quality check: reject when strategy latched onto nav/UI elements
            if not _is_low_quality_batch(products):
                results.append(("css_strategy", products))

    # 2. API response interception
    if captured_responses:
        api_products = extract_from_api_responses(
            captured_responses, url, domain, product_query,
        )
        if api_products:
            results.append(("api_intercept", api_products))

    # 3. In-page JSON-LD / microdata / OG via JS evaluation
    fallback = await _extract_jsonld_product_js(page, url, domain)
    if fallback:
        results.append(("jsonld_js", [fallback]))

    # 4. Structured data from rendered HTML (catches sites where JS builds
    #    microdata/OG tags dynamically — not present in static HTML).
    try:
        rendered_html = await page.content()
        if len(rendered_html) > 1000:
            soup = BeautifulSoup(rendered_html, "lxml")
            soup_results = extract_all_from_soup(soup, url, domain, product_query)
            for method, products in soup_results:
                # Avoid duplicating jsonld if we already got it from JS
                if method == "jsonld" and any(m == "jsonld_js" for m, _ in results):
                    continue
                results.append((f"{method}_rendered", products))
    except Exception:
        pass

    return results


# ===================================================================
# Page-level product name extraction
# ===================================================================


def _extract_page_product_name(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    if h1:
        text = h1.get_text(strip=True)
        if text and len(text) < 300:
            return text
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()
    title = soup.find("title")
    if title:
        return title.get_text(strip=True).split(" - ")[0].strip()
    return ""


# ===================================================================
# Data-attribute extraction (price-comparison pages)
# ===================================================================

def _extract_from_data_attrs(
    soup: BeautifulSoup,
    page_url: str,
    domain: str,
    page_product_name: str,
) -> list[ProductResult]:
    containers = soup.find_all(attrs={"data-product-price": True})
    if not containers:
        return []

    products: list[ProductResult] = []
    currency = get_default_currency_for_domain(domain)

    for el in containers[:_MAX_PRODUCTS_PER_SITE]:
        price_raw = el.get("data-product-price", "")
        price = _parse_price(price_raw)
        if price is None:
            continue

        name = ""
        for attr in ("data-site-name", "data-store-name", "data-seller-name",
                      "data-shop-name", "data-merchant-name"):
            name = (el.get(attr) or "").strip()
            if name:
                break
        if not name:
            name = domain

        link_el = el.find("a", href=True)
        seller_url = urljoin(page_url, link_el["href"]) if link_el else page_url

        products.append(ProductResult(
            name=page_product_name or name,
            model_id=None,
            sellers=[Seller(name=name, price=price, currency=currency, url=seller_url)],
        ))

    return products


# ===================================================================
# JSON-LD extraction from static HTML
# ===================================================================


def _parse_jsonld_product(
    item: dict, page_url: str, domain: str,
) -> ProductResult | None:
    """Parse a single JSON-LD Product item into a ProductResult."""
    if not isinstance(item, dict):
        return None
    if item.get("@type") != "Product":
        return None

    name = (item.get("name") or "").strip()
    if not name or len(name) < 3:
        return None

    offers = item.get("offers", {})
    offer = offers[0] if isinstance(offers, list) and offers else offers
    if not isinstance(offer, dict):
        offer = {}

    price_raw = offer.get("price") or offer.get("lowPrice")
    price = float(price_raw) if price_raw else None
    if price is not None and (price <= 0 or price > _MAX_SANE_PRICE):
        price = None

    currency = (offer.get("priceCurrency") or "").strip()
    if not currency:
        currency = get_default_currency_for_domain(domain)

    brand_obj = item.get("brand")
    brand = None
    if isinstance(brand_obj, dict):
        brand = brand_obj.get("name")
    elif isinstance(brand_obj, str):
        brand = brand_obj

    model_id_raw = item.get("mpn") or item.get("sku") or item.get("gtin13") or None
    model_id = str(model_id_raw) if model_id_raw is not None else None

    image = item.get("image")
    if isinstance(image, list):
        image = image[0] if image else None

    # Resolve product URL from offers if available
    product_url = str(page_url)
    offer_url = offer.get("url")
    if offer_url and isinstance(offer_url, str):
        product_url = urljoin(str(page_url), offer_url)

    return ProductResult(
        name=name,
        model_id=model_id,
        brand=brand,
        image_url=image if isinstance(image, str) else None,
        sellers=[Seller(name=domain, price=price, currency=currency, url=product_url)],
    )


def _extract_jsonld_from_soup(
    soup: BeautifulSoup, page_url: str, domain: str,
) -> ProductResult | None:
    """Extract a single Product from JSON-LD."""
    import json
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        items = [data] if isinstance(data, dict) else data if isinstance(data, list) else []
        if isinstance(data, dict) and "@graph" in data:
            items = data["@graph"]

        for item in items:
            product = _parse_jsonld_product(item, page_url, domain)
            if product:
                return product

    return None


def _extract_jsonld_itemlist_from_soup(
    soup: BeautifulSoup, page_url: str, domain: str,
) -> list[ProductResult]:
    """Extract products from JSON-LD ItemList (common on listing/search pages)."""
    import json
    products: list[ProductResult] = []

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        if not isinstance(data, dict) or data.get("@type") != "ItemList":
            continue

        list_elements = data.get("itemListElement", {})
        # Can be a dict (keyed by ID) or a list
        if isinstance(list_elements, dict):
            list_elements = list(list_elements.values())
        if not isinstance(list_elements, list):
            continue

        for el in list_elements:
            if not isinstance(el, dict):
                continue
            # ListItem wraps the actual Product in "item"
            item = el.get("item", el)
            product = _parse_jsonld_product(item, page_url, domain)
            if product:
                products.append(product)

    return products[:_MAX_PRODUCTS_PER_SITE]


# ===================================================================
# Microdata extraction (itemprop)
# ===================================================================


def _extract_microdata_from_soup(
    soup: BeautifulSoup, page_url: str, domain: str,
) -> ProductResult | None:
    price_el = soup.find(attrs={"itemprop": "price"})
    if not price_el:
        return None

    price_raw = price_el.get("content") or price_el.get_text(strip=True) or ""
    price = _parse_price(price_raw)
    if not price or price <= 0:
        return None

    currency_el = soup.find(attrs={"itemprop": "priceCurrency"})
    currency = ""
    if currency_el:
        currency = (currency_el.get("content") or currency_el.get_text(strip=True) or "").strip()
    if not currency:
        currency = get_default_currency_for_domain(domain)

    # Find product name
    name_el = soup.find(attrs={"itemprop": "name"})
    name = ""
    if name_el:
        name = (name_el.get("content") or name_el.get_text(strip=True) or "").strip()
    if not name:
        name = _extract_page_product_name(soup)
    if not name or len(name) < 3:
        return None

    # Brand
    brand = None
    brand_el = soup.find(attrs={"itemprop": "brand"})
    if brand_el:
        inner = brand_el.find(attrs={"itemprop": "name"})
        brand = ((inner or brand_el).get("content") or (inner or brand_el).get_text(strip=True) or "").strip() or None

    # SKU / MPN
    model_id = None
    for prop in ("sku", "mpn", "gtin13"):
        el = soup.find(attrs={"itemprop": prop})
        if el:
            val = (el.get("content") or el.get_text(strip=True) or "").strip()
            if val:
                model_id = val
                break
    if not model_id:
        model_id = _extract_model_from_text(name)

    return ProductResult(
        name=name,
        model_id=model_id,
        brand=brand,
        sellers=[Seller(name=domain, price=price, currency=currency, url=page_url)],
    )


# ===================================================================
# OG meta tag extraction
# ===================================================================


def _extract_og_product_from_soup(
    soup: BeautifulSoup, page_url: str, domain: str,
) -> ProductResult | None:
    og_type = soup.find("meta", property="og:type")
    if not og_type or (og_type.get("content") or "").lower() != "product":
        return None

    price_meta = soup.find("meta", property="product:price:amount")
    if not price_meta:
        return None
    price = _parse_price(price_meta.get("content", ""))
    if not price or price <= 0:
        return None

    currency_meta = soup.find("meta", property="product:price:currency")
    currency = (currency_meta.get("content", "") if currency_meta else "").strip()
    if not currency:
        currency = get_default_currency_for_domain(domain)

    og_title = soup.find("meta", property="og:title")
    name = (og_title.get("content", "") if og_title else "").strip()
    if not name or len(name) < 3:
        return None

    og_img = soup.find("meta", property="og:image")
    image_url = (og_img.get("content", "") if og_img else "") or None

    model_id = _extract_model_from_text(name)

    return ProductResult(
        name=name,
        model_id=model_id,
        image_url=image_url,
        sellers=[Seller(name=domain, price=price, currency=currency, url=page_url)],
    )


# ===================================================================
# CSS listing card extraction (search/category pages)
# ===================================================================


def _extract_listing_cards_from_soup(
    soup: BeautifulSoup, page_url: str, domain: str,
) -> list[ProductResult]:
    """Extract products from listing/search page HTML cards.

    Looks for repeated card-like elements that each contain a product name,
    price, and link.  Works on sites like skroutz.gr, bestprice.gr etc.
    """
    products: list[ProductResult] = []
    currency = get_default_currency_for_domain(domain)

    # Common card container selectors (order by specificity)
    card_selectors = [
        "li.card",
        "[class*='product-card']",
        "[class*='ProductCard']",
        "[class*='product-item']",
        "div[data-product-id]",
        "article[class*='product']",
    ]

    cards = []
    for sel in card_selectors:
        cards = soup.select(sel)
        if len(cards) >= 2:
            break

    if len(cards) < 2:
        return []

    for card in cards[:_MAX_PRODUCTS_PER_SITE]:
        # Extract product name from first link with text
        name = ""
        product_url = page_url
        link = card.select_one("a[href]")
        if link:
            name = link.get_text(strip=True)[:200]
            href = link.get("href", "")
            if href:
                product_url = urljoin(page_url, href)

        if not name or len(name) < 3:
            # Try any heading
            heading = card.select_one("h2, h3, h4, [class*='name'], [class*='title']")
            if heading:
                name = heading.get_text(strip=True)[:200]

        if not name or len(name) < 3:
            continue

        # Extract price
        price = None
        price_el = card.select_one("[class*='price'], [data-price]")
        if price_el:
            price_text = price_el.get("data-price") or price_el.get_text(strip=True)
            price = _parse_price(price_text or "")

        # Extract image
        img = card.select_one("img[src], img[data-src]")
        image_url = None
        if img:
            image_url = img.get("src") or img.get("data-src")
            if image_url:
                image_url = urljoin(page_url, image_url)

        products.append(ProductResult(
            name=name,
            model_id=_extract_model_from_text(name),
            image_url=image_url,
            sellers=[Seller(name=domain, price=price, currency=currency, url=product_url)],
        ))

    return products


# ===================================================================
# In-page JSON-LD / microdata / DOM price extraction (Playwright JS)
# ===================================================================


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


# ===================================================================
# CSS strategy extraction (delegates to strategy module)
# ===================================================================


async def extract_with_strategy(
    page: object,
    strategy: object,
    base_url: str,
    *,
    criteria: dict[str, dict] | None = None,
) -> list[ProductResult]:
    """Extract products from page using a scraping strategy."""
    from src.mcp_servers.web_scraper_mcp.spec_patterns import build_extraction_patterns

    domain = _extract_domain(base_url)

    try:
        containers = await page.query_selector_all(strategy.product_container)
    except Exception:
        logger.warning("Failed to find containers with '%s'", strategy.product_container)
        return []

    extraction_patterns = build_extraction_patterns(criteria)

    products: list[ProductResult] = []
    for container in containers[:_MAX_PRODUCTS_PER_SITE]:
        product = await _extract_single_product(
            container, strategy, base_url, domain,
            extraction_patterns=extraction_patterns,
        )
        if product:
            products.append(product)

    logger.info("Extracted %d products from %s", len(products), domain)
    return products


async def _extract_single_product(
    container: object,
    strategy: object,
    base_url: str,
    domain: str,
    *,
    extraction_patterns: list | None = None,
) -> ProductResult | None:
    """Extract a single product from a container element."""
    name = ""
    if strategy.name_attr:
        try:
            name = (await container.get_attribute(strategy.name_attr) or "").strip()
        except Exception:
            pass
    if not name and strategy.name_selector:
        try:
            name_el = await container.query_selector(strategy.name_selector)
            if name_el:
                name = (await name_el.inner_text()).strip()
        except Exception:
            pass

    if not name:
        return None
    if len(name) < 8 or name in get_garbage_names():
        return None

    price: float | None = None
    currency = strategy.currency_hint or "USD"
    if strategy.price_attr:
        try:
            price_raw = (await container.get_attribute(strategy.price_attr) or "").strip()
            if price_raw:
                price = _parse_price(price_raw)
                if price is not None:
                    currency = get_default_currency_for_domain(domain)
        except Exception:
            pass
    if price is None and strategy.price_selector:
        try:
            price_el = await container.query_selector(strategy.price_selector)
            if price_el:
                price_text = (await price_el.inner_text()).strip()
                price = _parse_price(price_text)
                detected = _detect_currency_from_text(price_text)
                if detected:
                    currency = detected
        except Exception:
            pass

    if price is None:
        try:
            container_text = (await container.inner_text()).strip()
            price, currency = _extract_price_from_text(container_text, currency)
        except Exception:
            pass

    product_url: str | None = None
    if strategy.url_selector:
        try:
            url_el = await container.query_selector(strategy.url_selector)
            if url_el:
                href = await url_el.get_attribute("href")
                if href:
                    product_url = urljoin(base_url, href)
        except Exception:
            pass

    image_url: str | None = None
    if strategy.image_selector:
        try:
            img_el = await container.query_selector(strategy.image_selector)
            if img_el:
                src = await img_el.get_attribute("src") or await img_el.get_attribute("data-src")
                if src:
                    image_url = urljoin(base_url, src)
        except Exception:
            pass

    brand: str | None = None
    if strategy.brand_selector:
        try:
            brand_el = await container.query_selector(strategy.brand_selector)
            if brand_el:
                brand = (await brand_el.inner_text()).strip()
        except Exception:
            pass

    model_id: str | None = None
    if strategy.mpn_selector:
        try:
            mpn_el = await container.query_selector(strategy.mpn_selector)
            if mpn_el:
                model_id = (await mpn_el.inner_text()).strip()
        except Exception:
            pass

    if not model_id:
        key = f"{brand or ''}{name}".lower().strip()
        if key:
            model_id = hashlib.md5(key.encode()).hexdigest()[:12]

    criteria_data: dict[str, str] = {}
    for key, selector in (strategy.criteria_selectors or {}).items():
        try:
            el = await container.query_selector(selector)
            if el:
                text = (await el.inner_text()).strip()
                if text and len(text) < 200:
                    criteria_data[key] = text
        except Exception:
            continue

    if extraction_patterns:
        try:
            full_text = (await container.inner_text()).strip()
        except Exception:
            full_text = ""
        if full_text:
            for pattern, key, group_idx in extraction_patterns:
                if key in criteria_data:
                    continue
                match = pattern.search(full_text)
                if match:
                    value = match.group(group_idx).strip()
                    if value:
                        criteria_data[key] = value

    seller = Seller(name=domain, price=price, currency=currency, url=product_url)
    return ProductResult(
        name=name, model_id=model_id, brand=brand,
        image_url=image_url, criteria=criteria_data, sellers=[seller],
    )


# ===================================================================
# API response extraction (intercepted XHR/fetch)
# ===================================================================


def extract_from_api_responses(
    responses: list[dict],
    page_url: str,
    domain: str,
    product_query: str = "",
) -> list[ProductResult]:
    """Extract products from intercepted JSON responses."""
    all_products: list[ProductResult] = []
    query_lower = product_query.lower() if product_query else ""

    for resp in responses:
        found = _find_products_in_json(resp["data"], domain, page_url, query_lower)
        all_products.extend(found)

    seen: set[str] = set()
    unique: list[ProductResult] = []
    for p in all_products:
        key = p.name.lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return unique[:_MAX_PRODUCTS_PER_SITE]


def _find_products_in_json(
    data: object, domain: str, page_url: str,
    query_lower: str = "", depth: int = 0,
) -> list[ProductResult]:
    if depth > 6:
        return []

    if isinstance(data, dict):
        has_name = any(k in data for k in _NAME_FIELDS)
        has_price = any(k in data for k in _PRICE_FIELDS)

        if has_name and has_price:
            product = _dict_to_product(data, domain, page_url)
            if product:
                return [product]

        has_sellers = any(k in data for k in _SELLER_ARRAY_FIELDS)
        if has_name and has_sellers:
            product = _dict_to_product_with_sellers(data, domain, page_url)
            if product:
                return [product]

        for key, value in data.items():
            if isinstance(value, list) and len(value) >= 1:
                products = []
                for item in value:
                    if isinstance(item, dict):
                        sub = _find_products_in_json(item, domain, page_url, query_lower, depth + 1)
                        products.extend(sub)
                if products:
                    return products

        for key, value in data.items():
            if isinstance(value, dict):
                products = _find_products_in_json(value, domain, page_url, query_lower, depth + 1)
                if products:
                    return products

    elif isinstance(data, list):
        products = []
        for item in data:
            sub = _find_products_in_json(item, domain, page_url, query_lower, depth + 1)
            products.extend(sub)
        return products

    return []


def _get_field(obj: dict, candidates: set[str]) -> object | None:
    for f in candidates:
        if f in obj:
            return obj[f]
    return None


def _extract_price_value(obj: dict) -> tuple[float | None, str]:
    currency = ""
    cur_val = _get_field(obj, _CURRENCY_FIELDS)
    if isinstance(cur_val, str):
        currency = cur_val

    raw = _get_field(obj, _PRICE_FIELDS)
    if raw is None:
        return None, currency

    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw), currency
    if isinstance(raw, str):
        price = _parse_price(raw)
        if price and price > 0:
            if not currency:
                currency = _detect_currency_from_text(raw)
            return price, currency
    if isinstance(raw, dict):
        for subfield in ("value", "amount", "price", "raw", "final", "net"):
            if subfield in raw:
                sv = raw[subfield]
                if isinstance(sv, (int, float)) and sv > 0:
                    price = float(sv)
                elif isinstance(sv, str):
                    price = _parse_price(sv)
                else:
                    continue
                if price and price > 0:
                    if not currency:
                        cur_sub = _get_field(raw, _CURRENCY_FIELDS)
                        if isinstance(cur_sub, str):
                            currency = cur_sub
                    return price, currency

    return None, currency


def _dict_to_product(
    obj: dict, domain: str, page_url: str,
) -> ProductResult | None:
    name_raw = _get_field(obj, _NAME_FIELDS)
    if not isinstance(name_raw, str):
        return None
    name = name_raw.strip()
    if not name or len(name) < 3:
        return None

    price, currency = _extract_price_value(obj)
    if not currency:
        currency = get_default_currency_for_domain(domain)

    model_raw = _get_field(obj, _MODEL_FIELDS)
    model_id = str(model_raw).strip() if model_raw else None

    brand_raw = _get_field(obj, _BRAND_FIELDS)
    brand = None
    if isinstance(brand_raw, str):
        brand = brand_raw.strip() or None
    elif isinstance(brand_raw, dict) and "name" in brand_raw:
        brand = brand_raw["name"]

    img_raw = _get_field(obj, _IMAGE_FIELDS)
    image_url = None
    if isinstance(img_raw, str) and ("http" in img_raw or img_raw.startswith("/")):
        image_url = img_raw
    elif isinstance(img_raw, list) and img_raw and isinstance(img_raw[0], str):
        image_url = img_raw[0]

    product_url = page_url
    url_raw = _get_field(obj, {"url", "productUrl", "product_url", "link", "Link", "Url"})
    if isinstance(url_raw, str) and ("http" in url_raw or url_raw.startswith("/")):
        product_url = url_raw if url_raw.startswith("http") else urljoin(page_url, url_raw)

    if not model_id:
        key = f"{brand or ''}{name}".lower().strip()
        model_id = hashlib.md5(key.encode()).hexdigest()[:12]

    return ProductResult(
        name=name, model_id=model_id, brand=brand, image_url=image_url,
        sellers=[Seller(name=domain, price=price, currency=currency, url=product_url)],
    )


def _dict_to_product_with_sellers(
    obj: dict, domain: str, page_url: str,
) -> ProductResult | None:
    name_raw = _get_field(obj, _NAME_FIELDS)
    if not isinstance(name_raw, str) or len(name_raw.strip()) < 3:
        return None
    name = name_raw.strip()

    seller_array = None
    for f in _SELLER_ARRAY_FIELDS:
        if f in obj and isinstance(obj[f], list):
            seller_array = obj[f]
            break
    if not seller_array:
        return None

    sellers: list[Seller] = []
    for seller_obj in seller_array:
        if not isinstance(seller_obj, dict):
            continue
        s_name_raw = _get_field(seller_obj, _SELLER_NAME_FIELDS)
        s_name = str(s_name_raw).strip() if s_name_raw else domain

        s_price, s_currency = _extract_price_value(seller_obj)
        if not s_currency:
            s_currency = get_default_currency_for_domain(domain)

        s_url_raw = _get_field(seller_obj, _SELLER_URL_FIELDS)
        s_url = page_url
        if isinstance(s_url_raw, str) and ("http" in s_url_raw or s_url_raw.startswith("/")):
            s_url = s_url_raw if s_url_raw.startswith("http") else urljoin(page_url, s_url_raw)

        if s_name or s_price is not None:
            sellers.append(Seller(name=s_name, price=s_price, currency=s_currency, url=s_url))

    if not sellers:
        return None

    model_raw = _get_field(obj, _MODEL_FIELDS)
    model_id = str(model_raw).strip() if model_raw else None
    brand_raw = _get_field(obj, _BRAND_FIELDS)
    brand = None
    if isinstance(brand_raw, str):
        brand = brand_raw.strip() or None
    elif isinstance(brand_raw, dict) and "name" in brand_raw:
        brand = brand_raw["name"]

    img_raw = _get_field(obj, _IMAGE_FIELDS)
    image_url = None
    if isinstance(img_raw, str) and ("http" in img_raw or img_raw.startswith("/")):
        image_url = img_raw

    if not model_id:
        key = f"{brand or ''}{name}".lower().strip()
        model_id = hashlib.md5(key.encode()).hexdigest()[:12]

    return ProductResult(
        name=name, model_id=model_id, brand=brand,
        image_url=image_url, sellers=sellers,
    )


# ===================================================================
# Quality validation
# ===================================================================


def validate_results(
    products: list[ProductResult],
    product_query: str,
    domain: str,
) -> list[ProductResult]:
    """Filter out low-quality extraction results.

    Returns the cleaned list, or empty if everything was rejected.
    Applies both per-product checks and batch-level quality checks.
    """
    garbage = get_garbage_names()
    valid: list[ProductResult] = []

    for p in products:
        # Name checks
        if not p.name or len(p.name) < 3:
            continue
        if p.name.strip() in garbage:
            continue
        # Reject very short generic names (likely nav items)
        if len(p.name) < 5 and not p.model_id:
            continue

        # Price sanity
        if p.sellers and all(
            s.price is not None and (s.price > _MAX_SANE_PRICE or s.price < 0)
            for s in p.sellers
        ):
            continue

        # Must have at least a name and either a price or a URL
        has_price = any(s.price is not None and s.price > 0 for s in p.sellers)
        has_url = any(s.url for s in p.sellers)
        if not has_price and not has_url:
            continue

        valid.append(p)

    # Batch-level quality checks
    if _is_low_quality_batch(valid):
        return []

    # Duplicate name check: if >50% share the same name, likely wrong selector
    if len(valid) > 3:
        names = [p.name for p in valid]
        most_common = max(set(names), key=names.count)
        if names.count(most_common) > len(names) * 0.5:
            # Only reject if names are short/generic (not a comparison page)
            if len(most_common) < 30 and not any(
                s.price is not None and s.price > 0
                for p in valid for s in p.sellers
            ):
                return []

    return valid


def _is_low_quality_batch(products: list[ProductResult]) -> bool:
    """Check if a batch of products looks like nav/UI elements."""
    if len(products) < 2:
        return False
    names = [p.name for p in products]
    most_common = max(set(names), key=names.count)
    if names.count(most_common) >= len(products) * 0.5:
        has_any_price = any(
            s.price is not None
            for p in products for s in p.sellers
        )
        if not has_any_price:
            return True
    return False


# ===================================================================
# Shared helpers
# ===================================================================


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
