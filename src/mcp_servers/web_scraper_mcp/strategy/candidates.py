"""CSS selector candidate lists for strategy discovery."""

# CSS selector candidates tried in order for product containers
CONTAINER_CANDIDATES: list[str] = [
    "[data-product-id]",
    "[data-item-id]",
    ".product-card",
    ".product-item",
    ".product-tile",
    ".product-listing",
    ".product-box",
    ".search-result",
    ".s-result-item",
    ".product",
    "li[class*='product']",
    "div[class*='product']",
    "article[class*='product']",
    "div[class*='Product']",
    # Common e-commerce listing patterns
    "[class*='result-item']",
    "[class*='ResultItem']",
    "[class*='catalog-item']",
    "[class*='CatalogItem']",
    "[class*='grid-item']",
    "[class*='GridItem']",
    "[class*='listing-item']",
    "[class*='card'][class*='product']",
    # Generic -- last resort, can match non-product items
    "div[class*='item'][class*='product']",
    # Price-comparison / seller-listing patterns
    "[class*='compare-item']",
    "[class*='compare'][class*='row']",
    "[class*='bid-row']",
    "[class*='bid'][class*='item']",
    "[class*='offer-row']",
    "[class*='offer'][class*='item']",
    "[class*='seller-row']",
    "[class*='seller'][class*='item']",
    "[class*='store-row']",
    "[class*='store'][class*='item']",
    "[data-product-price]",
    "[data-site-name]",
]

NAME_CANDIDATES: list[str] = [
    "h2 a", "h3 a", "h2", "h3",
    "[class*='title'] a", "[class*='name'] a",
    "[class*='Title'] a", "[class*='Name'] a",
    "[class*='title']", "[class*='name']",
    "[class*='Title']", "[class*='Name']",
    "a[class*='product']",
    "a[class*='Product']",
    "a[class*='Model']",
    # Seller/store name patterns (price-comparison sites)
    "[class*='store-name']", "[class*='StoreName']",
    "[class*='seller-name']", "[class*='SellerName']",
    "[class*='shop-name']", "[class*='ShopName']",
    "[class*='merchant']", "[class*='Merchant']",
]

PRICE_CANDIDATES: list[str] = [
    "[class*='price']",
    "[class*='Price']",
    "[data-price]",
    "[data-min-price]",
    "span[class*='amount']",
    "[class*='cost']",
    "[class*='Cost']",
]

IMAGE_CANDIDATES: list[str] = [
    "img[src*='product']", "img[data-src]",
    "img[class*='product']", "img[class*='Product']",
    "img[loading]", "img",
]

URL_CANDIDATES: list[str] = [
    "a[href*='/product']", "a[href*='/dp/']",
    "a[href*='/item']", "a[href*='/p/']",
    "a[href*='/model']", "a[href*='pid=']",
    "a[href]",
]

SPEC_CONTAINER_CANDIDATES: list[str] = [
    "[class*='spec']",
    "[class*='Spec']",
    "[class*='attribute']",
    "[class*='Attribute']",
    "[class*='feature']",
    "[class*='Feature']",
    "[data-spec]",
    "[data-attribute]",
    "dl",
    "table[class*='spec']",
]

# Data attributes commonly used by price-comparison and e-commerce sites
DATA_ATTR_NAME_CANDIDATES: list[str] = [
    "data-site-name", "data-store-name", "data-seller-name",
    "data-shop-name", "data-merchant-name", "data-vendor-name",
]

DATA_ATTR_PRICE_CANDIDATES: list[str] = [
    "data-product-price", "data-price", "data-min-price",
    "data-sale-price", "data-final-price", "data-amount",
]

# Single-product page candidates
SINGLE_PRODUCT_NAME_CANDIDATES: list[str] = [
    "h1[class*='product']", "h1[class*='Product']",
    "h1[class*='title']", "h1[class*='Title']",
    "h1[class*='name']", "h1[class*='Name']",
    "[class*='product-title']", "[class*='product-name']",
    "[class*='productTitle']", "[class*='productName']",
    "[class*='ProductTitle']", "[class*='ProductName']",
    "[data-product-name]",
    "h1",
]

SINGLE_PRODUCT_PRICE_CANDIDATES: list[str] = [
    "[class*='product-price']", "[class*='productPrice']",
    "[class*='ProductPrice']", "[class*='product_price']",
    "[class*='price'][class*='current']",
    "[class*='sale-price']", "[class*='salePrice']",
    "[data-price]", "[data-product-price]",
    "[class*='price']", "[class*='Price']",
    "span[class*='amount']",
    "[class*='cost']", "[class*='Cost']",
]

SINGLE_PRODUCT_CONTAINER_CANDIDATES: list[str] = [
    "[class*='product-detail']", "[class*='productDetail']",
    "[class*='ProductDetail']", "[class*='product-info']",
    "[class*='productInfo']", "[class*='ProductInfo']",
    "[class*='product-page']", "[class*='productPage']",
    "[class*='product-summary']",
    "[class*='pdp-']",
    "[itemtype*='schema.org/Product']",
    "main", "article",
]

# CSS selectors tried in order to find product links on listing pages
PRODUCT_LINK_CANDIDATES: list[str] = [
    "a[href*='/product/']",
    "a[href*='/dp/']",
    "a[href*='/item/']",
    "a[href*='/p/']",
    "a[href*='/model/']",
    "a[href*='pid=']",
    "a[href*='productid=']",
    ".product-card a",
    ".product-item a",
    "[class*='product'] a[href]",
    "[class*='Product'] a[href]",
    "[data-product-id] a[href]",
    "h2 a[href]",
    "h3 a[href]",
    "[class*='title'] a[href]",
    "[class*='name'] a[href]",
]
