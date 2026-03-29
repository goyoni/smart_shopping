"""Core scraping orchestration with adaptive strategy."""

from __future__ import annotations

import hashlib
import json as _json
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import Browser, Response

from src.mcp_servers.web_scraper_mcp.db_cache import (
    get_cached_strategy,
    save_strategy,
    update_success_rate,
)
from src.mcp_servers.web_scraper_mcp.strategy import ScrapingStrategy, discover_strategy
from src.shared.browser import get_page
from src.shared.logging import get_logger
from src.shared.market_config import get_default_currency_for_domain, get_garbage_names
from src.shared.models import ProductResult, Seller

logger = get_logger(__name__)

_MAX_PRODUCTS_PER_SITE = 50
_MAX_PAGES = 3

# CSS selectors tried in order to find a "next page" link
_NEXT_PAGE_CANDIDATES: list[str] = [
    "a[aria-label*='next' i]",
    "a[rel='next']",
    "a.next",
    "a.pagination-next",
    "li.next > a",
    "a[class*='next']",
    "a[class*='Next']",
    "button[aria-label*='next' i]",
    "nav[aria-label*='pagination'] a:last-child",
    "[class*='pagination'] a:last-child",
]

# ---------------------------------------------------------------------------
# JSON API response interception — field name candidates
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

# Tracking/analytics domains to ignore during response interception
_IGNORE_DOMAINS = {
    "google-analytics.com", "googletagmanager.com", "facebook.com",
    "doubleclick.net", "analytics.", "hotjar.com", "clarity.ms",
    "sentry.io", "newrelic.com", "segment.com", "mixpanel.com",
}


def extract_specs_from_text(
    text: str,
    criteria: dict[str, dict] | None = None,
) -> dict[str, str]:
    """Extract product specification values from free text using dynamic regex.

    Patterns are built from the provided *criteria* dict (keyed by criterion
    name, values contain at least a ``unit`` field).  When *criteria* is
    ``None`` a default set is used that covers common product specs.
    """
    if not text:
        return {}

    from src.mcp_servers.web_scraper_mcp.spec_patterns import build_extraction_patterns

    patterns = build_extraction_patterns(criteria)
    specs: dict[str, str] = {}
    for pattern, key, group_idx in patterns:
        if key in specs:
            continue
        match = pattern.search(text)
        if match:
            value = match.group(group_idx).strip()
            if value:
                specs[key] = value

    return specs


def extract_domain(url: str) -> str:
    """Extract domain from URL, stripping 'www.' prefix."""
    parsed = urlparse(url)
    domain = parsed.hostname or ""
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def parse_price(text: str) -> float | None:
    """Extract numeric price from text containing currency symbols."""
    if not text:
        return None
    # Remove currency symbols and whitespace
    cleaned = re.sub(r"[^\d.,]", "", text.strip())
    if not cleaned:
        return None
    # Handle comma as thousands separator (1,299.99) or decimal (1.299,99)
    if "," in cleaned and "." in cleaned:
        if cleaned.rindex(",") > cleaned.rindex("."):
            # European format: 1.299,99
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # US format: 1,299.99
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # Could be thousands (1,299) or decimal (12,99)
        parts = cleaned.split(",")
        if len(parts[-1]) == 2:
            # Likely decimal: 12,99
            cleaned = cleaned.replace(",", ".")
        else:
            # Likely thousands: 1,299
            cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _detect_page_type(url: str) -> str:
    """Classify a URL into a page type for strategy caching."""
    parsed = urlparse(url)
    path = parsed.path.lower()
    query = parsed.query.lower()

    # Product detail pages
    if any(x in path for x in ("/product/", "/item/", "/model", "/dp/", "/p/")):
        return "product"
    if any(x in query for x in ("productid=", "modelid=", "pid=", "itemid=")):
        return "product"

    # Search results pages
    if any(x in path for x in ("/search", "/find", "/results")):
        return "search"
    if any(x in query for x in ("q=", "query=", "search=", "keyword=")):
        return "search"

    # Category/catalog pages
    if any(x in path for x in ("/catalog", "/category", "/cat/")):
        return "catalog"

    return "page"


