"""Tests for web scraper module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_servers.web_scraper_mcp.scraper import (
    _find_next_page_url,
    _is_safe_url,
    _merge_comparison_sellers,
    extract_domain,
    extract_specs_from_text,
    parse_price,
    scrape_page,
)
from src.mcp_servers.web_scraper_mcp.extractors import (
    _extract_from_data_attrs,
    _extract_jsonld_from_soup,
    _extract_microdata_from_soup,
    _extract_og_product_from_soup,
    _extract_page_product_name,
    validate_results,
)
from src.mcp_servers.web_scraper_mcp.diagnostics import (
    ExtractionResult,
    FailureType,
    classify_http_failure,
    classify_playwright_failure,
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
        with patch("src.mcp_servers.web_scraper_mcp.extractors.soup_methods.get_default_currency_for_domain",
                   return_value="ILS"):
            products = _extract_from_data_attrs(
                soup, "https://compare.example.com/product/123", "compare.example.com",
                "Bosch BFL523MB1F Microwave",
            )
        # Each seller row returns a separate ProductResult (merging is done in _post_process)
        assert len(products) == 3
        assert products[0].name == "Bosch BFL523MB1F Microwave"
        assert products[0].sellers[0].price == 1988
        assert products[0].sellers[0].name == "Store A"
        assert products[1].sellers[0].price == 1940
        assert products[1].sellers[0].name == "Store B"
        assert products[2].sellers[0].price == 1941

    def test_extracts_single_row(self):
        from bs4 import BeautifulSoup
        html = '<html><body><div data-product-price="100">one</div></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        with patch("src.mcp_servers.web_scraper_mcp.extractors.soup_methods.get_default_currency_for_domain",
                   return_value="USD"):
            products = _extract_from_data_attrs(soup, "https://x.com", "x.com", "Prod")
        assert len(products) == 1
        assert products[0].sellers[0].price == 100

    def test_skips_unparseable_prices(self):
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
        with patch("src.mcp_servers.web_scraper_mcp.extractors.soup_methods.get_default_currency_for_domain",
                   return_value="USD"):
            products = _extract_from_data_attrs(soup, "https://x.com", "x.com", "Widget")
        # "abc" is unparseable, "0" parses to 0.0, 500 and 600 are valid
        assert len(products) == 3
        assert products[0].sellers[0].price == 0.0
        assert products[1].sellers[0].price == 500
        assert products[2].sellers[0].price == 600


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

    def test_extracts_price_from_text_content(self):
        from bs4 import BeautifulSoup
        html = """
        <html><body>
        <h1 itemprop="name">Widget Pro 2000</h1>
        <span itemprop="price">₪2,990</span>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        with patch("src.mcp_servers.web_scraper_mcp.extractors.soup_methods.get_default_currency_for_domain",
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
        with patch("src.mcp_servers.web_scraper_mcp.extractors.soup_methods.get_default_currency_for_domain",
                   return_value="ILS"):
            result = _extract_og_product_from_soup(soup, "https://x.co.il/p", "x.co.il")
        assert result is not None
        assert result.sellers[0].currency == "ILS"


# ---------------------------------------------------------------------------
# Diagnostics — failure classification
# ---------------------------------------------------------------------------


class TestClassifyHttpFailure:
    def test_network_error(self):
        assert classify_http_failure(None, "", Exception("timeout")) == FailureType.NAVIGATION_FAILED

    def test_no_status_code(self):
        assert classify_http_failure(None, "") == FailureType.NAVIGATION_FAILED

    def test_empty_page(self):
        assert classify_http_failure(200, "short") == FailureType.EMPTY_PAGE

    def test_spa_no_data(self):
        assert classify_http_failure(200, "x" * 2000) == FailureType.JS_SPA_NO_DATA

    def test_cloudflare_js_challenge(self):
        body = "Just a moment..." + "x" * 2000
        assert classify_http_failure(403, body) == FailureType.CLOUDFLARE_JS

    def test_cloudflare_captcha(self):
        body = "Attention Required" + "x" * 2000
        assert classify_http_failure(403, body) == FailureType.CLOUDFLARE_CAPTCHA

    def test_waf_403(self):
        assert classify_http_failure(403, "Forbidden" + "x" * 2000) == FailureType.WAF_BLOCKED

    def test_rate_limit_429(self):
        assert classify_http_failure(429, "") == FailureType.HTTP_BLOCKED

    def test_service_unavailable_503(self):
        assert classify_http_failure(503, "") == FailureType.HTTP_BLOCKED


class TestClassifyPlaywrightFailure:
    def test_navigation_error(self):
        assert classify_playwright_failure("", 0, Exception("timeout")) == FailureType.NAVIGATION_FAILED

    def test_cloudflare_js(self):
        assert classify_playwright_failure("Just a moment...", 5000) == FailureType.CLOUDFLARE_JS

    def test_cloudflare_captcha(self):
        assert classify_playwright_failure("Attention Required!", 5000) == FailureType.CLOUDFLARE_CAPTCHA

    def test_empty_page(self):
        assert classify_playwright_failure("Some Title", 500) == FailureType.EMPTY_PAGE

    def test_no_products(self):
        assert classify_playwright_failure("Shop Page", 50000) == FailureType.NO_PRODUCTS_FOUND


class TestExtractionResult:
    def test_success_with_products(self):
        r = ExtractionResult(
            products=[ProductResult(name="Test", model_id="T1", sellers=[])],
            access_method="httpx",
            extraction_method="jsonld",
        )
        assert r.success is True

    def test_failure_without_products(self):
        r = ExtractionResult(
            failure_type=FailureType.HTTP_BLOCKED,
            access_method="httpx",
        )
        assert r.success is False

    def test_failure_with_products_and_failure_type(self):
        r = ExtractionResult(
            products=[ProductResult(name="Test", model_id="T1", sellers=[])],
            failure_type=FailureType.LOW_QUALITY,
            access_method="httpx",
        )
        assert r.success is False


# ---------------------------------------------------------------------------
# Pipeline — scrape_page integration tests
# ---------------------------------------------------------------------------


class TestScrapePagePipeline:
    @pytest.mark.asyncio
    async def test_returns_empty_on_unsafe_url(self):
        mock_browser = AsyncMock()
        results = await scrape_page(mock_browser, "http://127.0.0.1/admin")
        assert results == []

    @pytest.mark.asyncio
    async def test_http_success_skips_playwright(self):
        """When httpx extracts products successfully, playwright is never called."""
        mock_browser = AsyncMock()
        product = ProductResult(
            name="Test Product",
            model_id="TP1",
            sellers=[Seller(name="shop.com", price=100, currency="USD", url="https://shop.com")],
        )
        http_result = ExtractionResult(
            products=[product],
            access_method="httpx",
            extraction_method="jsonld",
            domain="shop.example.com",
            page_type="search",
        )

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline.get_cached_strategy", return_value=None),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline._attempt_http_listing_then_product", return_value=http_result),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline._attempt_playwright") as mock_pw,
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline.validate_results", return_value=[product]),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline._cache_success"),
        ):
            results = await scrape_page(mock_browser, "https://shop.example.com/search?q=laptop")

        assert len(results) == 1
        assert results[0].name == "Test Product"
        mock_pw.assert_not_called()

    @pytest.mark.asyncio
    async def test_escalates_to_playwright_on_http_failure(self):
        """When HTTP methods fail, pipeline escalates to playwright."""
        mock_browser = AsyncMock()
        product = ProductResult(
            name="Browser Product",
            model_id="BP1",
            sellers=[Seller(name="shop.com", price=200, currency="USD", url="https://shop.com")],
        )
        http_fail = ExtractionResult(
            access_method="httpx",
            failure_type=FailureType.JS_SPA_NO_DATA,
            failure_detail="200 OK but no extractable products",
            domain="shop.example.com",
            page_type="search",
        )
        curl_fail = ExtractionResult(
            access_method="curl_cffi",
            failure_type=FailureType.JS_SPA_NO_DATA,
            failure_detail="200 OK but no extractable products",
            domain="shop.example.com",
            page_type="search",
        )
        pw_success = ExtractionResult(
            products=[product],
            access_method="playwright",
            extraction_method="css_strategy",
            domain="shop.example.com",
            page_type="search",
        )

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline.get_cached_strategy", return_value=None),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline._attempt_http_listing_then_product",
                  side_effect=[http_fail, curl_fail]),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline._attempt_playwright",
                  return_value=pw_success),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline.validate_results",
                  return_value=[product]),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline._cache_success"),
        ):
            results = await scrape_page(mock_browser, "https://shop.example.com/search?q=laptop")

        assert len(results) == 1
        assert results[0].name == "Browser Product"

    @pytest.mark.asyncio
    async def test_aborts_on_captcha(self):
        """CAPTCHA detection stops the pipeline immediately."""
        mock_browser = AsyncMock()
        captcha_result = ExtractionResult(
            access_method="httpx",
            failure_type=FailureType.CLOUDFLARE_CAPTCHA,
            failure_detail="CAPTCHA block",
            domain="shop.example.com",
            page_type="search",
        )

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline.get_cached_strategy", return_value=None),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline._attempt_http_listing_then_product",
                  return_value=captcha_result),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline._attempt_playwright") as mock_pw,
        ):
            results = await scrape_page(mock_browser, "https://shop.example.com/search?q=laptop")

        assert results == []
        # Playwright should NOT be called after CAPTCHA
        mock_pw.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_methods_fail_returns_empty(self):
        """When every method fails, returns empty list."""
        mock_browser = AsyncMock()
        http_fail = ExtractionResult(
            access_method="httpx",
            failure_type=FailureType.HTTP_BLOCKED,
            failure_detail="403",
            domain="shop.example.com",
            page_type="search",
        )
        curl_fail = ExtractionResult(
            access_method="curl_cffi",
            failure_type=FailureType.WAF_BLOCKED,
            failure_detail="403",
            domain="shop.example.com",
            page_type="search",
        )
        pw_fail = ExtractionResult(
            access_method="playwright",
            failure_type=FailureType.NO_PRODUCTS_FOUND,
            failure_detail="title='Shop', body=50000",
            domain="shop.example.com",
            page_type="search",
        )

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline.get_cached_strategy", return_value=None),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline._attempt_http_listing_then_product",
                  side_effect=[http_fail, curl_fail]),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline._attempt_playwright",
                  return_value=pw_fail),
        ):
            results = await scrape_page(mock_browser, "https://shop.example.com/search?q=laptop")

        assert results == []

    @pytest.mark.asyncio
    async def test_validation_failure_tries_next_method(self):
        """When products are extracted but fail validation, the pipeline continues."""
        mock_browser = AsyncMock()
        bad_product = ProductResult(
            name="Garbage",
            model_id="G1",
            sellers=[],
        )
        good_product = ProductResult(
            name="Real Product",
            model_id="RP1",
            sellers=[Seller(name="shop.com", price=100, currency="USD", url="https://shop.com")],
        )
        http_result = ExtractionResult(
            products=[bad_product],
            access_method="httpx",
            extraction_method="jsonld",
            domain="shop.example.com",
            page_type="search",
        )
        pw_result = ExtractionResult(
            products=[good_product],
            access_method="playwright",
            extraction_method="css_strategy",
            domain="shop.example.com",
            page_type="search",
        )

        validate_calls = []

        def mock_validate(products, query, domain):
            validate_calls.append(products)
            if products == [bad_product]:
                return []  # Validation fails
            return products  # Validation passes

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline.get_cached_strategy", return_value=None),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline._attempt_http_listing_then_product",
                  side_effect=[
                      http_result,
                      ExtractionResult(access_method="curl_cffi",
                                       failure_type=FailureType.HTTP_BLOCKED,
                                       failure_detail="403",
                                       domain="shop.example.com",
                                       page_type="search"),
                  ]),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline._attempt_playwright",
                  return_value=pw_result),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline.validate_results",
                  side_effect=mock_validate),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline._cache_success"),
        ):
            results = await scrape_page(mock_browser, "https://shop.example.com/search?q=laptop")

        assert len(results) == 1
        assert results[0].name == "Real Product"

    @pytest.mark.asyncio
    async def test_skips_blocked_domain(self):
        """Blocked domains are skipped immediately."""
        mock_browser = AsyncMock()

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline.is_domain_blocked", return_value=True),
        ):
            results = await scrape_page(mock_browser, "https://blocked.example.com/search?q=test")

        assert results == []

    @pytest.mark.asyncio
    async def test_records_failure_on_pipeline_exhaustion(self):
        """When all methods fail, update_failure is called."""
        mock_browser = AsyncMock()
        fail_result = ExtractionResult(
            access_method="playwright",
            failure_type=FailureType.NO_PRODUCTS_FOUND,
            failure_detail="no products",
            domain="shop.example.com",
            page_type="search",
        )

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline.is_domain_blocked", return_value=False),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline.get_cached_strategy", return_value=None),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline._attempt_http_listing_then_product",
                  return_value=ExtractionResult(
                      access_method="httpx",
                      failure_type=FailureType.HTTP_BLOCKED,
                      failure_detail="403",
                      domain="shop.example.com",
                      page_type="search",
                  )),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline._attempt_playwright",
                  return_value=fail_result),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline.update_failure") as mock_update_failure,
        ):
            results = await scrape_page(mock_browser, "https://shop.example.com/search?q=test")

        assert results == []
        mock_update_failure.assert_awaited_once_with(
            "shop.example.com", "no_products", "search",
        )

    @pytest.mark.asyncio
    async def test_marks_validation_failure(self):
        """When extraction succeeds but validation fails, mark_validation_failure is called."""
        mock_browser = AsyncMock()
        bad_product = ProductResult(
            name="X", model_id="", sellers=[],
        )
        http_result = ExtractionResult(
            products=[bad_product],
            access_method="httpx",
            extraction_method="jsonld",
            domain="shop.example.com",
            page_type="search",
        )
        # All methods return products that fail validation
        fail_http2 = ExtractionResult(
            access_method="curl_cffi",
            failure_type=FailureType.HTTP_BLOCKED,
            failure_detail="403",
            domain="shop.example.com",
            page_type="search",
        )
        fail_pw = ExtractionResult(
            access_method="playwright",
            failure_type=FailureType.NO_PRODUCTS_FOUND,
            failure_detail="nothing",
            domain="shop.example.com",
            page_type="search",
        )

        with (
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline.is_domain_blocked", return_value=False),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline.get_cached_strategy", return_value=None),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline._attempt_http_listing_then_product",
                  side_effect=[http_result, fail_http2]),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline._attempt_playwright",
                  return_value=fail_pw),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline.validate_results", return_value=[]),
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline.mark_validation_failure") as mock_val,
            patch("src.mcp_servers.web_scraper_mcp.scraper.pipeline.update_failure"),
        ):
            results = await scrape_page(mock_browser, "https://shop.example.com/search?q=test")

        assert results == []
        mock_val.assert_awaited_once()


