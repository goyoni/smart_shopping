"""API response extraction (intercepted XHR/fetch JSON)."""

from __future__ import annotations

import hashlib
from urllib.parse import urljoin

from src.shared.market_config import get_default_currency_for_domain
from src.shared.models import ProductResult, Seller

from .helpers import (
    _MAX_PRODUCTS_PER_SITE,
    _detect_currency_from_text,
    _extraction_event,
    _parse_price,
)

# Field-name candidate sets for JSON API extraction
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
        if found:
            _extraction_event("api_response_parse",
                f"Found {len(found)} products from API: {resp.get('url', '?')[:120]}",
                api_url=str(resp.get("url", ""))[:200],
                product_count=len(found),
            )
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
