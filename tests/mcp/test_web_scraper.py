"""Tests for web scraper module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_servers.web_scraper_mcp.scraper import (
    _extract_from_data_attrs,
    _extract_jsonld_from_soup,
    _extract_microdata_from_soup,
    _extract_og_product_from_soup,
    _extract_page_product_name,
    _find_next_page_url,
    _is_safe_url,
    _merge_comparison_sellers,
    _try_http_prefetch,
    extract_domain,
    extract_specs_from_text,
    parse_price,
    scrape_page,
)
from src.mcp_servers.web_scraper_mcp.strategy import ScrapingStrategy
from src.shared.models import ProductResult, Seller


class TestParsePrice:
    def test_usd_price(self):
        assert parse_price("$299.99") == 299.99

    def test_nis_price(self):
        assert parse_price("₪1,299") == 1299.0

    def test_european_format(self):
        assert parse_price("1.299,99 €") == 1299.99

    def test_plain_number(self):
        assert parse_price("450") == 450.0

    def test_empty_string(self):
        assert parse_price("") is None

    def test_no_digits(self):
        assert parse_price("free") is None

    def test_decimal_comma(self):
        assert parse_price("12,99") == 12.99

    def test_thousands_comma(self):
        assert parse_price("1,299") == 1299.0

    def test_rejects_insane_price(self):
        # Concatenated digits from multiple price elements
        assert parse_price("25802480") is None

    def test_accepts_high_but_sane_price(self):
        assert parse_price("999,999") == 999999.0

    def test_rejects_above_million(self):
        assert parse_price("1,500,000") is None


class TestExtractDomain:
    def test_strips_www(self):
        assert extract_domain("https://www.amazon.com/dp/123") == "amazon.com"

    def test_without_www(self):
        assert extract_domain("https://ksp.co.il/product/1") == "ksp.co.il"


class TestScrapePageWithNoStrategy:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_strategy_found(self):
        mock_page = AsyncMock()
        mock_page.query_selector_all.return_value = []  # No containers found

        mock_browser = AsyncMock()

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.get_page") as mock_get_page,
            patch("src.mcp_servers.web_scraper_mcp.scraper.get_cached_strategy", return_value=None),
            patch("src.mcp_servers.web_scraper_mcp.scraper.discover_strategy", return_value=None),
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_page)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_page.return_value = mock_ctx

            results = await scrape_page(mock_browser, "https://shop.example.com/search?q=laptop")

        assert results == []


class TestScrapePageWithCachedStrategy:
    @pytest.mark.asyncio
    async def test_uses_cached_strategy(self):
        strategy = ScrapingStrategy(
            product_container=".product-card",
            name_selector="h2",
            price_selector=".price",
        )

        mock_name_el = AsyncMock()
        mock_name_el.inner_text.return_value = "Test Laptop"

        mock_price_el = AsyncMock()
        mock_price_el.inner_text.return_value = "$999.99"

        mock_container = AsyncMock()

        async def mock_query_selector(selector):
            if selector == "h2":
                return mock_name_el
            if selector == ".price":
                return mock_price_el
            return None

        mock_container.query_selector = mock_query_selector
        mock_container.inner_text = AsyncMock(return_value="Test Laptop $999.99")

        mock_page = AsyncMock()
        mock_page.query_selector_all.return_value = [mock_container]

        mock_browser = AsyncMock()

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.get_page") as mock_get_page,
            patch("src.mcp_servers.web_scraper_mcp.scraper.get_cached_strategy", return_value=strategy),
            patch("src.mcp_servers.web_scraper_mcp.scraper.update_success_rate") as mock_update,
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_page)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_page.return_value = mock_ctx

            results = await scrape_page(mock_browser, "https://shop.example.com/search?q=laptop")

        assert len(results) == 1
        assert results[0].name == "Test Laptop"
        assert results[0].sellers[0].price == 999.99
        assert results[0].sellers[0].currency == "USD"
        mock_update.assert_awaited_once_with("shop.example.com", success=True, page_type="search")


class TestScrapePageCachedStrategyFailure:
    @pytest.mark.asyncio
    async def test_re_discovers_on_cached_failure(self):
        cached_strategy = ScrapingStrategy(
            product_container=".old-selector",
            name_selector="h2",
        )
        new_strategy = ScrapingStrategy(
            product_container=".new-card",
            name_selector="h3",
        )

        mock_name_el = AsyncMock()
        mock_name_el.inner_text.return_value = "New Product"

        mock_container = AsyncMock()

        async def mock_qs(selector):
            if selector == "h3":
                return mock_name_el
            return None

        mock_container.query_selector = mock_qs
        mock_container.inner_text = AsyncMock(return_value="New Product")

        mock_page = AsyncMock()

        call_count = 0

        async def mock_query_selector_all(selector):
            nonlocal call_count
            call_count += 1
            if selector == ".old-selector":
                return []  # Cached strategy fails
            if selector == ".new-card":
                return [mock_container]
            return []

        mock_page.query_selector_all = mock_query_selector_all

        mock_browser = AsyncMock()

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.get_page") as mock_get_page,
            patch("src.mcp_servers.web_scraper_mcp.scraper.get_cached_strategy", return_value=cached_strategy),
            patch("src.mcp_servers.web_scraper_mcp.scraper.update_success_rate") as mock_update,
            patch("src.mcp_servers.web_scraper_mcp.scraper.discover_strategy", return_value=new_strategy),
            patch("src.mcp_servers.web_scraper_mcp.scraper.save_strategy") as mock_save,
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_page)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_page.return_value = mock_ctx

            results = await scrape_page(mock_browser, "https://shop.example.com/products")

        assert len(results) == 1
        assert results[0].name == "New Product"
        # Cached strategy failure should decrement success rate
        mock_update.assert_awaited_with("shop.example.com", success=False, page_type="page")
        # New strategy should be saved
        mock_save.assert_awaited_once()


class TestExtractSpecsFromText:
    def test_extracts_noise_level(self):
        specs = extract_specs_from_text("Samsung Refrigerator 39 dB noise")
        assert specs["noise_level"] == "39 dB"

    def test_extracts_capacity(self):
        specs = extract_specs_from_text("350 liters capacity")
        assert specs["capacity"] == "350 liters"

    def test_extracts_capacity_L(self):
        specs = extract_specs_from_text("Fridge 400L")
        assert specs["capacity"] == "400L"

    def test_extracts_energy_rating(self):
        specs = extract_specs_from_text("A+ Energy class")
        assert specs["energy_rating"] == "A+"

    def test_extracts_resolution(self):
        specs = extract_specs_from_text('Samsung 55" 4K Smart TV')
        assert specs["resolution"] == "4K"
        assert specs["screen_size"] == '55"'

    def test_extracts_processor(self):
        specs = extract_specs_from_text("Laptop i7-13700H 16GB RAM 512GB SSD")
        assert specs["processor"] == "i7-13700H"
        assert specs["ram"] == "16GB RAM"
        assert specs["storage"] == "512GB SSD"

    def test_extracts_anc(self):
        specs = extract_specs_from_text("Sony WH-1000XM5 ANC Headphones")
        assert specs["noise_cancelling"] == "ANC"

    def test_extracts_weight(self):
        specs = extract_specs_from_text("Weight: 55 kg")
        assert specs["weight"] == "55 kg"

    def test_empty_text(self):
        assert extract_specs_from_text("") == {}

    def test_no_specs(self):
        assert extract_specs_from_text("A great product for your home") == {}

    def test_multiple_specs(self):
        text = "LG Refrigerator 350L 39 dB A+ Energy Frost-Free"
        specs = extract_specs_from_text(text)
        assert "capacity" in specs
        assert "noise_level" in specs
        assert "energy_rating" in specs
        assert "frost_free" in specs


class TestExtractSpecsWithCriteria:
    """Tests for extract_specs_from_text with explicit criteria dicts."""

    def test_custom_criteria_noise(self):
        criteria = {"noise_level": {"unit": "dB"}}
        specs = extract_specs_from_text("Operating at 42 dB", criteria=criteria)
        assert "noise_level" in specs

    def test_custom_criteria_unknown_unit(self):
        criteria = {"mystery": {"unit": "zorps"}}
        specs = extract_specs_from_text("mystery 5 zorps", criteria=criteria)
        assert specs == {}

    def test_custom_criteria_weight(self):
        criteria = {"weight": {"unit": "kg"}}
        specs = extract_specs_from_text("Total weight 12 kg", criteria=criteria)
        assert specs["weight"] == "12 kg"

    def test_custom_criteria_only_extracts_requested(self):
        criteria = {"noise_level": {"unit": "dB"}}
        text = "Weight: 55 kg, Noise: 39 dB, 350 liters"
        specs = extract_specs_from_text(text, criteria=criteria)
        assert "noise_level" in specs
        # weight and capacity should NOT be extracted since not in criteria
        assert "weight" not in specs
        assert "capacity" not in specs


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestFindNextPageUrl:
    @pytest.mark.asyncio
    async def test_finds_next_link_by_rel(self):
        mock_el = AsyncMock()
        mock_el.get_attribute.return_value = "/page/2"

        mock_page = AsyncMock()

        async def mock_qs(selector):
            if selector == "a[rel='next']":
                return mock_el
            return None

        mock_page.query_selector = mock_qs

        result = await _find_next_page_url(mock_page, "https://shop.example.com")
        assert result == "https://shop.example.com/page/2"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_next(self):
        mock_page = AsyncMock()
        mock_page.query_selector.return_value = None

        result = await _find_next_page_url(mock_page, "https://shop.example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolves_relative_url(self):
        mock_el = AsyncMock()
        mock_el.get_attribute.return_value = "?page=3"

        mock_page = AsyncMock()

        async def mock_qs(selector):
            if "aria-label" in selector and "next" in selector.lower():
                return mock_el
            return None

        mock_page.query_selector = mock_qs

        result = await _find_next_page_url(mock_page, "https://shop.example.com/search")
        assert result == "https://shop.example.com/search?page=3"


class TestScrapePagePagination:
    @pytest.mark.asyncio
    async def test_follows_pagination(self):
        strategy = ScrapingStrategy(
            product_container=".product-card",
            name_selector="h2",
            price_selector=".price",
        )

        def make_container(name, price_text):
            mock_name_el = AsyncMock()
            mock_name_el.inner_text.return_value = name
            mock_price_el = AsyncMock()
            mock_price_el.inner_text.return_value = price_text
            container = AsyncMock()

            async def qs(selector):
                if selector == "h2":
                    return mock_name_el
                if selector == ".price":
                    return mock_price_el
                return None

            container.query_selector = qs
            container.inner_text = AsyncMock(return_value=f"{name} {price_text}")
            return container

        page1_containers = [make_container("Product A", "$100")]
        page2_containers = [make_container("Product B", "$200")]

        goto_count = 0

        mock_page = AsyncMock()

        async def mock_query_all(selector):
            nonlocal goto_count
            if selector == ".product-card":
                return page1_containers if goto_count <= 1 else page2_containers
            return []

        mock_page.query_selector_all = mock_query_all

        # First call: no next link (page 1 already loaded via goto)
        # After page 1 extraction, _find_next_page_url is called
        next_link_el = AsyncMock()
        next_link_el.get_attribute.return_value = "/page/2"

        qs_call_count = 0

        async def mock_qs(selector):
            nonlocal qs_call_count
            qs_call_count += 1
            # Only return next link after page 1
            if goto_count <= 1 and "next" in selector.lower():
                return next_link_el
            return None

        mock_page.query_selector = mock_qs

        original_goto = mock_page.goto

        async def track_goto(*args, **kwargs):
            nonlocal goto_count
            goto_count += 1

        mock_page.goto = track_goto

        mock_browser = AsyncMock()

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.get_page") as mock_get_page,
            patch("src.mcp_servers.web_scraper_mcp.scraper.get_cached_strategy", return_value=strategy),
            patch("src.mcp_servers.web_scraper_mcp.scraper.update_success_rate"),
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_page)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_page.return_value = mock_ctx

            results = await scrape_page(mock_browser, "https://shop.example.com/search")

        # Should have products from both pages
        assert len(results) == 2
        assert results[0].name == "Product A"
        assert results[1].name == "Product B"


# ---------------------------------------------------------------------------
# Data-attribute extraction
# ---------------------------------------------------------------------------


class TestDataAttributeExtraction:
    @pytest.mark.asyncio
    async def test_extracts_from_data_attrs_and_merges_sellers(self):
        """When strategy has name_attr/price_attr, read from container attributes.

        Two seller rows with different names/prices should merge into
        one product with multiple sellers (comparison-page pattern).
        """
        strategy = ScrapingStrategy(
            product_container="[data-site-name]",
            name_attr="data-site-name",
            price_attr="data-product-price",
        )

        def make_seller_container(store_name, price):
            container = AsyncMock()

            async def get_attr(attr):
                if attr == "data-site-name":
                    return store_name
                if attr == "data-product-price":
                    return str(price)
                return None

            container.get_attribute = get_attr
            container.query_selector = AsyncMock(return_value=None)
            container.inner_text = AsyncMock(return_value=f"{store_name} ₪{price}")
            return container

        c1 = make_seller_container("Store Alpha", 1299)
        c2 = make_seller_container("Store Beta", 1199)

        mock_page = AsyncMock()
        mock_page.query_selector_all.return_value = [c1, c2]

        mock_browser = AsyncMock()

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.get_page") as mock_get_page,
            patch("src.mcp_servers.web_scraper_mcp.scraper.get_cached_strategy", return_value=strategy),
            patch("src.mcp_servers.web_scraper_mcp.scraper.update_success_rate"),
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_page)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_page.return_value = mock_ctx

            results = await scrape_page(
                mock_browser,
                "https://shop.example.co.il/product/123",
                product_query="BFL523MB1F",
            )

        # Should merge into one product with two sellers
        assert len(results) == 1
        assert results[0].name == "BFL523MB1F"
        assert len(results[0].sellers) == 2
        assert results[0].sellers[0].name == "Store Alpha"
        assert results[0].sellers[0].price == 1299.0
        assert results[0].sellers[1].name == "Store Beta"
        assert results[0].sellers[1].price == 1199.0

    @pytest.mark.asyncio
    async def test_falls_back_to_css_when_no_data_attr(self):
        """When data attr is empty, fall back to CSS sub-selector."""
        strategy = ScrapingStrategy(
            product_container=".product-card",
            name_selector="h2",
            price_selector=".price",
            name_attr="data-site-name",  # Set but won't match
        )

        mock_container = AsyncMock()

        async def mock_get_attribute(attr):
            return None  # No data attributes present

        mock_name_el = AsyncMock()
        mock_name_el.inner_text.return_value = "Test Product Name Here"

        mock_price_el = AsyncMock()
        mock_price_el.inner_text.return_value = "$599"

        async def mock_query_selector(selector):
            if selector == "h2":
                return mock_name_el
            if selector == ".price":
                return mock_price_el
            return None

        mock_container.get_attribute = mock_get_attribute
        mock_container.query_selector = mock_query_selector
        mock_container.inner_text = AsyncMock(return_value="Test Product Name Here $599")

        mock_page = AsyncMock()
        mock_page.query_selector_all.return_value = [mock_container]

        mock_browser = AsyncMock()

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.get_page") as mock_get_page,
            patch("src.mcp_servers.web_scraper_mcp.scraper.get_cached_strategy", return_value=strategy),
            patch("src.mcp_servers.web_scraper_mcp.scraper.update_success_rate"),
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_page)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_page.return_value = mock_ctx

            results = await scrape_page(mock_browser, "https://shop.example.com/search?q=laptop")

        assert len(results) == 1
        assert results[0].name == "Test Product Name Here"
        assert results[0].sellers[0].price == 599.0


# ---------------------------------------------------------------------------
# Comparison-page merge
# ---------------------------------------------------------------------------


class TestMergeComparisonSellers:
    def test_merges_seller_rows_unique_names(self):
        """Rows with unique names (seller names) are merged into one product."""
        products = [
            ProductResult(
                name="Store A",
                model_id="hash_a",
                sellers=[Seller(name="example.com", price=1000, currency="ILS", url="https://example.com")],
            ),
            ProductResult(
                name="Store B",
                model_id="hash_b",
                sellers=[Seller(name="example.com", price=1100, currency="ILS", url="https://example.com")],
            ),
            ProductResult(
                name="Store C",
                model_id="hash_c",
                sellers=[Seller(name="example.com", price=950, currency="ILS", url="https://example.com")],
            ),
        ]

        result = _merge_comparison_sellers(products, "BFL523MB1F", "example.com")

        assert len(result) == 1
        assert result[0].name == "BFL523MB1F"
        assert len(result[0].sellers) == 3
        assert result[0].sellers[0].name == "Store A"
        assert result[0].sellers[1].name == "Store B"
        assert result[0].sellers[2].name == "Store C"

    def test_merges_seller_rows_same_name(self):
        """Rows with identical names (generic label) are merged."""
        products = [
            ProductResult(
                name="Product Page",
                model_id="h1",
                sellers=[Seller(name="store.com", price=500, currency="USD", url="https://store.com")],
            ),
            ProductResult(
                name="Product Page",
                model_id="h2",
                sellers=[Seller(name="store.com", price=520, currency="USD", url="https://store.com")],
            ),
        ]

        result = _merge_comparison_sellers(products, "XYZ123", "store.com")

        assert len(result) == 1
        assert len(result[0].sellers) == 2

    def test_no_merge_when_query_in_name(self):
        """Products whose name contains the query are real products, not seller rows."""
        products = [
            ProductResult(
                name="BFL523MB1F Microwave",
                model_id="m1",
                sellers=[Seller(name="store.com", price=500, currency="USD", url="https://store.com")],
            ),
            ProductResult(
                name="BFL523MB2F Microwave XL",
                model_id="m2",
                sellers=[Seller(name="store.com", price=600, currency="USD", url="https://store.com")],
            ),
        ]

        result = _merge_comparison_sellers(products, "BFL523MB1F", "store.com")

        # Should NOT merge — these are different products
        assert len(result) == 2

    def test_no_merge_single_product(self):
        """Single product should not be changed."""
        products = [
            ProductResult(
                name="Store A",
                model_id="h1",
                sellers=[Seller(name="store.com", price=100, currency="USD", url="https://store.com")],
            ),
        ]

        result = _merge_comparison_sellers(products, "ABC123", "store.com")
        assert len(result) == 1
        assert result[0].name == "Store A"  # Unchanged

    def test_no_merge_when_no_prices(self):
        """Products without prices should not be merged."""
        products = [
            ProductResult(
                name="Store A",
                model_id="h1",
                sellers=[Seller(name="store.com", price=None, currency="USD", url="https://store.com")],
            ),
            ProductResult(
                name="Store B",
                model_id="h2",
                sellers=[Seller(name="store.com", price=None, currency="USD", url="https://store.com")],
            ),
        ]

        result = _merge_comparison_sellers(products, "ABC123", "store.com")
        assert len(result) == 2

    def test_no_merge_mixed_names(self):
        """Products with some duplicate and some unique names: ambiguous, skip."""
        products = [
            ProductResult(
                name="Store A",
                model_id="h1",
                sellers=[Seller(name="s.com", price=100, currency="USD", url="https://s.com")],
            ),
            ProductResult(
                name="Store A",
                model_id="h2",
                sellers=[Seller(name="s.com", price=200, currency="USD", url="https://s.com")],
            ),
            ProductResult(
                name="Store B",
                model_id="h3",
                sellers=[Seller(name="s.com", price=300, currency="USD", url="https://s.com")],
            ),
        ]

        result = _merge_comparison_sellers(products, "ABC123", "s.com")
        # Mixed names (not all same, not all unique) -> no merge
        assert len(result) == 3


# ---------------------------------------------------------------------------
# SSRF protection
# ---------------------------------------------------------------------------

class TestIsSafeUrl:
    def test_rejects_localhost(self):
        assert _is_safe_url("http://localhost/secret") is False

    def test_rejects_127(self):
        assert _is_safe_url("http://127.0.0.1/admin") is False

    def test_rejects_private_10(self):
        assert _is_safe_url("http://10.0.0.1/internal") is False

    def test_rejects_private_192(self):
        assert _is_safe_url("http://192.168.1.1/") is False

    def test_rejects_metadata_169(self):
        assert _is_safe_url("http://169.254.169.254/latest/meta-data/") is False

    def test_accepts_public_url(self):
        assert _is_safe_url("https://www.example.com/products") is True

    def test_rejects_no_scheme(self):
        assert _is_safe_url("ftp://example.com") is False

    def test_rejects_empty(self):
        assert _is_safe_url("") is False


# ---------------------------------------------------------------------------
# HTTP pre-fetch
# ---------------------------------------------------------------------------

class TestHttpPrefetch:
    @pytest.mark.asyncio
    async def test_returns_none_for_unsafe_url(self):
        result = await _try_http_prefetch(
            "http://127.0.0.1/admin", "test", "127.0.0.1",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_both_http_clients_error(self):
        """When both httpx and curl_cffi fail, returns None."""
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_cf_resp = MagicMock()
        mock_cf_resp.status_code = 403
        mock_cf_session = AsyncMock()
        mock_cf_session.get.return_value = mock_cf_resp
        mock_cf_session.__aenter__ = AsyncMock(return_value=mock_cf_session)
        mock_cf_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.httpx.AsyncClient",
                  return_value=mock_client),
            patch("src.mcp_servers.web_scraper_mcp.scraper.CurlSession",
                  return_value=mock_cf_session),
        ):
            result = await _try_http_prefetch(
                "https://example.com/product", "test", "example.com",
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_curl_cffi_fallback_on_httpx_failure(self):
        """When httpx returns 403 but curl_cffi succeeds, uses curl_cffi response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        microdata_html = """<html><body>
        <div itemscope itemtype="https://schema.org/Product">
            <h1 itemprop="name">Bosch SMV4ECX28E</h1>
            <span itemprop="price" content="3999">₪3,999</span>
            <meta itemprop="priceCurrency" content="ILS"/>
        </div>
        </body></html>""" + " " * 1000  # pad to exceed min length

        mock_cf_resp = MagicMock()
        mock_cf_resp.status_code = 200
        mock_cf_resp.text = microdata_html
        mock_cf_session = AsyncMock()
        mock_cf_session.get.return_value = mock_cf_resp
        mock_cf_session.__aenter__ = AsyncMock(return_value=mock_cf_session)
        mock_cf_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.httpx.AsyncClient",
                  return_value=mock_client),
            patch("src.mcp_servers.web_scraper_mcp.scraper.CurlSession",
                  return_value=mock_cf_session),
        ):
            result = await _try_http_prefetch(
                "https://shop.example.com/product/123", "SMV4ECX28E", "shop.example.com",
            )
        assert result is not None
        assert len(result) == 1
        assert result[0].name == "Bosch SMV4ECX28E"

    @pytest.mark.asyncio
    async def test_returns_none_for_small_html(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>tiny</body></html>"
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.mcp_servers.web_scraper_mcp.scraper.httpx.AsyncClient",
                   return_value=mock_client):
            result = await _try_http_prefetch(
                "https://example.com/product", "test", "example.com",
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self):
        """When both httpx and curl_cffi throw network errors, returns None."""
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_cf_session = AsyncMock()
        mock_cf_session.get.side_effect = Exception("Connection refused")
        mock_cf_session.__aenter__ = AsyncMock(return_value=mock_cf_session)
        mock_cf_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.httpx.AsyncClient",
                  return_value=mock_client),
            patch("src.mcp_servers.web_scraper_mcp.scraper.CurlSession",
                  return_value=mock_cf_session),
        ):
            result = await _try_http_prefetch(
                "https://example.com/product", "test", "example.com",
            )
        assert result is None