# ---------------------------------------------------------------------------
# Enhanced validation
# ---------------------------------------------------------------------------


class TestValidateResults:
    def test_rejects_short_names(self):
        products = [
            ProductResult(name="AB", model_id="", sellers=[
                Seller(name="s.com", price=100, currency="USD", url="https://s.com"),
            ]),
        ]
        assert validate_results(products, "", "s.com") == []

    def test_rejects_negative_prices(self):
        products = [
            ProductResult(name="Good Product", model_id="GP1", sellers=[
                Seller(name="s.com", price=-50, currency="USD", url="https://s.com"),
            ]),
        ]
        assert validate_results(products, "", "s.com") == []

    def test_keeps_valid_products(self):
        products = [
            ProductResult(name="Samsung Galaxy S24", model_id="SM-S921B", sellers=[
                Seller(name="shop.com", price=3499, currency="ILS", url="https://shop.com"),
            ]),
        ]
        result = validate_results(products, "Galaxy S24", "shop.com")
        assert len(result) == 1

    def test_rejects_low_quality_batch(self):
        """Batch of products with same name and no prices is rejected."""
        products = [
            ProductResult(name="Menu Item", model_id="", sellers=[]),
            ProductResult(name="Menu Item", model_id="", sellers=[]),
            ProductResult(name="Menu Item", model_id="", sellers=[]),
        ]
        assert validate_results(products, "", "s.com") == []

    def test_keeps_zero_price_with_url(self):
        """Products with no price but a valid URL are kept."""
        products = [
            ProductResult(name="Product With URL", model_id="P1", sellers=[
                Seller(name="s.com", price=None, currency="USD", url="https://s.com/p/1"),
            ]),
        ]
        result = validate_results(products, "", "s.com")
        assert len(result) == 1

    def test_filters_garbage_names(self):
        """Products with garbage names are filtered out."""
        products = [
            ProductResult(name="Valid Product XYZ", model_id="XYZ", sellers=[
                Seller(name="s.com", price=100, currency="USD", url="https://s.com"),
            ]),
        ]
        with patch("src.mcp_servers.web_scraper_mcp.extractors.validation.get_garbage_names",
                   return_value={"Valid Product XYZ"}):
            result = validate_results(products, "", "s.com")
        assert len(result) == 0
