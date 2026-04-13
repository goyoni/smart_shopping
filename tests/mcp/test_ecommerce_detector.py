"""Tests for e-commerce detector module.

Tests the default-allow approach: most URLs pass through, only known
non-commerce categories are rejected.
"""

from __future__ import annotations

import pytest

from src.mcp_servers.web_search_mcp.ecommerce_detector import (
    EcommerceSignal,
    detect_ecommerce,
    extract_domain,
    identify_ecommerce_sites,
)


class TestExtractDomain:
    def test_strips_www_prefix(self):
        assert extract_domain("https://www.amazon.com/dp/123") == "amazon.com"

    def test_without_www(self):
        assert extract_domain("https://ksp.co.il/product/123") == "ksp.co.il"

    def test_with_port(self):
        assert extract_domain("http://localhost:8080/shop") == "localhost"

    def test_empty_url(self):
        assert extract_domain("") == ""


class TestHardRejections:
    """Known non-commerce domains and manufacturer sites are rejected."""

    def test_social_media_rejected(self):
        signal = detect_ecommerce("https://www.youtube.com/watch?v=xyz")
        assert signal.is_ecommerce is False
        assert signal.confidence == 0.0
        assert "known_non_ecommerce" in signal.signals

    def test_wikipedia_rejected(self):
        signal = detect_ecommerce("https://en.wikipedia.org/wiki/Microwave")
        assert signal.is_ecommerce is False

    def test_manufacturer_site_rejected(self):
        signal = detect_ecommerce("https://us.braun.com/en-us/service/products/6031")
        assert signal.is_ecommerce is False
        assert "manufacturer_site" in signal.signals

    def test_manufacturer_country_variant_rejected(self):
        signal = detect_ecommerce("https://www.braun.hu/hu-hu/male-grooming")
        assert signal.is_ecommerce is False

    def test_reddit_rejected(self):
        signal = detect_ecommerce("https://www.reddit.com/r/deals/comments/abc")
        assert signal.is_ecommerce is False

    def test_medium_rejected(self):
        signal = detect_ecommerce("https://medium.com/@user/review-of-braun-ipl")
        assert signal.is_ecommerce is False


class TestDefaultAllow:
    """Unknown sites pass through by default — the scraper decides."""

    def test_unknown_site_passes(self):
        signal = detect_ecommerce(
            "https://www.alm.co.il/842002772.html",
            title="מכשיר להסרת שיער Braun Silk Expert Pro 5",
        )
        assert signal.is_ecommerce is True
        assert signal.confidence > 0

    def test_unknown_site_no_title_passes(self):
        signal = detect_ecommerce("https://www.somestore.co.il/page")
        assert signal.is_ecommerce is True

    def test_unknown_blog_no_path_passes(self):
        """A blog-ish domain without /blog/ in the path still passes."""
        signal = detect_ecommerce(
            "https://blog.example.com/post/123",
            title="My Blog Post",
        )
        assert signal.is_ecommerce is True


class TestPenalties:
    """Non-commerce URL path patterns reduce confidence."""

    def test_blog_path_penalized(self):
        signal = detect_ecommerce("https://example.com/blog/best-ipl-review")
        assert signal.is_ecommerce is False
        assert any("non_ecommerce_path" in s for s in signal.signals)

    def test_news_path_penalized(self):
        signal = detect_ecommerce("https://example.com/news/tech-deals-2025")
        assert signal.is_ecommerce is False

    def test_forum_path_penalized(self):
        signal = detect_ecommerce("https://example.com/forum/thread/12345")
        assert signal.is_ecommerce is False

    def test_wiki_path_penalized(self):
        signal = detect_ecommerce("https://example.com/wiki/Product_Guide")
        assert signal.is_ecommerce is False


