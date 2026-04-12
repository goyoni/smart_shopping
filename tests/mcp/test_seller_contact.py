"""Tests for seller contact extraction and caching."""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from src.mcp_servers.web_scraper_mcp.seller_contact import (
    SellerContact,
    _extract_seller_domain,
    _is_junk_email,
    extract_contact_from_soup,
)
from src.shared.models import ProductResult, Seller


def _make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


class TestExtractContactFromSoup:
    def test_whatsapp_link(self):
        html = '<html><body><a href="https://wa.me/972501234567">WhatsApp</a></body></html>'
        contact = extract_contact_from_soup(_make_soup(html))
        assert contact.phone == "+972501234567"
        assert contact.whatsapp_url == "https://wa.me/972501234567"

    def test_whatsapp_link_with_plus(self):
        html = '<html><body><a href="https://wa.me/+302101234567">Chat</a></body></html>'
        contact = extract_contact_from_soup(_make_soup(html))
        assert contact.phone == "+302101234567"

    def test_tel_link(self):
        html = '<html><body><a href="tel:+972-2-123-4567">Call us</a></body></html>'
        contact = extract_contact_from_soup(_make_soup(html))
        assert contact.phone == "+97221234567"

    def test_tel_link_short_number_rejected(self):
        html = '<html><body><a href="tel:123">Short</a></body></html>'
        contact = extract_contact_from_soup(_make_soup(html))
        assert contact.phone == ""

    def test_mailto_link(self):
        html = '<html><body><a href="mailto:info@shop.com">Email us</a></body></html>'
        contact = extract_contact_from_soup(_make_soup(html))
        assert contact.email == "info@shop.com"

    def test_mailto_with_params(self):
        html = '<html><body><a href="mailto:sales@shop.com?subject=hello">Email</a></body></html>'
        contact = extract_contact_from_soup(_make_soup(html))
        assert contact.email == "sales@shop.com"

    def test_junk_email_filtered(self):
        html = '<html><body><a href="mailto:track@sentry.io">Report</a></body></html>'
        contact = extract_contact_from_soup(_make_soup(html))
        assert contact.email == ""

    def test_jsonld_contact_point(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@type": "Organization", "contactPoint": {"@type": "ContactPoint", "telephone": "+30-210-1234567", "email": "info@shop.gr"}}
        </script>
        </body></html>"""
        contact = extract_contact_from_soup(_make_soup(html))
        assert contact.phone == "+30-210-1234567"
        assert contact.email == "info@shop.gr"

    def test_jsonld_contact_point_list(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@type": "Organization", "contactPoint": [
            {"@type": "ContactPoint", "telephone": "+972-3-111-2222"},
            {"@type": "ContactPoint", "email": "support@store.co.il"}
        ]}
        </script>
        </body></html>"""
        contact = extract_contact_from_soup(_make_soup(html))
        assert contact.phone == "+972-3-111-2222"
        assert contact.email == "support@store.co.il"

    def test_phone_regex_in_footer(self):
        html = """<html><body>
        <footer>Contact us: +972-50-123-4567</footer>
        </body></html>"""
        contact = extract_contact_from_soup(_make_soup(html))
        assert "972" in contact.phone
        assert "50" in contact.phone

    def test_email_regex_in_footer(self):
        html = """<html><body>
        <footer>Email: support@myshop.co.il for inquiries</footer>
        </body></html>"""
        contact = extract_contact_from_soup(_make_soup(html))
        assert contact.email == "support@myshop.co.il"

    def test_whatsapp_takes_priority_over_tel(self):
        html = """<html><body>
        <a href="https://wa.me/972501111111">WA</a>
        <a href="tel:+972-2-222-3333">Call</a>
        </body></html>"""
        contact = extract_contact_from_soup(_make_soup(html))
        assert contact.phone == "+972501111111"
        assert contact.whatsapp_url == "https://wa.me/972501111111"

    def test_no_contact_info(self):
        html = '<html><body><p>Just a regular page</p></body></html>'
        contact = extract_contact_from_soup(_make_soup(html))
        assert contact.phone == ""
        assert contact.email == ""
        assert not contact.has_contact

    def test_has_contact_true(self):
        contact = SellerContact(phone="+1234567890")
        assert contact.has_contact is True

    def test_has_contact_email_only(self):
        contact = SellerContact(email="test@shop.com")
        assert contact.has_contact is True

    def test_contact_in_class_named_div(self):
        html = """<html><body>
        <div class="contact-info">Phone: +30-210-555-6789</div>
        </body></html>"""
        contact = extract_contact_from_soup(_make_soup(html))
        assert "210" in contact.phone


class TestExtractSellerDomain:
    def test_basic_url(self):
        assert _extract_seller_domain("https://shop.example.com/product/123") == "shop.example.com"

    def test_strips_www(self):
        assert _extract_seller_domain("https://www.myshop.co.il/item") == "myshop.co.il"

    def test_empty_url(self):
        assert _extract_seller_domain("") == ""

    def test_url_with_path_and_query(self):
        assert _extract_seller_domain("https://store.gr/search?q=test&page=1") == "store.gr"


class TestIsJunkEmail:
    def test_sentry_email(self):
        assert _is_junk_email("error@sentry.io") is True

    def test_google_email(self):
        assert _is_junk_email("analytics@google.com") is True

    def test_real_email(self):
        assert _is_junk_email("info@realshop.co.il") is False

    def test_example_email(self):
        assert _is_junk_email("user@example.com") is True
