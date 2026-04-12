"""Seller contact info extraction and caching.

Extracts phone numbers (especially WhatsApp), emails from seller pages.
Caches results per domain to avoid re-scraping.

Two-phase approach:
  Phase A — Inline: extract from page HTML we already have (free).
  Phase B — Dedicated: visit seller homepage/contact page if needed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag
from playwright.async_api import Browser, Page
from sqlalchemy import select

from src.backend.db.engine import async_session
from src.backend.db.models import SellerContactCache
from src.shared.logging import get_logger, get_tracer, operation_span
from src.shared.market_config import get_contact_paths
from src.shared.models import ProductResult

logger = get_logger(__name__)
_tracer = get_tracer(__name__)

_CACHE_TTL_DAYS = 30

# International phone pattern — matches +972-..., +30 ..., (02) 123-4567, etc.
_PHONE_RE = re.compile(
    r"""(?<!\d)                     # not preceded by digit
    (?:\+?\d{1,3}[-.\s]?)?         # optional country code
    \(?0?\d{1,4}\)?                # area code (optional parens)
    [-.\s]?\d{2,4}                 # first group
    [-.\s]?\d{3,4}                 # second group
    (?!\d)""",                     # not followed by digit
    re.VERBOSE,
)

# Email pattern
_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)

# Domains to skip for email (tracking pixels, not real contacts)
_EMAIL_SKIP_DOMAINS = {
    "sentry.io", "google.com", "facebook.com", "example.com",
    "wixpress.com", "yoursite.com", "yourdomain.com",
}


@dataclass
class SellerContact:
    """Extracted contact info for a seller domain."""

    phone: str = ""
    email: str = ""
    whatsapp_url: str = ""
    source: str = ""  # "inline", "homepage", "contact_page"

    @property
    def has_contact(self) -> bool:
        return bool(self.phone or self.email)


# ------------------------------------------------------------------
# Extraction from HTML (BeautifulSoup)
# ------------------------------------------------------------------


def extract_contact_from_soup(soup: BeautifulSoup, domain: str = "") -> SellerContact:
    """Extract phone/email from parsed HTML.

    Checks: WhatsApp links → tel: links → mailto: links →
    JSON-LD contactPoint → regex in footer/header.
    """
    phone = ""
    email = ""
    whatsapp_url = ""

    # 1. WhatsApp links (highest priority)
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "wa.me" in href or "whatsapp.com" in href or "whatsapp://" in href:
            whatsapp_url = href
            # Extract phone from wa.me/+972... or wa.me/972...
            match = re.search(r"wa\.me/(\+?\d+)", href)
            if match:
                phone = "+" + match.group(1).lstrip("+")
            break

    # 2. tel: links
    if not phone:
        for link in soup.find_all("a", href=re.compile(r"^tel:", re.I)):
            raw = link["href"].replace("tel:", "").strip()
            cleaned = re.sub(r"[^\d+]", "", raw)
            if len(cleaned) >= 7:
                phone = cleaned
                break

    # 3. mailto: links
    if not email:
        for link in soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
            raw = link["href"].replace("mailto:", "").split("?")[0].strip()
            if _EMAIL_RE.match(raw) and not _is_junk_email(raw):
                email = raw
                break

    # 4. JSON-LD contactPoint
    if not phone or not email:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, dict):
                    cp = data.get("contactPoint")
                    if isinstance(cp, dict):
                        if not phone and cp.get("telephone"):
                            phone = cp["telephone"]
                        if not email and cp.get("email"):
                            email = cp["email"]
                    elif isinstance(cp, list):
                        for point in cp:
                            if isinstance(point, dict):
                                if not phone and point.get("telephone"):
                                    phone = point["telephone"]
                                if not email and point.get("email"):
                                    email = point["email"]
            except (json.JSONDecodeError, TypeError):
                continue

    # 5. Regex fallback — search footer and header areas only
    if not phone or not email:
        for section in _find_contact_sections(soup):
            text = section.get_text(separator=" ", strip=True)
            if not phone:
                match = _PHONE_RE.search(text)
                if match:
                    candidate = re.sub(r"[^\d+]", "", match.group())
                    if len(candidate) >= 7:
                        phone = candidate
            if not email:
                match = _EMAIL_RE.search(text)
                if match and not _is_junk_email(match.group()):
                    email = match.group()

    return SellerContact(phone=phone, email=email, whatsapp_url=whatsapp_url)


def _find_contact_sections(soup: BeautifulSoup) -> list[Tag]:
    """Find HTML sections likely to contain contact info."""
    sections: list[Tag] = []

    # Footer
    for tag in ("footer", ):
        for el in soup.find_all(tag):
            sections.append(el)

    # Header
    for el in soup.find_all("header"):
        sections.append(el)

    # Contact-related divs
    for el in soup.find_all(
        class_=re.compile(r"contact|phone|footer|header", re.I)
    ):
        sections.append(el)

    # Aria-label contact sections
    for el in soup.find_all(attrs={"aria-label": re.compile(r"contact", re.I)}):
        sections.append(el)

    return sections


def _is_junk_email(email: str) -> bool:
    """Filter out tracking/placeholder emails."""
    domain = email.split("@")[-1].lower()
    return any(skip in domain for skip in _EMAIL_SKIP_DOMAINS)


# ------------------------------------------------------------------
# Extraction from Playwright Page
# ------------------------------------------------------------------

_CONTACT_JS = """() => {
    const results = { phones: [], emails: [], whatsapp: [] };

    // WhatsApp links
    document.querySelectorAll('a[href*="wa.me"], a[href*="whatsapp.com"], a[href*="whatsapp://"]')
        .forEach(a => results.whatsapp.push(a.href));

    // tel: links
    document.querySelectorAll('a[href^="tel:"]')
        .forEach(a => results.phones.push(a.href.replace('tel:', '').trim()));

    // mailto: links
    document.querySelectorAll('a[href^="mailto:"]')
        .forEach(a => {
            const email = a.href.replace('mailto:', '').split('?')[0].trim();
            if (email.includes('@')) results.emails.push(email);
        });

    return results;
}"""


async def extract_contact_from_page(page: Page, domain: str = "") -> SellerContact:
    """Extract contact info from a live Playwright page."""
    try:
        data = await page.evaluate(_CONTACT_JS)
    except Exception:
        return SellerContact()

    phone = ""
    email = ""
    whatsapp_url = ""

    # WhatsApp
    if data.get("whatsapp"):
        whatsapp_url = data["whatsapp"][0]
        match = re.search(r"wa\.me/(\+?\d+)", whatsapp_url)
        if match:
            phone = "+" + match.group(1).lstrip("+")

    # Phone
    if not phone and data.get("phones"):
        for raw in data["phones"]:
            cleaned = re.sub(r"[^\d+]", "", raw)
            if len(cleaned) >= 7:
                phone = cleaned
                break

    # Email
    if data.get("emails"):
        for raw in data["emails"]:
            if _EMAIL_RE.match(raw) and not _is_junk_email(raw):
                email = raw
                break

    # If JS extraction found nothing, try getting page content for soup fallback
    if not phone and not email:
        try:
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            return extract_contact_from_soup(soup, domain)
        except Exception:
            pass

    return SellerContact(phone=phone, email=email, whatsapp_url=whatsapp_url)


# ------------------------------------------------------------------
# Dedicated contact page scraping (Phase B)
# ------------------------------------------------------------------


async def scrape_seller_contact(
    browser: Browser,
    domain: str,
    market: str = "us",
) -> SellerContact:
    """Visit a seller's homepage and contact page to extract contact info.

    Tries homepage first, then known contact page paths.
    """
    from src.shared.browser import dismiss_consent, get_page

    with operation_span(
        _tracer, f"scrape_seller_contact:{domain}",
        input=domain,
    ) as span:
        # Try homepage first (with and without www.)
        for prefix in (domain, f"www.{domain}"):
            contact = await _try_page(
                browser, f"https://{prefix}", domain, market,
            )
            if contact.has_contact:
                contact.source = "homepage"
                span.set_attribute("summary",
                    f"Found contact on {prefix}: phone={contact.phone!r} email={contact.email!r}")
                await save_contact(domain, contact)
                return contact

        # Try contact pages (with and without www.)
        for path in get_contact_paths(market):
            for prefix in (domain, f"www.{domain}"):
                url = f"https://{prefix}{path}"
                contact = await _try_page(browser, url, domain, market)
                if contact.has_contact:
                    contact.source = "contact_page"
                    span.set_attribute("summary",
                        f"Found contact on {prefix}{path}: phone={contact.phone!r} email={contact.email!r}")
                    await save_contact(domain, contact)
                    return contact

        span.set_attribute("summary", f"No contact info found for {domain}")
        # Cache the negative result to avoid re-scraping
        await save_contact(domain, SellerContact(source="not_found"))
        return SellerContact()


async def _try_page(
    browser: Browser,
    url: str,
    domain: str,
    market: str,
) -> SellerContact:
    """Visit a URL and extract contact info."""
    from src.shared.browser import dismiss_consent, get_page

    try:
        async with get_page(browser, market=market) as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await dismiss_consent(page, domain)
            return await extract_contact_from_page(page, domain)
    except Exception as exc:
        logger.debug("Failed to scrape contact from %s: %s", url, exc)
        return SellerContact()


# ------------------------------------------------------------------
# DB cache
# ------------------------------------------------------------------


async def get_cached_contact(domain: str) -> SellerContact | None:
    """Load cached contact info for a domain."""
    async with async_session() as session:
        stmt = select(SellerContactCache).where(
            SellerContactCache.domain == domain,
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            return None

        # TTL check
        now = datetime.now(timezone.utc)
        created = record.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if now - created > timedelta(days=_CACHE_TTL_DAYS):
            return None

        return SellerContact(
            phone=record.phone or "",
            email=record.email or "",
            whatsapp_url=record.whatsapp_url or "",
            source=record.source or "",
        )


async def save_contact(domain: str, contact: SellerContact) -> None:
    """Save or update cached contact info for a domain."""
    async with async_session() as session:
        stmt = select(SellerContactCache).where(
            SellerContactCache.domain == domain,
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()

        if record:
            record.phone = contact.phone or None
            record.email = contact.email or None
            record.whatsapp_url = contact.whatsapp_url or None
            record.source = contact.source
            record.updated_at = datetime.now(timezone.utc)
        else:
            record = SellerContactCache(
                domain=domain,
                phone=contact.phone or None,
                email=contact.email or None,
                whatsapp_url=contact.whatsapp_url or None,
                source=contact.source,
            )
            session.add(record)

        await session.commit()
        logger.info("Saved seller contact for %s: phone=%s email=%s source=%s",
                     domain, contact.phone, contact.email, contact.source)


# ------------------------------------------------------------------
# Enrichment: populate sellers with contact info
# ------------------------------------------------------------------


def _extract_seller_domain(seller_url: str) -> str:
    """Extract domain from a seller URL, stripping www."""
    parsed = urlparse(seller_url)
    domain = parsed.hostname or ""
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


async def enrich_sellers_inline(
    products: list[ProductResult],
    contact: SellerContact,
    domain: str,
) -> None:
    """Apply inline-extracted contact info to all sellers on the same domain.

    Modifies products in-place.
    """
    if not contact.has_contact:
        return

    for product in products:
        for seller in product.sellers:
            seller_domain = _extract_seller_domain(seller.url or "")
            if seller_domain == domain or not seller_domain:
                if not seller.phone and contact.phone:
                    seller.phone = contact.phone
                if not seller.email and contact.email:
                    seller.email = contact.email


async def enrich_sellers(
    products: list[ProductResult],
    browser: Browser,
    market: str = "us",
) -> list[ProductResult]:
    """Enrich all sellers with contact info from cache or by scraping.

    For each unique seller domain:
    1. Check cache → use if fresh
    2. Scrape homepage/contact page → cache result
    3. Apply to all sellers on that domain
    """
    # Collect unique seller domains that need contact info
    domains_to_enrich: set[str] = set()
    for product in products:
        for seller in product.sellers:
            if not seller.phone and not seller.email and seller.url:
                domain = _extract_seller_domain(seller.url)
                if domain:
                    domains_to_enrich.add(domain)

    if not domains_to_enrich:
        return products

    logger.info("Enriching contact info for %d seller domains", len(domains_to_enrich))

    # Resolve contact info for each domain
    contacts: dict[str, SellerContact] = {}
    for domain in domains_to_enrich:
        # Check cache first
        cached = await get_cached_contact(domain)
        if cached:
            contacts[domain] = cached
            logger.debug("Contact cache hit for %s: phone=%s", domain, cached.phone)
            continue

        # Scrape
        contact = await scrape_seller_contact(browser, domain, market)
        contacts[domain] = contact

    # Apply contact info to sellers
    for product in products:
        for seller in product.sellers:
            if seller.phone and seller.email:
                continue
            seller_domain = _extract_seller_domain(seller.url or "")
            if seller_domain in contacts:
                contact = contacts[seller_domain]
                if not seller.phone and contact.phone:
                    seller.phone = contact.phone
                if not seller.email and contact.email:
                    seller.email = contact.email

    enriched_count = sum(
        1 for d, c in contacts.items() if c.has_contact
    )
    logger.info("Enriched %d/%d seller domains with contact info",
                 enriched_count, len(domains_to_enrich))

    return products