async def _find_next_page_url(page: object, base_url: str) -> str | None:
    """Detect a 'next page' link on the current page."""
    for selector in _NEXT_PAGE_CANDIDATES:
        try:
            el = await page.query_selector(selector)
            if el:
                href = await el.get_attribute("href")
                if href:
                    return urljoin(base_url, href)
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# HTTP pre-fetch — extract products from server-rendered HTML without a
# browser.  Many sites (especially price-comparison aggregators) return
# complete HTML via a simple GET.  This is faster and avoids anti-bot
# detection triggered by headless browsers.
# ---------------------------------------------------------------------------

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "he,en;q=0.9",
}

_HTTP_TIMEOUT = 15.0

_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "[::1]"}
_BLOCKED_PREFIXES = ("10.", "172.16.", "172.17.", "172.18.", "172.19.",
                     "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
                     "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
                     "172.30.", "172.31.", "192.168.", "169.254.", "fe80::",
                     "fc00::", "fd00::")


def _is_safe_url(url: str) -> bool:
    """Reject URLs targeting private/internal networks (SSRF protection)."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host or parsed.scheme not in ("http", "https"):
        return False
    if host in _BLOCKED_HOSTS:
        return False
    if any(host.startswith(p) for p in _BLOCKED_PREFIXES):
        return False
    return True


async def _try_http_prefetch(
    url: str, product_query: str, domain: str,
) -> list[ProductResult] | None:
    """Try to extract products from server-rendered HTML via HTTP GET.

    Returns a list of products if successful, or None to signal that
    the caller should fall back to Playwright-based scraping.
    """
    if not _is_safe_url(url):
        return None
    try:
        async with httpx.AsyncClient(
            headers=_HTTP_HEADERS,
            follow_redirects=True,
            timeout=_HTTP_TIMEOUT,
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
    except Exception:
        return None

    html = resp.text
    if len(html) < 1000:
        return None

    soup = BeautifulSoup(html, "lxml")

    # Extract product name from page (h1, title, og:title)
    page_product_name = _extract_page_product_name(soup)

    # Try data-attribute based extraction (price-comparison pages)
    products = _extract_from_data_attrs(soup, url, domain, page_product_name)
    if products:
        logger.info(
            "HTTP pre-fetch extracted %d products from %s (data-attrs)",
            len(products), domain,
        )
        return products

    # Try JSON-LD extraction from static HTML
    product = _extract_jsonld_from_soup(soup, url, domain)
    if product:
        logger.info("HTTP pre-fetch extracted product from %s (JSON-LD)", domain)
        return [product]

    return None


def _extract_page_product_name(soup: BeautifulSoup) -> str:
    """Extract the main product name from a page."""
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


def _extract_from_data_attrs(
    soup: BeautifulSoup,
    page_url: str,
    domain: str,
    page_product_name: str,
) -> list[ProductResult]:
    """Extract products from elements with data-product-price / data-site-name.

    This handles price-comparison layouts where each row represents a seller.
    """
    # Find elements with data-product-price attribute
    rows = soup.select("[data-product-price]")
    if len(rows) < 2:
        return []

    currency = get_default_currency_for_domain(domain)
    sellers: list[Seller] = []

    for row in rows:
        price_raw = row.get("data-product-price", "")
        price = parse_price(price_raw)
        if price is None or price <= 0:
            continue

        seller_name = row.get("data-site-name", domain)

        # Try to extract seller URL from links inside the row
        seller_url = page_url
        link = row.select_one("a[href]")
        if link and link.get("href"):
            href = link["href"]
            if href.startswith("/"):
                seller_url = urljoin(page_url, href)
            elif href.startswith("http"):
                seller_url = href

        sellers.append(Seller(
            name=seller_name,
            price=price,
            currency=currency,
            url=seller_url,
        ))

    if not sellers:
        return []

    # All sellers are for the same product on comparison pages
    product_name = page_product_name or domain
    model_id = None

    # Try to extract model from page URL or name
    if page_product_name:
        # Look for alphanumeric model ID patterns in the name
        model_match = re.search(r'\b([A-Z]{2,}[\d]{3,}[A-Z\d]*)\b', page_product_name)
        if model_match:
            model_id = model_match.group(1)

    if not model_id:
        key = page_product_name.lower().strip()
        model_id = hashlib.md5(key.encode()).hexdigest()[:12]

    # Extract image from OG meta or page content
    image_url = None
    og_img = soup.find("meta", property="og:image")
    if og_img and og_img.get("content"):
        image_url = og_img["content"]

    return [ProductResult(
        name=product_name,
        model_id=model_id,
        image_url=image_url,
        sellers=sellers,
    )]


def _extract_jsonld_from_soup(
    soup: BeautifulSoup,
    page_url: str,
    domain: str,
) -> ProductResult | None:
    """Extract a product from JSON-LD script tags in static HTML."""
    scripts = soup.select('script[type="application/ld+json"]')
    for script in scripts:
        try:
            data = _json.loads(script.string or "")
            if isinstance(data, dict) and data.get("@graph"):
                for item in data["@graph"]:
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        data = item
                        break
            if not isinstance(data, dict) or data.get("@type") != "Product":
                continue

            name = data.get("name", "").strip()
            if not name or len(name) < 3:
                continue

            offers = data.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}

            price_raw = offers.get("price") or offers.get("lowPrice")
            price = float(price_raw) if price_raw else None
            currency = offers.get("priceCurrency", "")
            if not currency:
                currency = get_default_currency_for_domain(domain)

            model_id = data.get("mpn") or data.get("sku") or None
            if not model_id:
                key = f"{data.get('brand', {}).get('name', '')}{name}".lower()
                model_id = hashlib.md5(key.encode()).hexdigest()[:12]

            brand_raw = data.get("brand")
            brand = None
            if isinstance(brand_raw, dict):
                brand = brand_raw.get("name")
            elif isinstance(brand_raw, str):
                brand = brand_raw

            image = data.get("image")
            if isinstance(image, list):
                image = image[0] if image else None

            seller = Seller(
                name=domain,
                price=price,
                currency=currency,
                url=page_url,
            )

            return ProductResult(
                name=name,
                model_id=model_id,
                brand=brand,
                image_url=image,
                sellers=[seller],
            )
        except Exception:
            continue

    return None


async def scrape_page(
    browser: Browser,
    url: str,
    product_query: str = "",
    *,
    locale: str = "en-US",
    criteria: dict[str, dict] | None = None,
) -> list[ProductResult]:
    """Scrape a product listing page using cached or newly discovered strategy.

    1. Navigate to page (intercepting API responses)
    2. Check for cached strategy
    3. If cached: use it; on failure re-discover
    4. If not cached: discover strategy
    5. Extract products
    6. Fallback: extract from intercepted API responses
    7. Fallback: extract from JSON-LD / OG meta
    8. Follow pagination links for additional pages
    """
    domain = extract_domain(url)
    page_type = _detect_page_type(url)

    # Try lightweight HTTP fetch before spinning up Playwright.
    # Works well for server-rendered sites (e.g. price-comparison pages).
    http_products = await _try_http_prefetch(url, product_query, domain)
    if http_products:
        logger.info("HTTP pre-fetch returned %d products for %s", len(http_products), url)
        return http_products

    async with get_page(browser, locale=locale) as page:
        # Set up API response interception BEFORE navigation
        captured_responses: list[dict] = []

        async def _on_response(response: Response) -> None:
            try:
                resp_url = response.url
                # Skip tracking/analytics
                if any(d in resp_url for d in _IGNORE_DOMAINS):
                    return
                ct = response.headers.get("content-type", "")
                if response.status != 200:
                    return
                # Accept JSON responses (explicit json content-type) and
                # also text/plain or text/html responses from API endpoints
                # (some sites serve JSON with non-json content types).
                is_json_ct = "json" in ct
                is_api_url = any(s in resp_url for s in ("/api/", "/ajax/", "/graphql", "format=json", ".json"))
                if not is_json_ct and not is_api_url:
                    return
                # Skip large responses (>500KB)
                cl = response.headers.get("content-length", "")
                if cl and int(cl) > 500_000:
                    return
                body = await response.body()
                if len(body) > 500_000:
                    return
                data = _json.loads(body)
                captured_responses.append({"url": resp_url, "data": data})
            except Exception:
                pass

        page.on("response", _on_response)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            logger.warning("Failed to navigate to %s", url)
            return []

        # Wait for content — first networkidle, then look for price indicators
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass  # Continue even if not fully idle

        # Wait for dynamic product content (JS-heavy sites like ksp, zap)
        try:
            await page.wait_for_selector(
                "[class*='price'], [class*='Price'], [data-price], "
                "[class*='product'], [class*='Product']",
                timeout=5000,
            )
        except Exception:
            pass  # May not have visible price elements yet

        # Try cached strategy first
        cached = await get_cached_strategy(domain, page_type)
        strategy: ScrapingStrategy | None = None
        products: list[ProductResult] = []
        if cached:
            logger.info("Using cached strategy for %s/%s", domain, page_type)
            if cached.discovery_method == "api_intercept":
                # For API strategies, extract from captured responses
                products = _extract_from_api_responses(
                    captured_responses, url, domain, product_query,
                )
            else:
                products = await _extract_with_strategy(page, cached, url, criteria=criteria)
            if products:
                await update_success_rate(domain, success=True, page_type=page_type)
                strategy = cached
            else:
                logger.info("Cached strategy failed for %s/%s, re-discovering", domain, page_type)
                await update_success_rate(domain, success=False, page_type=page_type)

        if strategy is None:
            # Discover new strategy
            strategy = await discover_strategy(page, product_query, criteria=criteria)
            if strategy:
                products = await _extract_with_strategy(page, strategy, url, criteria=criteria)

                # Quality check: if multiple products share the same name and
                # none have prices, the strategy latched onto nav/UI elements.
                if products and len(products) >= 2:
                    names = [p.name for p in products]
                    most_common = max(set(names), key=names.count)
                    if names.count(most_common) >= len(products) * 0.5:
                        has_any_price = any(
                            s.price is not None
                            for p in products for s in p.sellers
                        )
                        if not has_any_price:
                            logger.warning(
                                "Strategy for %s extracted %d products with duplicate name '%s' and no prices — discarding",
                                domain, len(products), most_common[:50],
                            )
                            products = []

                if products:
                    await save_strategy(domain, strategy, page_type)

            # Fallback 1: intercepted API responses
            if not products and captured_responses:
                api_products = _extract_from_api_responses(
                    captured_responses, url, domain, product_query,
                )
                if api_products:
                    logger.info(
                        "Extracted %d products from API responses for %s",
                        len(api_products), domain,
                    )
                    products = api_products
                    # Save an API strategy marker so next time we use interception directly
                    api_strategy = ScrapingStrategy(
                        product_container="",
                        discovery_method="api_intercept",
                    )
                    await save_strategy(domain, api_strategy, page_type)

            # Fallback 2: JSON-LD / OG meta tags
            if not products:
                fallback = await _extract_jsonld_product(page, url, domain)
                if fallback:
                    logger.info("Extracted product from JSON-LD/meta for %s", domain)
                    return [fallback]
                if not strategy:
                    logger.warning("No strategy discovered for %s", domain)
                else:
                    logger.warning("Strategy discovered but no products extracted from %s", url)
                return []

        # Comparison-page merge: if we extracted multiple "products" that
        # are actually sellers for the same product, merge them.  This
        # handles price-comparison layouts where each row is a seller.
        if products and product_query:
            products = _merge_comparison_sellers(products, product_query, domain)

        # If no products match the search query, try JSON-LD as a fallback
        # (CSS strategy may have grabbed sidebar/related items instead of the
        # main product on a detail page).
        if product_query and products:
            query_lower = product_query.lower()
            has_match = any(
                query_lower in p.name.lower() or
                (p.model_id and query_lower in p.model_id.lower())
                for p in products
            )
            if not has_match:
                fallback = await _extract_jsonld_product(page, url, domain)
                if fallback and (
                    query_lower in fallback.name.lower() or
                    (fallback.model_id and query_lower in fallback.model_id.lower()) or
                    query_lower in url.lower()
                ):
                    logger.info(
                        "CSS strategy returned unrelated products for %s, using JSON-LD instead",
                        domain,
                    )
                    products = [fallback]

        # Paginate: follow next-page links for additional results
        visited = {url}
        current_url = url
        for page_num in range(2, _MAX_PAGES + 1):
            if len(products) >= _MAX_PRODUCTS_PER_SITE:
                break

            next_url = await _find_next_page_url(page, current_url)
            if not next_url or next_url in visited:
                break
            visited.add(next_url)

            try:
                await page.goto(next_url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                logger.warning("Failed to navigate to page %d: %s", page_num, next_url)
                break

            page_products = await _extract_with_strategy(
                page, strategy, next_url, criteria=criteria,
            )
            if not page_products:
                break

            products.extend(page_products)
            current_url = next_url
            logger.info("Page %d: extracted %d products from %s", page_num, len(page_products), domain)

        return products[:_MAX_PRODUCTS_PER_SITE]


# ---------------------------------------------------------------------------
# API response extraction (for JS SPA sites)
# ---------------------------------------------------------------------------


def _extract_from_api_responses(
    responses: list[dict],
    page_url: str,
    domain: str,
    product_query: str = "",
) -> list[ProductResult]:
    """Extract products from intercepted XHR/fetch JSON responses.

    Recursively searches JSON structures for objects with product-like fields
    (name + price). Works for both single-product detail APIs and multi-product
    listing APIs, including price-comparison sites with seller arrays.
    """
    all_products: list[ProductResult] = []
    query_lower = product_query.lower() if product_query else ""

    for resp in responses:
        data = resp["data"]
        found = _find_products_in_json(data, domain, page_url, query_lower)
        all_products.extend(found)

    # Deduplicate by name
    seen: set[str] = set()
    unique: list[ProductResult] = []
    for p in all_products:
        key = p.name.lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return unique[:_MAX_PRODUCTS_PER_SITE]


def _find_products_in_json(
    data: object,
    domain: str,
    page_url: str,
    query_lower: str = "",
    depth: int = 0,
) -> list[ProductResult]:
    """Recursively search JSON for product-like objects."""
    if depth > 6:
        return []

    if isinstance(data, dict):
        # Check if this dict looks like a product
        has_name = any(k in data for k in _NAME_FIELDS)
        has_price = any(k in data for k in _PRICE_FIELDS)

        if has_name and has_price:
            product = _dict_to_product(data, domain, page_url)
            if product:
                return [product]

        # Check if this dict has a seller/store array (price comparison pattern)
        has_sellers = any(k in data for k in _SELLER_ARRAY_FIELDS)
        if has_name and has_sellers:
            product = _dict_to_product_with_sellers(data, domain, page_url)
            if product:
                return [product]

        # Look for arrays of products inside this dict
        for key, value in data.items():
            if isinstance(value, list) and len(value) >= 1:
                products = []
                for item in value:
                    if isinstance(item, dict):
                        sub = _find_products_in_json(item, domain, page_url, query_lower, depth + 1)
                        products.extend(sub)
                if products:
                    return products

        # Recurse into nested dicts
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
    """Get the first matching field value from a dict."""
    for field in candidates:
        if field in obj:
            return obj[field]
    return None


def _extract_price_value(obj: dict) -> tuple[float | None, str]:
    """Extract price and currency from a dict that may contain nested price objects."""
    currency = ""

    # Try currency field first
    cur_val = _get_field(obj, _CURRENCY_FIELDS)
    if isinstance(cur_val, str):
        currency = cur_val

    raw = _get_field(obj, _PRICE_FIELDS)
    if raw is None:
        return None, currency

    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw), currency
    if isinstance(raw, str):
        price = parse_price(raw)
        if price and price > 0:
            if not currency:
                currency = _detect_currency_from_text(raw)
            return price, currency
    if isinstance(raw, dict):
        # Nested price object: {"value": 1234, "currency": "ILS"}
        for subfield in ("value", "amount", "price", "raw", "final", "net"):
            if subfield in raw:
                sv = raw[subfield]
                if isinstance(sv, (int, float)) and sv > 0:
                    price = float(sv)
                elif isinstance(sv, str):
                    price = parse_price(sv)
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
    """Convert a JSON dict with product fields into a ProductResult."""
    # Name
    name_raw = _get_field(obj, _NAME_FIELDS)
    if not isinstance(name_raw, str):
        return None
    name = name_raw.strip()
    if not name or len(name) < 3:
        return None

    # Price + currency
    price, currency = _extract_price_value(obj)
    if not currency:
        currency = get_default_currency_for_domain(domain)

    # Model / SKU
    model_raw = _get_field(obj, _MODEL_FIELDS)
    model_id = str(model_raw).strip() if model_raw else None

    # Brand
    brand_raw = _get_field(obj, _BRAND_FIELDS)
    brand = None
    if isinstance(brand_raw, str):
        brand = brand_raw.strip() or None
    elif isinstance(brand_raw, dict) and "name" in brand_raw:
        brand = brand_raw["name"]

    # Image
    img_raw = _get_field(obj, _IMAGE_FIELDS)
    image_url = None
    if isinstance(img_raw, str) and ("http" in img_raw or img_raw.startswith("/")):
        image_url = img_raw
    elif isinstance(img_raw, list) and img_raw and isinstance(img_raw[0], str):
        image_url = img_raw[0]

    # Product URL from the JSON (if present)
    product_url = page_url
    url_raw = _get_field(obj, {"url", "productUrl", "product_url", "link", "Link", "Url"})
    if isinstance(url_raw, str) and ("http" in url_raw or url_raw.startswith("/")):
        product_url = url_raw if url_raw.startswith("http") else urljoin(page_url, url_raw)

    if not model_id:
        key = f"{brand or ''}{name}".lower().strip()
        model_id = hashlib.md5(key.encode()).hexdigest()[:12]

    seller = Seller(
        name=domain,
        price=price,
        currency=currency,
        url=product_url,
    )

    return ProductResult(
        name=name,
        model_id=model_id,
        brand=brand,
        image_url=image_url,
        sellers=[seller],
    )


def _dict_to_product_with_sellers(
    obj: dict, domain: str, page_url: str,
) -> ProductResult | None:
    """Convert a JSON dict with name + seller array into a ProductResult.

    Used for price-comparison sites (zap, lastprice) where the API returns
    a product with an array of sellers/stores, each with their own price.
    """
    name_raw = _get_field(obj, _NAME_FIELDS)
    if not isinstance(name_raw, str) or len(name_raw.strip()) < 3:
        return None
    name = name_raw.strip()

    # Find the seller array
    seller_array = None
    for field in _SELLER_ARRAY_FIELDS:
        if field in obj and isinstance(obj[field], list):
            seller_array = obj[field]
            break
    if not seller_array:
        return None

    # Extract sellers
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
            sellers.append(Seller(
                name=s_name,
                price=s_price,
                currency=s_currency,
                url=s_url,
            ))

    if not sellers:
        return None

    # Product-level fields
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
        name=name,
        model_id=model_id,
        brand=brand,
        image_url=image_url,
        sellers=sellers,
    )


# ---------------------------------------------------------------------------
# CSS strategy extraction
# ---------------------------------------------------------------------------


async def _extract_with_strategy(
    page: object,
    strategy: ScrapingStrategy,
    base_url: str,
    *,
    criteria: dict[str, dict] | None = None,
) -> list[ProductResult]:
    """Extract products from page using a scraping strategy."""
    from src.mcp_servers.web_scraper_mcp.spec_patterns import build_extraction_patterns

    domain = extract_domain(base_url)

    try:
        containers = await page.query_selector_all(strategy.product_container)
    except Exception:
        logger.warning("Failed to find containers with '%s'", strategy.product_container)
        return []

    # Build extraction patterns once for all products on this page
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
    strategy: ScrapingStrategy,
    base_url: str,
    domain: str,
    *,
    extraction_patterns: list[tuple[re.Pattern, str, int]] | None = None,
) -> ProductResult | None:
    """Extract a single product from a container element.

    Criteria extraction uses two phases:
    1. CSS selectors from ``strategy.criteria_selectors`` (highest priority)
    2. Text regex fallback using pre-compiled ``extraction_patterns``
    """
    # Extract name (required) — try data attribute first, then CSS selector
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

    # Reject names that are clearly navigation/UI elements rather than products
    if len(name) < 8 or name in get_garbage_names():
        return None

    # Extract price — try data attribute first, then CSS selector, then regex
    price: float | None = None
    currency = strategy.currency_hint or "USD"
    if strategy.price_attr:
        try:
            price_raw = (await container.get_attribute(strategy.price_attr) or "").strip()
            if price_raw:
                price = parse_price(price_raw)
                # Data attributes are pure numbers; use domain default currency
                if price is not None:
                    currency = get_default_currency_for_domain(domain)
        except Exception:
            pass
    if price is None and strategy.price_selector:
        try:
            price_el = await container.query_selector(strategy.price_selector)
            if price_el:
                price_text = (await price_el.inner_text()).strip()
                price = parse_price(price_text)
                detected_currency = _detect_currency_from_text(price_text)
                if detected_currency:
                    currency = detected_currency
        except Exception:
            pass

    # Fallback: extract price from container text using regex
    if price is None:
        try:
            container_text = (await container.inner_text()).strip()
            price, currency = _extract_price_from_text(container_text, currency)
        except Exception:
            pass

    # Extract product URL
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

    # Extract image URL
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

    # Extract brand
    brand: str | None = None
    if strategy.brand_selector:
        try:
            brand_el = await container.query_selector(strategy.brand_selector)
            if brand_el:
                brand = (await brand_el.inner_text()).strip()
        except Exception:
            pass

    # Extract MPN / model_id
    model_id: str | None = None
    if strategy.mpn_selector:
        try:
            mpn_el = await container.query_selector(strategy.mpn_selector)
            if mpn_el:
                model_id = (await mpn_el.inner_text()).strip()
        except Exception:
            pass

    # Fallback model_id from hash
    if not model_id:
        key = f"{brand or ''}{name}".lower().strip()
        if key:
            model_id = hashlib.md5(key.encode()).hexdigest()[:12]

    # --- Criteria extraction: CSS selectors first, then text regex fallback ---
    criteria_data: dict[str, str] = {}

    # Phase 1: CSS selectors from cached strategy
    for key, selector in strategy.criteria_selectors.items():
        try:
            el = await container.query_selector(selector)
            if el:
                text = (await el.inner_text()).strip()
                if text and len(text) < 200:
                    criteria_data[key] = text
        except Exception:
            continue

    # Phase 2: Text regex fallback for keys not yet found
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

    seller = Seller(
        name=domain,
        price=price,
        currency=currency,
        url=product_url,
    )

    return ProductResult(
        name=name,
        model_id=model_id,
        brand=brand,
        image_url=image_url,
        criteria=criteria_data,
        sellers=[seller],
    )


# ---------------------------------------------------------------------------
# JSON-LD / OG meta fallback
# ---------------------------------------------------------------------------


async def _extract_jsonld_product(
    page: object,
    url: str,
    domain: str,
) -> ProductResult | None:
    """Extract product data from JSON-LD (schema.org/Product) or OG meta tags.

    Many e-commerce sites embed structured data even when their DOM is
    rendered via JavaScript and CSS selectors fail.
    """
    try:
        data = await page.evaluate("""() => {
            // Try JSON-LD first
            const scripts = document.querySelectorAll('script[type="application/ld+json"]');
            for (const s of scripts) {
                try {
                    let obj = JSON.parse(s.textContent);
                    // Handle @graph wrapper
                    if (obj['@graph']) {
                        for (const item of obj['@graph']) {
                            if (item['@type'] === 'Product') { obj = item; break; }
                        }
                    }
                    if (obj['@type'] === 'Product') {
                        const offers = obj.offers || {};
                        // offers can be an array or object
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
            // Fallback: OG meta tags + DOM price extraction
            const getName = (sel) => {
                const el = document.querySelector(sel);
                return el ? el.getAttribute('content') || '' : '';
            };
            const ogTitle = getName('meta[property="og:title"]');

            // Try to extract price from rendered DOM
            let domPrice = null;
            let domCurrency = '';
            const pricePattern = /[$₪€£]\s*([\d,]+(?:\.\d{1,2})?)|(\d[\d,]*(?:\.\d{1,2})?)\s*[$₪€£]/;
            const priceSelectors = [
                '[class*="price"]', '[class*="Price"]',
                '[data-price]', 'span[class*="amount"]',
                '[class*="product-price"]', '[class*="ProductPrice"]',
            ];
            for (const sel of priceSelectors) {
                const els = document.querySelectorAll(sel);
                for (const el of els) {
                    const text = (el.textContent || '').trim();
                    const m = text.match(pricePattern);
                    if (m) {
                        domPrice = parseFloat((m[1] || m[2]).replace(/,/g, ''));
                        if (text.includes('₪')) domCurrency = 'ILS';
                        else if (text.includes('$')) domCurrency = 'USD';
                        else if (text.includes('€')) domCurrency = 'EUR';
                        break;
                    }
                }
                if (domPrice) break;
            }

            if (ogTitle) {
                return {
                    name: ogTitle,
                    brand: '',
                    price: domPrice,
                    currency: domCurrency,
                    image: getName('meta[property="og:image"]'),
                    mpn: '',
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
    currency = data.get("currency") or ""
    if not currency and price:
        currency = get_default_currency_for_domain(domain)

    seller = Seller(
        name=domain,
        price=price,
        currency=currency,
        url=url,
    )

    model_id = data.get("mpn") or None
    if not model_id:
        key = f"{data.get('brand', '')}{name}".lower().strip()
        model_id = hashlib.md5(key.encode()).hexdigest()[:12]

    return ProductResult(
        name=name,
        model_id=model_id,
        brand=data.get("brand") or None,
        image_url=data.get("image") or None,
        sellers=[seller],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_price_from_text(text: str, default_currency: str = "USD") -> tuple[float | None, str]:
    """Extract price from free text as a fallback when CSS selectors fail."""
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
            price = parse_price(match.group(1))
            if price is not None and price > 0:
                return price, cur

    return None, default_currency


def _merge_comparison_sellers(
    products: list[ProductResult],
    product_query: str,
    domain: str,
) -> list[ProductResult]:
    """Merge multiple single-seller products into one multi-seller product.

    On price-comparison pages each extracted "product" is really one seller
    row for the same product.  Detect this pattern and merge:

    - All products share the same name (or are seller names from data attrs)
    - All have exactly one seller each
    - The query matches none of the names (names are seller names, not product)

    Returns the original list unchanged if the pattern is not detected.
    """
    if len(products) < 2:
        return products

    # All must have exactly one seller with a price
    if not all(len(p.sellers) == 1 and p.sellers[0].price is not None for p in products):
        return products

    # Check if names look like seller/store names rather than product names.
    # Heuristic: the product query doesn't appear in any of the extracted
    # names, meaning the "name" field holds a seller name, not a product name.
    query_lower = product_query.lower()
    has_query_match = any(
        query_lower in p.name.lower() or
        (p.model_id and query_lower in p.model_id.lower())
        for p in products
    )
    if has_query_match:
        return products

    # Check for name uniformity: either all same name, or all unique
    # (unique = seller names like "Store A", "Store B")
    names = [p.name for p in products]
    unique_names = set(names)
    all_same = len(unique_names) == 1
    all_unique = len(unique_names) == len(names)

    if not (all_same or all_unique):
        return products

    # Merge: combine all sellers into one ProductResult
    sellers: list[Seller] = []
    for p in products:
        seller = p.sellers[0]
        # If all names are the same, the name is likely a generic label;
        # if all unique, each name is a seller name.
        if all_unique:
            seller.name = p.name
        sellers.append(seller)

    merged = ProductResult(
        name=product_query,
        model_id=product_query,
        brand=products[0].brand,
        image_url=products[0].image_url,
        sellers=sellers,
    )

    logger.info(
        "Merged %d comparison rows into 1 product with %d sellers for %s",
        len(products), len(sellers), domain,
    )
    return [merged]


def _detect_currency_from_text(text: str) -> str:
    """Detect currency from price text."""
    if "₪" in text:
        return "ILS"
    if "€" in text:
        return "EUR"
    if "£" in text:
        return "GBP"
    if "$" in text:
        return "USD"
    return ""
