"""Pagination detection and multi-page extraction for listing/search pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from playwright.async_api import Page

from src.shared.logging import get_logger
from src.shared.models import ProductResult

from .helpers import _MAX_PAGES, _NEXT_PAGE_CANDIDATES, _pipeline_event

logger = get_logger(__name__)

# Common URL parameter names used for pagination
_PAGE_PARAMS = ("page", "p", "pg", "offset", "start", "from", "pagina")

# Text patterns for "next page" buttons across supported languages
_NEXT_TEXT_PATTERNS = (
    "next", "›", "»", "→", ">",
    "הבא",       # Hebrew
    "επόμενη",   # Greek
    "التالي",    # Arabic
)

# Text patterns for "load more" buttons
_LOAD_MORE_PATTERNS = (
    "load more", "show more", "see more", "view more",
    "הצג עוד", "טען עוד",           # Hebrew
    "εμφάνιση περισσότερων", "δείτε περισσότερα",  # Greek
    "عرض المزيد",                    # Arabic
)


@dataclass
class PaginationInfo:
    """Detected pagination strategy for a listing page."""

    pagination_type: str  # "link" | "load_more" | "url_param" | ""
    next_url: str = ""
    next_selector: str = ""       # CSS selector for next page link/button
    load_more_selector: str = ""  # CSS selector for load-more button
    param_name: str = ""          # URL param name (e.g. "page")
    current_page: int = 1


async def detect_pagination(page: Page, current_url: str) -> PaginationInfo:
    """Detect pagination controls on the current page.

    Tries heuristics in priority order:
    1. <a rel="next"> or <link rel="next"> → direct link
    2. CSS selectors for next-page links (from _NEXT_PAGE_CANDIDATES)
    3. URL parameter detection (page=1, p=1, etc.)
    4. Text-based detection for "Next" / "Load more" buttons
    """
    # --- 1. rel="next" ---
    for tag in ("a", "link"):
        try:
            el = await page.query_selector(f'{tag}[rel="next"]')
            if el:
                href = await el.get_attribute("href")
                if href:
                    return PaginationInfo(
                        pagination_type="link",
                        next_url=urljoin(current_url, href),
                        next_selector=f'{tag}[rel="next"]',
                    )
        except Exception:
            pass

    # --- 2. CSS selector candidates ---
    for selector in _NEXT_PAGE_CANDIDATES:
        try:
            el = await page.query_selector(selector)
            if el:
                tag_name = await el.evaluate("e => e.tagName.toLowerCase()")
                href = await el.get_attribute("href") if tag_name == "a" else None
                if href:
                    return PaginationInfo(
                        pagination_type="link",
                        next_url=urljoin(current_url, href),
                        next_selector=selector,
                    )
                # Button without href — might be a JS-driven pagination
                if tag_name == "button" or not href:
                    return PaginationInfo(
                        pagination_type="link",
                        next_selector=selector,
                    )
        except Exception:
            continue

    # --- 3. URL parameter detection ---
    parsed = urlparse(current_url)
    params = parse_qs(parsed.query)
    for param in _PAGE_PARAMS:
        if param in params:
            try:
                current_val = int(params[param][0])
            except (ValueError, IndexError):
                continue
            return PaginationInfo(
                pagination_type="url_param",
                param_name=param,
                current_page=current_val,
            )

    # --- 4. Text-based detection ---
    # Look for "Next" text links/buttons
    for pattern in _NEXT_TEXT_PATTERNS:
        try:
            # Escape special CSS characters
            selector = f'a:has-text("{pattern}"), button:has-text("{pattern}")'
            el = await page.query_selector(selector)
            if el:
                tag_name = await el.evaluate("e => e.tagName.toLowerCase()")
                href = await el.get_attribute("href") if tag_name == "a" else None
                # Verify it's in a pagination context (not random text)
                parent_classes = await el.evaluate(
                    "e => (e.closest('nav, [class*=\"pag\"], [class*=\"Pag\"]') || {}).className || ''"
                )
                if parent_classes or pattern in ("›", "»", "→", ">"):
                    if href:
                        return PaginationInfo(
                            pagination_type="link",
                            next_url=urljoin(current_url, href),
                        )
                    else:
                        return PaginationInfo(
                            pagination_type="link",
                            next_selector=selector,
                        )
        except Exception:
            continue

    # Look for "Load more" buttons
    for pattern in _LOAD_MORE_PATTERNS:
        try:
            selector = f'button:has-text("{pattern}"), a:has-text("{pattern}")'
            el = await page.query_selector(selector)
            if el:
                return PaginationInfo(
                    pagination_type="load_more",
                    load_more_selector=selector,
                )
        except Exception:
            continue

    return PaginationInfo(pagination_type="")


def build_next_page_url(current_url: str, param_name: str, current_page: int) -> str:
    """Build URL for the next page by incrementing a URL parameter."""
    parsed = urlparse(current_url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    next_page = current_page + 1
    params[param_name] = [str(next_page)]

    new_query = urlencode(params, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def build_page_url(base_url: str, param_name: str, page_num: int) -> str:
    """Build URL for a specific page number."""
    parsed = urlparse(base_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params[param_name] = [str(page_num)]
    new_query = urlencode(params, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def detect_url_pagination(url: str) -> tuple[str, int] | None:
    """Detect pagination parameters from a URL (for HTTP-based access).

    Returns (param_name, current_page) or None.
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    for param in _PAGE_PARAMS:
        if param in params:
            try:
                current_val = int(params[param][0])
                return (param, current_val)
            except (ValueError, IndexError):
                continue
    return None