# ---------------------------------------------------------------------------
# BeautifulSoup extraction helpers
# ---------------------------------------------------------------------------

class TestExtractPageProductName:
    def test_extracts_from_h1(self):
        from bs4 import BeautifulSoup
        html = "<html><body><h1>Bosch SMV4ECX28E Dishwasher</h1></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        assert _extract_page_product_name(soup) == "Bosch SMV4ECX28E Dishwasher"

    def test_extracts_from_og_title(self):
        from bs4 import BeautifulSoup
        html = '<html><head><meta property="og:title" content="Samsung Fridge"/></head></html>'
        soup = BeautifulSoup(html, "html.parser")
        assert _extract_page_product_name(soup) == "Samsung Fridge"

    def test_extracts_from_title_tag(self):
        from bs4 import BeautifulSoup
        html = "<html><head><title>Product X - Shop</title></head></html>"
        soup = BeautifulSoup(html, "html.parser")
        assert _extract_page_product_name(soup) == "Product X"

    def test_returns_empty_when_no_title(self):
        from bs4 import BeautifulSoup
        html = "<html><body><div>no heading</div></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        assert _extract_page_product_name(soup) == ""


class TestExtractFromDataAttrs:
    def test_extracts_sellers_from_comparison_page(self):
        from bs4 import BeautifulSoup
        html = """
        <html><head><title>Bosch BFL523MB1F</title></head><body>
        <h1>Bosch BFL523MB1F Microwave</h1>
        <div data-product-price="1988" data-site-name="Store A">
            <a href="https://store-a.com/buy">Buy</a>
        </div>
        <div data-product-price="1940" data-site-name="Store B">
            <a href="https://store-b.com/buy">Buy</a>
        </div>
        <div data-product-price="1941" data-site-name="Store C">
            <a href="/store-c">Buy</a>
        </div>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        with patch("src.mcp_servers.web_scraper_mcp.scraper.get_default_currency_for_domain",
                   return_value="ILS"):
            products = _extract_from_data_attrs(
                soup, "https://compare.example.com/product/123", "compare.example.com",
                "Bosch BFL523MB1F Microwave",
            )
        assert len(products) == 1
        p = products[0]
        assert p.name == "Bosch BFL523MB1F Microwave"
        assert p.model_id == "BFL523MB1F"
        assert len(p.sellers) == 3
        assert p.sellers[0].price == 1988
        assert p.sellers[0].name == "Store A"
        assert p.sellers[1].price == 1940

    def test_returns_empty_for_single_row(self):
        from bs4 import BeautifulSoup
        html = '<html><body><div data-product-price="100">one</div></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        assert _extract_from_data_attrs(soup, "https://x.com", "x.com", "Prod") == []

    def test_skips_invalid_prices(self):
        from bs4 import BeautifulSoup
        html = """
        <html><body>
        <div data-product-price="abc" data-site-name="A"></div>
        <div data-product-price="0" data-site-name="B"></div>
        <div data-product-price="500" data-site-name="C"></div>
        <div data-product-price="600" data-site-name="D"></div>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        with patch("src.mcp_servers.web_scraper_mcp.scraper.get_default_currency_for_domain",
                   return_value="USD"):
            products = _extract_from_data_attrs(soup, "https://x.com", "x.com", "Widget")
        # Only 500 and 600 are valid
        assert len(products) == 1
        assert len(products[0].sellers) == 2


