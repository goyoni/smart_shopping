# Architecture Guidelines

This document defines high-level architectural principles for the Smart Shopping Agent. All implementation must follow these guidelines. The `/project:architect` review command validates compliance.

## 1. Multi-Market Extensibility

The product serves multiple markets (Israel, US, future expansions). Every market-specific concern must be isolated and extensible.

### Rule: No Inline Market-Specific Logic

Market-varying behavior (currencies, languages, web sources, formatting, regional defaults) must **never** be handled with inline conditionals scattered through the code.

**Bad** - Inline branching:
```python
# Hardcoded conditionals
if country == "IL":
    currency_symbol = "₪"
    sites = ["zap.co.il", "ksp.co.il"]
elif country == "US":
    currency_symbol = "$"
    sites = ["amazon.com", "bestbuy.com"]
```

**Good** - Isolated per-market modules with a central resolver:
```python
# config/markets/il.py
MARKET_CONFIG = {
    "currency": {"code": "ILS", "symbol": "₪", "position": "prefix"},
    "language": "he",
    "direction": "rtl",
}

# config/markets/us.py
MARKET_CONFIG = {
    "currency": {"code": "USD", "symbol": "$", "position": "prefix"},
    "language": "en",
    "direction": "ltr",
}

# config/markets/__init__.py
def get_market_config(market_code: str) -> dict:
    """Load market configuration by code."""
    ...
```

### Rule: Configuration Over Code

Market-specific data belongs in configuration files or database records, not in application logic.

**Bad** - Large constants embedded in source:
```python
SITES_BY_COUNTRY = {
    "IL": ["zap.co.il", "ksp.co.il", "ivory.co.il", ...],
    "US": ["amazon.com", "bestbuy.com", "walmart.com", ...],
}
```

**Good** - Per-market config files:
```
config/markets/il.yaml   # or .py, .json
config/markets/us.yaml
```

### Rule: Market-Agnostic Core Logic

Core business logic (search orchestration, result processing, caching, scraping strategies) must be market-agnostic. Market-specific behavior is injected via configuration or strategy objects, not baked into the core.

**Bad**:
```python
def format_price(price, country):
    if country == "IL":
        return f"₪{price:,.2f}"
    else:
        return f"${price:,.2f}"
```

**Good**:
```python
def format_price(price: float, currency_config: CurrencyConfig) -> str:
    return currency_config.format(price)
```

## 2. Modular Boundaries

### Rule: MCP Servers Are Independent

Each MCP server must be independently deployable and testable. No MCP server should import from another MCP server's internals.

### Rule: Shared Code Goes in `/src/shared`

Cross-cutting concerns (models, utilities, types) belong in `/src/shared`, not duplicated across modules.

## 3. Data-Driven Over Code-Driven

### Rule: Prefer Database/Config for Domain Data

Product categories, criteria definitions, scraping strategies, and similar domain data should live in the database or config files. Code should define *how* to process data, not *what* the data is.

**Bad**:
```python
PRODUCT_CRITERIA = {
    "refrigerator": ["noise_level", "energy_rating", "capacity"],
    "microwave": ["wattage", "capacity", "type"],
}
```

**Good**: Criteria stored in database, discovered and cached by the Product Criteria MCP.

## 4. Strategy Pattern for Variation

### Rule: Use Strategy/Plugin Patterns for Behavioral Differences

When behavior varies by market, product type, or site, use the strategy pattern: define an interface, implement per-variant, and select at runtime.

**Bad**:
```python
def search(query, market):
    if market == "IL":
        # 50 lines of IL-specific search logic
    elif market == "US":
        # 50 lines of US-specific search logic
```

**Good**:
```python
class SearchStrategy(Protocol):
    def execute(self, query: str) -> list[Result]: ...

class ILSearchStrategy:
    def execute(self, query: str) -> list[Result]: ...

class USSearchStrategy:
    def execute(self, query: str) -> list[Result]: ...

def get_strategy(market: str) -> SearchStrategy:
    ...
```

## Summary Checklist

When reviewing or implementing code, verify:

- [ ] No `if country/market/currency == "X"` conditionals in core logic
- [ ] Market-specific data lives in config files or database, not inline constants
- [ ] Core business logic is market-agnostic
- [ ] Behavioral variations use strategy/plugin patterns
- [ ] MCP servers don't cross-import
- [ ] Shared code is in `/src/shared`
- [ ] Domain data is data-driven (DB/config), not code-driven