def deduplicate_products(products: list[ProductResult]) -> list[ProductResult]:
    """Remove duplicate products collected across multiple pages.

    Deduplicates by (name_lower, first_seller_url) or model_id if available.
    """
    seen: set[str] = set()
    unique: list[ProductResult] = []

    for p in products:
        # Build dedup key
        if p.model_id:
            key = p.model_id.lower()
        else:
            seller_url = p.sellers[0].url if p.sellers else ""
            key = f"{p.name.lower()}|{seller_url or ''}"

        if key not in seen:
            seen.add(key)
            unique.append(p)

    return unique


async def paginate_playwright(
    page: Page,
    domain: str,
    current_url: str,
    first_page_products: list[ProductResult],
    extract_fn,
    max_pages: int = _MAX_PAGES,
) -> list[ProductResult]:
    """Follow pagination on a Playwright page and collect products.

    Args:
        page: The Playwright page (already on the first listing page).
        domain: The site domain.
        current_url: The URL of the first page.
        first_page_products: Products already extracted from page 1.
        extract_fn: Async callable(page, url) -> list[ProductResult]
            that extracts products from the current page.
        max_pages: Maximum additional pages to follow (default 3).

    Returns:
        Combined deduplicated products from all pages.
    """
    all_products = list(first_page_products)
    pagination = await detect_pagination(page, current_url)

    if not pagination.pagination_type:
        return all_products

    _pipeline_event(
        domain, "pagination",
        f"detected: type={pagination.pagination_type} selector='{pagination.next_selector}' "
        f"param={pagination.param_name}",
    )

    for page_num in range(2, max_pages + 2):  # page 2 through max_pages+1
        next_url = None

        if pagination.pagination_type == "link":
            if pagination.next_url:
                next_url = pagination.next_url
            elif pagination.next_selector:
                # Click the next button and wait for navigation
                try:
                    el = await page.query_selector(pagination.next_selector)
                    if not el:
                        _pipeline_event(domain, "pagination", f"page {page_num}: next selector not found, stopping")
                        break
                    await el.click()
                    await page.wait_for_load_state("networkidle", timeout=10000)
                    next_url = page.url
                except Exception as exc:
                    _pipeline_event(domain, "pagination", f"page {page_num}: click failed: {exc}")
                    break

        elif pagination.pagination_type == "url_param":
            next_url = build_next_page_url(
                current_url, pagination.param_name, pagination.current_page + page_num - 2,
            )

        elif pagination.pagination_type == "load_more":
            try:
                el = await page.query_selector(pagination.load_more_selector)
                if not el:
                    _pipeline_event(domain, "pagination", f"page {page_num}: load-more button gone, stopping")
                    break
                await el.click()
                await page.wait_for_load_state("networkidle", timeout=10000)
                # Extract from the same page (content was appended)
                products = await extract_fn(page, current_url)
                if products:
                    all_products.extend(products)
                    _pipeline_event(domain, "pagination",
                        f"page {page_num} (load-more): {len(products)} products", product_count=len(products))
                continue
            except Exception as exc:
                _pipeline_event(domain, "pagination", f"page {page_num}: load-more failed: {exc}")
                break

        if not next_url:
            _pipeline_event(domain, "pagination", f"page {page_num}: no next URL, stopping")
            break

        # Navigate to next page
        if next_url != page.url:
            try:
                await page.goto(next_url, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception as exc:
                _pipeline_event(domain, "pagination", f"page {page_num}: navigation failed: {exc}")
                break

        # Extract products
        products = await extract_fn(page, next_url)
        if not products:
            _pipeline_event(domain, "pagination", f"page {page_num}: 0 products, stopping")
            break

        all_products.extend(products)
        _pipeline_event(domain, "pagination",
            f"page {page_num}: {len(products)} products (total: {len(all_products)})",
            product_count=len(products))

        # Update current_url and re-detect pagination for next iteration
        current_url = next_url
        pagination = await detect_pagination(page, current_url)
        if not pagination.pagination_type:
            _pipeline_event(domain, "pagination", f"page {page_num}: no more pagination detected")
            break

    result = deduplicate_products(all_products)
    if len(result) < len(all_products):
        _pipeline_event(domain, "pagination",
            f"deduplicated {len(all_products)} -> {len(result)} products")
    return result