class TestBoosts:
    """E-commerce path patterns boost confidence above baseline."""

    def test_product_path_boosted(self):
        signal = detect_ecommerce(
            "https://www.public.gr/product/prosopiki-frontida/1925186"
        )
        assert signal.is_ecommerce is True
        assert signal.confidence > 0.5
        assert any("path_pattern" in s for s in signal.signals)

    def test_dp_path_boosted(self):
        signal = detect_ecommerce("https://www.amazon.com/dp/B123")
        assert signal.is_ecommerce is True
        assert any("path_pattern" in s for s in signal.signals)

    def test_items_path_boosted(self):
        signal = detect_ecommerce(
            "https://www.dynamica.co.il/items/7414711-Braun-IPL-Pro-PL5156"
        )
        assert signal.is_ecommerce is True
        assert any("path_pattern" in s for s in signal.signals)

    def test_catalog_path_boosted(self):
        signal = detect_ecommerce(
            "https://www.ivory.co.il/catalog.php?id=105270"
        )
        assert signal.is_ecommerce is True
        assert any("path_pattern" in s for s in signal.signals)

    def test_shop_path_boosted(self):
        signal = detect_ecommerce("https://mysite.com/shop/item-456")
        assert signal.is_ecommerce is True

    def test_item_path_boosted(self):
        signal = detect_ecommerce("https://www.ebay.com/item/123")
        assert signal.is_ecommerce is True


class TestIdentifyEcommerceSites:
    @pytest.mark.asyncio
    async def test_filters_non_ecommerce(self):
        urls_data = [
            {"url": "https://www.amazon.com/dp/B123", "title": "Widget"},
            {"url": "https://www.youtube.com/watch?v=xyz", "title": "Review"},
            {"url": "https://www.alm.co.il/842002772.html", "title": "Product"},
        ]
        results = await identify_ecommerce_sites(urls_data)
        ecom_domains = {r.domain for r in results}
        assert "amazon.com" in ecom_domains
        assert "alm.co.il" in ecom_domains
        assert "youtube.com" not in ecom_domains

    @pytest.mark.asyncio
    async def test_empty_input(self):
        assert await identify_ecommerce_sites([]) == []

    @pytest.mark.asyncio
    async def test_handles_missing_fields(self):
        urls_data = [{"url": "https://www.somestore.com/page"}]
        results = await identify_ecommerce_sites(urls_data)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_market_tld_boost(self):
        """Greek sites should rank above non-market sites when market=gr."""
        urls_data = [
            {"url": "https://www.bestbuy.com/product/widget/123", "title": "Buy widget"},
            {"url": "https://www.public.gr/product/widget/123", "title": "Widget"},
        ]
        results = await identify_ecommerce_sites(urls_data, market="gr")
        assert len(results) == 2
        assert results[0].domain == "public.gr"
        assert any("market_match" in s for s in results[0].signals)

    @pytest.mark.asyncio
    async def test_foreign_market_tld_penalized(self):
        """Czech .cz sites should be penalized when market=il."""
        urls_data = [
            {"url": "https://www.alza.cz/product/braun/123", "title": "Braun"},
            {"url": "https://www.dynamica.co.il/items/123", "title": "Braun"},
        ]
        results = await identify_ecommerce_sites(urls_data, market="il")
        il_site = next(r for r in results if r.domain == "dynamica.co.il")
        cz_site = next(r for r in results if r.domain == "alza.cz")
        assert il_site.confidence > cz_site.confidence


class TestTraceScenario:
    """Replay the actual URLs from the failing trace to ensure they all pass."""

    def test_all_false_negatives_now_pass(self):
        false_negatives = [
            ("https://www.dynamica.co.il/items/7414711-Braun-IPL-Pro-PL5156",
             "מכשיר להסרת שיער Braun IPL Silk Expert Pro 5 PL5156 - לקנייה אונליין | דינמיקה"),
            ("https://www.alm.co.il/842002772.html",
             "מכשיר להסרת שיער Braun Silk Expert Pro 5 IPL 5 PL5052 בראון"),
            ("https://www.ivory.co.il/catalog.php?id=105270",
             "מכשיר להסרת שיער בראון Braun Silk-expert Pro 5 PL5243 IPL - אייבורי מחשבים וסלולר"),
            ("https://www.eilatdepot.co.il/items/6960482-braun",
             "מסיר שיער Braun Silk expert Pro 5 PL5152 IPL בראון - אילת דיפו"),
        ]
        for url, title in false_negatives:
            signal = detect_ecommerce(url, title)
            assert signal.is_ecommerce is True, f"{signal.domain} should pass but was rejected"

    def test_correct_rejections_still_rejected(self):
        should_reject = [
            ("https://www.braun.cz/cs-cz", "Braun CZ homepage"),
            ("https://us.braun.com/en-us", "Braun US"),
            ("https://www.bbraun.cz/cs.html", "B. Braun medical"),
        ]
        for url, title in should_reject:
            signal = detect_ecommerce(url, title)
            assert signal.is_ecommerce is False, f"{signal.domain} should be rejected"