class TestExtractJsonldFromSoup:
    def test_extracts_product_from_jsonld(self):
        from bs4 import BeautifulSoup
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@type": "Product",
            "name": "Samsung Galaxy S24",
            "mpn": "SM-S921B",
            "brand": {"@type": "Brand", "name": "Samsung"},
            "image": "https://img.example.com/s24.jpg",
            "offers": {"price": "3499", "priceCurrency": "ILS"}
        }
        </script>
        </head></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        result = _extract_jsonld_from_soup(soup, "https://shop.example.com/s24", "shop.example.com")
        assert result is not None
        assert result.name == "Samsung Galaxy S24"
        assert result.model_id == "SM-S921B"
        assert result.brand == "Samsung"
        assert result.sellers[0].price == 3499
        assert result.sellers[0].currency == "ILS"

    def test_returns_none_when_no_jsonld(self):
        from bs4 import BeautifulSoup
        html = "<html><body>No JSON-LD</body></html>"
        soup = BeautifulSoup(html, "html.parser")
        assert _extract_jsonld_from_soup(soup, "https://x.com", "x.com") is None

    def test_skips_non_product_jsonld(self):
        from bs4 import BeautifulSoup
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@type": "Organization", "name": "Acme Corp"}
        </script>
        </head></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        assert _extract_jsonld_from_soup(soup, "https://x.com", "x.com") is None


