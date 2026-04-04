"""HTML-based extraction methods (JSON-LD, microdata, OG meta, data-attrs, CSS listing)."""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.shared.market_config import get_default_currency_for_domain
from src.shared.models import ProductResult, Seller

from .helpers import (
    _MAX_PRODUCTS_PER_SITE,
    _MAX_SANE_PRICE,
    _extract_model_from_text,
    _extraction_event,
    _parse_price,
)


def extract_all_from_soup(
    soup: BeautifulSoup,
    url: str,
    domain: str,
    product_query: str,
) -> list[tuple[str, list[ProductResult]]]:
    """Run all HTML-based extraction methods on *soup*.

    Returns a list of ``(method_name, products)`` pairs.
    """
    results: list[tuple[str, list[ProductResult]]] = []

    page_product_name = _extract_page_product_name(soup)

    # 1. Data-attribute extraction (price-comparison aggregators)
    products = _extract_from_data_attrs(soup, url, domain, page_product_name)
    _extraction_event("data_attrs", f"{len(products)} products" if products else "0 products", product_count=len(products))
    if products:
        results.append(("data_attrs", products))

    # 2. JSON-LD (single Product)
    product = _extract_jsonld_from_soup(soup, url, domain)
    _extraction_event("jsonld", f"found: {product.name[:60]}" if product else "not found", product_count=1 if product else 0)
    if product:
        results.append(("jsonld", [product]))

    # 3. JSON-LD ItemList (product listings / search results)
    itemlist_products = _extract_jsonld_itemlist_from_soup(soup, url, domain)
    _extraction_event("jsonld_itemlist", f"{len(itemlist_products)} products" if itemlist_products else "0 products", product_count=len(itemlist_products))
    if itemlist_products:
        results.append(("jsonld_itemlist", itemlist_products))

    # 4. Microdata (itemprop)
    product = _extract_microdata_from_soup(soup, url, domain)
    _extraction_event("microdata", f"found: {product.name[:60]}" if product else "not found", product_count=1 if product else 0)
    if product:
        results.append(("microdata", [product]))

    # 5. OG meta tags
    product = _extract_og_product_from_soup(soup, url, domain)
    _extraction_event("og_meta", f"found: {product.name[:60]}" if product else "not found (og:type!=product or no price)", product_count=1 if product else 0)
    if product:
        results.append(("og_meta", [product]))

    # 6. CSS listing cards
    listing_products = _extract_listing_cards_from_soup(soup, url, domain)
    _extraction_event("css_listing", f"{len(listing_products)} products" if listing_products else "0 products (no card containers with >=2 elements)", product_count=len(listing_products))
    if listing_products:
        results.append(("css_listing", listing_products))

    methods_with_results = [(m, len(p)) for m, p in results]
    _extraction_event("soup_summary",
        f"Ran 6 methods on {domain}: {methods_with_results if methods_with_results else 'all returned 0'}",
        winning_methods=str(methods_with_results),
    )

    return results


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


def _parse_jsonld_product(
    item: dict, page_url: str, domain: str,
) -> ProductResult | None:
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
    products: list[ProductResult] = []

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        if not isinstance(data, dict) or data.get("@type") != "ItemList":
            continue

        list_elements = data.get("itemListElement", {})
        if isinstance(list_elements, dict):
            list_elements = list(list_elements.values())
        if not isinstance(list_elements, list):
            continue

        for el in list_elements:
            if not isinstance(el, dict):
                continue
            item = el.get("item", el)
            product = _parse_jsonld_product(item, page_url, domain)
            if product:
                products.append(product)

    return products[:_MAX_PRODUCTS_PER_SITE]


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

    name_el = soup.find(attrs={"itemprop": "name"})
    name = ""
    if name_el:
        name = (name_el.get("content") or name_el.get_text(strip=True) or "").strip()
    if not name:
        name = _extract_page_product_name(soup)
    if not name or len(name) < 3:
        return None

    brand = None
    brand_el = soup.find(attrs={"itemprop": "brand"})
    if brand_el:
        inner = brand_el.find(attrs={"itemprop": "name"})
        brand = ((inner or brand_el).get("content") or (inner or brand_el).get_text(strip=True) or "").strip() or None

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


def _extract_listing_cards_from_soup(
    soup: BeautifulSoup, page_url: str, domain: str,
) -> list[ProductResult]:
    products: list[ProductResult] = []
    currency = get_default_currency_for_domain(domain)

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
        name = ""
        product_url = page_url
        link = card.select_one("a[href]")
        if link:
            name = link.get_text(strip=True)[:200]
            href = link.get("href", "")
            if href:
                product_url = urljoin(page_url, href)

        if not name or len(name) < 3:
            heading = card.select_one("h2, h3, h4, [class*='name'], [class*='title']")
            if heading:
                name = heading.get_text(strip=True)[:200]

        if not name or len(name) < 3:
            continue

        price = None
        price_el = card.select_one("[class*='price'], [data-price]")
        if price_el:
            price_text = price_el.get("data-price") or price_el.get_text(strip=True)
            price = _parse_price(price_text or "")

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