class TestExtractMicrodataFromSoup:
    def test_extracts_product_from_microdata(self):
        from bs4 import BeautifulSoup
        html = """
        <html><head>
        <meta property="og:image" content="https://img.example.com/prod.jpg"/>
        </head><body>
        <h1 itemprop="name">Bosch SMV4ECX28E Dishwasher</h1>
        <span itemprop="brand" content="Bosch">Bosch</span>
        <meta itemprop="price" content="4155"/>
        <meta itemprop="priceCurrency" content="ILS"/>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        result = _extract_microdata_from_soup(soup, "https://shop.example.com/p/123", "shop.example.com")
        assert result is not None
        assert result.name == "Bosch SMV4ECX28E Dishwasher"
        assert result.model_id == "SMV4ECX28E"
        assert result.brand == "Bosch"
        assert result.sellers[0].price == 4155
        assert result.sellers[0].currency == "ILS"
        assert result.image_url == "https://img.example.com/prod.jpg"

    def test_extracts_price_from_text_content(self):
        from bs4 import BeautifulSoup
        html = """
        <html><body>
        <h1 itemprop="name">Widget Pro 2000</h1>
        <span itemprop="price">₪2,990</span>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        with patch("src.mcp_servers.web_scraper_mcp.scraper.get_default_currency_for_domain",
                   return_value="ILS"):
            result = _extract_microdata_from_soup(soup, "https://x.com/p", "x.com")
        assert result is not None
        assert result.sellers[0].price == 2990

    def test_returns_none_without_price(self):
        from bs4 import BeautifulSoup
        html = '<html><body><h1 itemprop="name">Test</h1></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        assert _extract_microdata_from_soup(soup, "https://x.com", "x.com") is None

    def test_returns_none_for_zero_price(self):
        from bs4 import BeautifulSoup
        html = """
        <html><body>
        <h1 itemprop="name">Widget</h1>
        <meta itemprop="price" content="0"/>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        assert _extract_microdata_from_soup(soup, "https://x.com", "x.com") is None

    def test_falls_back_to_h1_for_name(self):
        from bs4 import BeautifulSoup
        html = """
        <html><body>
        <h1>Samsung RM70F63REB Fridge</h1>
        <meta itemprop="price" content="8500"/>
        <meta itemprop="priceCurrency" content="ILS"/>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        result = _extract_microdata_from_soup(soup, "https://x.com/p", "x.com")
        assert result is not None
        assert result.model_id == "RM70F63REB"


class TestExtractOgProductFromSoup:
    def test_extracts_product_from_og_meta(self):
        from bs4 import BeautifulSoup
        html = """
        <html><head>
        <meta property="og:type" content="product"/>
        <meta property="og:title" content="Bosch HBG578EB3 Built-in Oven"/>
        <meta property="og:image" content="https://img.example.com/oven.jpg"/>
        <meta property="product:price:amount" content="3128"/>
        <meta property="product:price:currency" content="ILS"/>
        </head></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        result = _extract_og_product_from_soup(soup, "https://shop.example.com/oven", "shop.example.com")
        assert result is not None
        assert result.name == "Bosch HBG578EB3 Built-in Oven"
        assert result.model_id == "HBG578EB3"
        assert result.sellers[0].price == 3128
        assert result.sellers[0].currency == "ILS"
        assert result.image_url == "https://img.example.com/oven.jpg"

    def test_returns_none_without_og_type_product(self):
        from bs4 import BeautifulSoup
        html = """
        <html><head>
        <meta property="og:type" content="website"/>
        <meta property="product:price:amount" content="100"/>
        </head></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        assert _extract_og_product_from_soup(soup, "https://x.com", "x.com") is None

    def test_returns_none_without_price(self):
        from bs4 import BeautifulSoup
        html = """
        <html><head>
        <meta property="og:type" content="product"/>
        <meta property="og:title" content="Test Product"/>
        </head></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        assert _extract_og_product_from_soup(soup, "https://x.com", "x.com") is None

    def test_uses_domain_currency_fallback(self):
        from bs4 import BeautifulSoup
        html = """
        <html><head>
        <meta property="og:type" content="product"/>
        <meta property="og:title" content="Bosch PVS631HC1E Cooktop"/>
        <meta property="product:price:amount" content="1982"/>
        </head></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        with patch("src.mcp_servers.web_scraper_mcp.scraper.get_default_currency_for_domain",
                   return_value="ILS"):
            result = _extract_og_product_from_soup(soup, "https://x.co.il/p", "x.co.il")
        assert result is not None
        assert result.sellers[0].currency == "ILS"
