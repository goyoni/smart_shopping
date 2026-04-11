"""Site search strategy discovery, caching, and execution.

Discovers how to search for products on a specific e-commerce site
(URL template or search input selector), caches the strategy per domain,
and provides a function to execute in-site searches during the
fill-missing phase.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import litellm
from playwright.async_api import Page
from sqlalchemy import select

from src.backend.db.engine import async_session
from src.backend.db.models import SiteSearchStrategy
from src.shared.config import settings
from src.shared.logging import get_logger, get_tracer, operation_span, set_span_token_counts

logger = get_logger(__name__)
_tracer = get_tracer(__name__)

_CACHE_TTL_DAYS = 30


@dataclass
class SiteSearchConfig:
    """Serializable site search strategy."""

    strategy_type: str  # "url_template" or "search_input"
    search_url_template: str = ""
    search_input_selector: str = ""
    submit_selector: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SiteSearchConfig:
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


# ------------------------------------------------------------------
# DB cache
# ------------------------------------------------------------------

async def get_cached_site_search(domain: str) -> SiteSearchConfig | None:
    """Load cached site search strategy for a domain."""
    async with async_session() as session:
        stmt = select(SiteSearchStrategy).where(
            SiteSearchStrategy.domain == domain,
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

        # Invalidate strategies that keep failing
        if (record.fail_count or 0) >= 3:
            logger.info(
                "Invalidating cached site search for %s: %d consecutive failures",
                domain, record.fail_count,
            )
            await session.delete(record)
            await session.commit()
            return None

        return SiteSearchConfig(
            strategy_type=record.strategy_type,
            search_url_template=record.search_url_template,
            search_input_selector=record.search_input_selector,
            submit_selector=record.submit_selector,
        )


async def save_site_search(domain: str, config: SiteSearchConfig) -> None:
    """Save or update a site search strategy."""
    # Normalize: if domain has no www. prefix but the discovered template
    # does (due to redirect), strip it so the template matches the domain.
    if not domain.startswith("www.") and config.search_url_template:
        config.search_url_template = config.search_url_template.replace(
            f"://www.{domain}", f"://{domain}"
        )
    async with async_session() as session:
        stmt = select(SiteSearchStrategy).where(
            SiteSearchStrategy.domain == domain,
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()

        if record:
            record.strategy_type = config.strategy_type
            record.search_url_template = config.search_url_template
            record.search_input_selector = config.search_input_selector
            record.submit_selector = config.submit_selector
            record.updated_at = datetime.now(timezone.utc)
        else:
            record = SiteSearchStrategy(
                domain=domain,
                strategy_type=config.strategy_type,
                search_url_template=config.search_url_template,
                search_input_selector=config.search_input_selector,
                submit_selector=config.submit_selector,
            )
            session.add(record)

        await session.commit()
        logger.info("Saved site search strategy for %s: %s", domain, config.strategy_type)


async def record_site_search_failure(domain: str) -> None:
    """Increment failure count for a domain's cached strategy."""
    async with async_session() as session:
        stmt = select(SiteSearchStrategy).where(
            SiteSearchStrategy.domain == domain,
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()
        if record:
            record.fail_count = (record.fail_count or 0) + 1
            record.updated_at = datetime.now(timezone.utc)
            await session.commit()
            logger.debug("Site search fail_count for %s: %d", domain, record.fail_count)


async def reset_site_search_failures(domain: str) -> None:
    """Reset failure count on successful search."""
    async with async_session() as session:
        stmt = select(SiteSearchStrategy).where(
            SiteSearchStrategy.domain == domain,
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()
        if record and (record.fail_count or 0) > 0:
            record.fail_count = 0
            record.updated_at = datetime.now(timezone.utc)
            await session.commit()


async def invalidate_site_search(domain: str) -> None:
    """Delete cached strategy for a domain, forcing re-discovery."""
    async with async_session() as session:
        stmt = select(SiteSearchStrategy).where(
            SiteSearchStrategy.domain == domain,
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()
        if record:
            await session.delete(record)
            await session.commit()
            logger.info("Invalidated site search strategy for %s", domain)


# ------------------------------------------------------------------
# LLM-based discovery
# ------------------------------------------------------------------

_SEARCH_DISCOVERY_JS = """() => {
    // Find search forms and inputs
    const forms = document.querySelectorAll('form');
    const searchForms = [];
    for (const form of forms) {
        const action = form.getAttribute('action') || '';
        const method = (form.getAttribute('method') || 'get').toLowerCase();
        const inputs = form.querySelectorAll('input[type="text"], input[type="search"], input:not([type])');
        for (const input of inputs) {
            const name = input.getAttribute('name') || '';
            const placeholder = input.getAttribute('placeholder') || '';
            const id = input.getAttribute('id') || '';
            const cls = input.getAttribute('class') || '';
            const ariaLabel = input.getAttribute('aria-label') || '';
            searchForms.push({
                formAction: action,
                formMethod: method,
                inputName: name,
                inputId: id,
                inputClass: cls.substring(0, 100),
                placeholder: placeholder.substring(0, 80),
                ariaLabel: ariaLabel.substring(0, 80),
            });
        }
    }

    // Also find standalone search inputs not in forms
    const standaloneInputs = document.querySelectorAll(
        'input[type="search"], input[placeholder*="search" i], input[placeholder*="חיפוש" i], ' +
        'input[placeholder*="αναζήτηση" i], input[placeholder*="بحث" i], ' +
        'input[aria-label*="search" i], input[aria-label*="חיפוש" i], ' +
        'input[name="q"], input[name="s"], input[name="query"], input[name="search"], ' +
        'input[name="keyword"], input[name="keywords"], input[name="term"]'
    );
    const standalone = [];
    for (const input of standaloneInputs) {
        // Check if already captured via a form
        const inForm = input.closest('form');
        standalone.push({
            name: input.getAttribute('name') || '',
            id: input.getAttribute('id') || '',
            cls: (input.getAttribute('class') || '').substring(0, 100),
            placeholder: (input.getAttribute('placeholder') || '').substring(0, 80),
            type: input.getAttribute('type') || '',
            hasParentForm: !!inForm,
            parentFormAction: inForm ? (inForm.getAttribute('action') || '') : '',
        });
    }

    return JSON.stringify({ searchForms, standalone, url: window.location.href });
}"""

_SEARCH_LLM_PROMPT = """\
You are a web scraping expert. Given the search form analysis from an e-commerce website, \
determine the best way to search for products on this site.

Given:
- The site domain
- Search forms found on the page (action URLs, input names, placeholders)
- Standalone search inputs

Determine ONE of these strategies:
1. **url_template**: A search URL pattern where {query} is the search term placeholder.
   Example: "https://example.com/search?q={query}" or "https://example.com/catalogsearch/result/?q={query}"
2. **search_input**: A CSS selector for the search input field (fallback when no search URL can be determined).

Return ONLY a valid JSON object (no markdown, no explanation):
{"strategy_type": "url_template", "search_url_template": "https://...", "search_input_selector": "", "submit_selector": ""}

OR:
{"strategy_type": "search_input", "search_url_template": "", "search_input_selector": "input#search", "submit_selector": "button.search-btn"}

CRITICAL RULES:
- The URL template must be a SEARCH ENDPOINT, not a product page or category page.
- Build the URL from the form action + input name: if action="/search" and input name="q", \
the template is "https://domain/search?q={query}".
- If no form has a clear search action, use "search_input" strategy instead.
- DO NOT use the current page URL as the template base. The current page is a product/category page, NOT a search page.
- DO NOT create URLs like "https://domain/{query}" or "https://domain/brand/x/?{query}" — \
these are NOT valid search URLs.
- The URL template MUST have a query parameter (e.g. ?q={query} or ?search={query}), \
NOT a path segment.
- If the form action is empty or "#", use "search_input" strategy.
- Prefer url_template over search_input when a valid search form action exists."""


async def discover_site_search(
    page: Page,
    domain: str,
) -> SiteSearchConfig | None:
    """Analyze a page to discover how to search the site.

    Called during Playwright scraping. Uses JS to find search forms,
    then LLM to determine the best search strategy.
    """
    # Check cache first
    cached = await get_cached_site_search(domain)
    if cached:
        return cached

    model = settings.scraper_llm_model or settings.llm_model
    is_local = model.startswith("ollama/")
    if not settings.llm_api_key and not is_local:
        return None

    with operation_span(
        _tracer, "discover_site_search",
        input=domain,
    ) as span:
        # Step 1: Extract search form info from the page
        try:
            search_info_raw = await page.evaluate(_SEARCH_DISCOVERY_JS)
            search_info = json.loads(search_info_raw) if isinstance(search_info_raw, str) else search_info_raw
        except Exception:
            logger.debug("Failed to extract search form info from %s", domain)
            span.set_attribute("summary", "JS extraction failed")
            return None

        forms = search_info.get("searchForms", [])
        standalone = search_info.get("standalone", [])
        page_url = search_info.get("url", "")

        if not forms and not standalone:
            # No forms found (page may be empty/broken — e.g. 502 via proxy).
            # Try LLM to infer a search URL from just the domain name.
            config = await _infer_search_url_from_domain(domain, span)
            if config:
                await save_site_search(domain, config)
            return config

        span.set_attribute("form_count", len(forms))
        span.set_attribute("standalone_count", len(standalone))

        # Step 2: Try heuristic first (avoid LLM call if obvious)
        config = _try_heuristic(domain, page_url, forms, standalone)
        if config:
            span.set_attribute("summary", f"Heuristic: {config.strategy_type} -> {config.search_url_template or config.search_input_selector}")
            await save_site_search(domain, config)
            return config

        # Step 3: Use LLM to determine strategy
        user_prompt = (
            f"Site: {domain}\nPage URL: {page_url}\n\n"
            f"Search forms found:\n{json.dumps(forms, indent=2, ensure_ascii=False)}\n\n"
            f"Standalone search inputs:\n{json.dumps(standalone, indent=2, ensure_ascii=False)}"
        )
        span.set_attribute("llm.user_prompt", user_prompt[:2000])

        try:
            llm_kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": _SEARCH_LLM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.0,
            }
            if settings.llm_api_key:
                llm_kwargs["api_key"] = settings.llm_api_key

            response = await litellm.acompletion(**llm_kwargs)
            raw = (response.choices[0].message.content or "").strip()
            usage = response.get("usage") or {}
            set_span_token_counts(
                span,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            )
        except Exception as exc:
            logger.warning("LLM site search discovery failed for %s", domain, exc_info=True)
            span.set_attribute("summary", f"LLM call failed: {exc}")
            return None

        span.set_attribute("llm.raw_response", raw[:500])

        # Parse LLM response
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("LLM site search: invalid JSON from %s: %s", domain, raw[:200])
            span.set_attribute("summary", f"Invalid JSON: {raw[:200]}")
            return None

        strategy_type = parsed.get("strategy_type", "")
        if strategy_type not in ("url_template", "search_input"):
            span.set_attribute("summary", f"Invalid strategy_type: {strategy_type}")
            return None

        config = SiteSearchConfig(
            strategy_type=strategy_type,
            search_url_template=parsed.get("search_url_template", ""),
            search_input_selector=parsed.get("search_input_selector", ""),
            submit_selector=parsed.get("submit_selector", ""),
        )

        if strategy_type == "url_template":
            if not _validate_url_template(config.search_url_template, domain):
                reason = _explain_template_rejection(config.search_url_template, domain)
                span.set_attribute("summary", f"LLM URL template rejected: {reason}")
                logger.info(
                    "Rejected LLM site search template for %s: %s (%s)",
                    domain, config.search_url_template, reason,
                )
                # Fall back to search_input if we have one
                if config.search_input_selector:
                    config = SiteSearchConfig(
                        strategy_type="search_input",
                        search_input_selector=config.search_input_selector,
                        submit_selector=config.submit_selector,
                    )
                else:
                    return None

        span.set_attribute("summary",
            f"LLM: {config.strategy_type} -> {config.search_url_template or config.search_input_selector}")

        await save_site_search(domain, config)
        return config


_INFER_SEARCH_URL_PROMPT = """\
You are a web scraping expert. Given ONLY a domain name, infer the most likely \
search URL pattern for this e-commerce site.

Common patterns by platform:
- Generic: https://domain.com/search?q={query}
- WooCommerce/WordPress: https://domain.com/?s={query}
- Shopify: https://domain.com/search?q={query}
- Magento: https://domain.com/catalogsearch/result/?q={query}
- ASP.NET sites (.asp): https://domain.com/items.asp?search={query}
- Custom: varies widely

Return ONLY a valid JSON object (no markdown, no explanation):
{"strategy_type": "url_template", "search_url_template": "https://...{query}..."}

If you truly cannot guess (domain is too generic or unknown), return:
{"strategy_type": "none"}

RULES:
- The URL MUST contain {query} as a query parameter value (after =)
- Use https:// with www. prefix if the domain already has it
- Prefer /search?q={query} when uncertain"""


async def _infer_search_url_from_domain(
    domain: str,
    span: object,
) -> SiteSearchConfig | None:
    """Use LLM to guess the search URL from just the domain name.

    Called when the page returned empty content (e.g. 502 via proxy)
    so no forms could be extracted.
    """
    model = settings.scraper_llm_model or settings.llm_model
    is_local = model.startswith("ollama/")
    if not settings.llm_api_key and not is_local:
        return None

    try:
        llm_kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": _INFER_SEARCH_URL_PROMPT},
                {"role": "user", "content": f"Domain: {domain}"},
            ],
            "temperature": 0.0,
        }
        if settings.llm_api_key:
            llm_kwargs["api_key"] = settings.llm_api_key

        response = await litellm.acompletion(**llm_kwargs)
        raw = (response.choices[0].message.content or "").strip()
        usage = response.get("usage") or {}
        set_span_token_counts(
            span,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )
    except Exception as exc:
        logger.warning("LLM domain inference failed for %s: %s", domain, exc)
        span.set_attribute("summary", f"LLM domain inference failed: {exc}")
        return None

    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    span.set_attribute("llm.domain_inference_raw", raw[:500])

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        span.set_attribute("summary", f"LLM domain inference: invalid JSON: {raw[:200]}")
        return None

    if parsed.get("strategy_type") == "none":
        span.set_attribute("summary", "LLM could not infer search URL for domain")
        return None

    template = parsed.get("search_url_template", "")
    if not template or not _validate_url_template(template, domain):
        reason = _explain_template_rejection(template, domain) if template else "empty template"
        span.set_attribute("summary", f"LLM domain inference rejected: {reason}")
        return None

    config = SiteSearchConfig(
        strategy_type="url_template",
        search_url_template=template,
    )
    span.set_attribute("summary",
        f"LLM domain inference: {config.search_url_template}")
    logger.info("Inferred search URL for %s from domain name: %s", domain, template)
    return config


def _try_heuristic(
    domain: str,
    page_url: str,
    forms: list[dict],
    standalone: list[dict],
) -> SiteSearchConfig | None:
    """Try to determine search strategy without LLM.

    Covers common cases:
    - <form action="/search"> with <input name="q">
    - Forms with search-related input names (q, s, query, search, keyword)
    - Standalone search inputs with known param names
    - WooCommerce (/?s=), Shopify (/search?q=), Magento (/catalogsearch/result/?q=)
    """
    from urllib.parse import urlparse

    parsed_url = urlparse(page_url)
    origin = f"{parsed_url.scheme}://{parsed_url.netloc}"

    _SEARCH_ACTIONS = (
        "search", "find", "query", "catalogsearch", "suche",
        "buscar", "chercher", "result",
    )
    _SEARCH_INPUT_NAMES = {"q", "s", "query", "search", "keyword", "keywords", "term", "text"}

    # --- Strategy 1: Forms with search-related action ---
    for form in forms:
        action = form.get("formAction", "")
        input_name = form.get("inputName", "")
        if not input_name:
            continue

        action_lower = (action or "").lower()

        # Skip forms with no action or empty/anchor action
        if not action or action in ("#", "javascript:void(0)"):
            # But if input name is a known search param, try building
            # a URL from origin + common search paths
            if input_name.lower() in _SEARCH_INPUT_NAMES:
                # Try common search paths: /search, /, /catalogsearch/result/
                template = f"{origin}/search?{input_name}={{query}}"
                return SiteSearchConfig(
                    strategy_type="url_template",
                    search_url_template=template,
                )
            continue

        # Check if action contains search keywords
        has_search_action = any(kw in action_lower for kw in _SEARCH_ACTIONS)
        has_search_input = input_name.lower() in _SEARCH_INPUT_NAMES

        if not has_search_action and not has_search_input:
            continue

        # Build full URL template
        if action.startswith("http"):
            base = action
        elif action.startswith("/"):
            base = f"{origin}{action}"
        else:
            base = f"{origin}/{action}"

        # Strip trailing slash, append query parameter
        base = base.rstrip("/")

        # If the action already has query params, use & instead of ?
        separator = "&" if "?" in base else "?"
        template = f"{base}{separator}{input_name}={{query}}"

        if _validate_url_template(template, domain):
            return SiteSearchConfig(
                strategy_type="url_template",
                search_url_template=template,
            )

    # --- Strategy 2: Standalone search inputs with known param names ---
    for inp in standalone:
        name = inp.get("name", "")
        parent_action = inp.get("parentFormAction", "")

        if not name or name.lower() not in _SEARCH_INPUT_NAMES:
            continue

        # If it has a parent form with a valid action, use that
        if parent_action and parent_action not in ("#", "javascript:void(0)"):
            if parent_action.startswith("http"):
                base = parent_action
            elif parent_action.startswith("/"):
                base = f"{origin}{parent_action}"
            else:
                base = f"{origin}/{parent_action}"
            base = base.rstrip("/")
            separator = "&" if "?" in base else "?"
            template = f"{base}{separator}{name}={{query}}"
        else:
            # No form action — assume /search endpoint
            template = f"{origin}/search?{name}={{query}}"

        if _validate_url_template(template, domain):
            return SiteSearchConfig(
                strategy_type="url_template",
                search_url_template=template,
            )

    return None


def _validate_url_template(template: str, domain: str) -> bool:
    """Validate that a URL template looks like a legitimate search URL.

    Rejects templates that are obviously product pages, category pages,
    or malformed URLs.
    """
    from urllib.parse import urlparse

    if "{query}" not in template:
        return False

    parsed = urlparse(template.replace("{query}", "TEST"))
    path = parsed.path.lower()

    # Must be on the same domain
    host = (parsed.hostname or "").removeprefix("www.")
    if domain.removeprefix("www.") not in host and host not in domain.removeprefix("www."):
        return False

    # Reject product-specific paths
    _PRODUCT_INDICATORS = (
        "/item/", "/product/", "/prod/", "/p/", "/mob/item/",
        "/model.", "/model/",
    )
    for indicator in _PRODUCT_INDICATORS:
        if indicator in path:
            return False

    # Reject contact/feedback/support pages misidentified as search
    _NON_SEARCH_INDICATORS = (
        "/contact", "/feedback", "/support", "/helpdesk",
        "service=contact", "service=feedback",
    )
    template_lower = template.lower()
    for indicator in _NON_SEARCH_INDICATORS:
        if indicator in template_lower:
            return False

    # Reject paths with numeric IDs (likely product pages)
    # e.g. /web/item/322977 or /p/56462228
    if re.search(r"/\d{4,}", path):
        return False

    # Must have a query parameter, not just /{query} in the path
    if "{query}" in template.split("?")[0] and "?" not in template:
        return False

    # {query} must be a parameter VALUE (after =), not a bare query string
    # e.g. "?q={query}" is valid, "?{query}" is not
    if "?" in template:
        query_string = template.split("?", 1)[1]
        if "{query}" in query_string and "={query}" not in query_string:
            return False

    return True


def _explain_template_rejection(template: str, domain: str) -> str:
    """Return a human-readable reason why a template was rejected."""
    if "{query}" not in template:
        return "missing {query} placeholder"
    if "{query}" in template.split("?")[0] and "?" not in template:
        return "query in path, not as URL parameter"
    if "?" in template:
        qs = template.split("?", 1)[1]
        if "{query}" in qs and "={query}" not in qs:
            return "query is bare in query string, not a parameter value (missing =)"
    if re.search(r"/\d{4,}", template.split("?")[0].lower()):
        return "path contains numeric ID (likely product page)"
    for ind in ("/item/", "/product/", "/prod/", "/p/", "/mob/item/", "/model."):
        if ind in template.lower():
            return f"path contains product indicator '{ind}'"
    for ind in ("/contact", "/feedback", "/support", "/helpdesk", "service=contact", "service=feedback"):
        if ind in template.lower():
            return f"non-search page indicator '{ind}'"
    return "failed validation"


# ------------------------------------------------------------------
# Discover via homepage (for domains scraped via HTTP, not Playwright)
# ------------------------------------------------------------------

async def discover_site_search_via_homepage(
    browser: object,
    domain: str,
    locale: str = "en-US",
    market: str = "us",
) -> SiteSearchConfig | None:
    """Visit a domain's homepage via Playwright to discover search strategy.

    Used in the fill-missing phase for domains that were scraped via
    httpx/curl_cffi (so never had a Playwright page to analyze).
    """
    cached = await get_cached_site_search(domain)
    if cached:
        return cached

    from src.shared.browser import dismiss_consent, get_page

    with operation_span(
        _tracer, f"discover_site_search_homepage:{domain}",
        input=domain,
    ) as span:
        try:
            async with get_page(browser, locale=locale, market=market) as page:
                await page.goto(
                    f"https://{domain}",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )
                await dismiss_consent(page, domain)

                config = await discover_site_search(page, domain)
                if config:
                    span.set_attribute("summary",
                        f"Discovered: {config.strategy_type} -> {config.search_url_template or config.search_input_selector}")
                else:
                    span.set_attribute("summary", "No search strategy found")
                return config
        except Exception as exc:
            span.set_attribute("summary", f"Homepage visit failed: {exc}")
            logger.debug("Failed to visit homepage of %s: %s", domain, exc)
            return None


# ------------------------------------------------------------------
# Execute in-site search
# ------------------------------------------------------------------

async def search_within_site(
    browser: object,
    domain: str,
    model_id: str,
    locale: str = "en-US",
    market: str = "us",
) -> list:
    """Search for a model ID within a site using the cached strategy.

    Returns a list of ProductResult objects found on the site.
    """
    from urllib.parse import quote_plus

    from src.shared.browser import dismiss_consent, get_page

    config = await get_cached_site_search(domain)
    if not config:
        return []

    with operation_span(
        _tracer, f"search_within_site:{domain}",
        input=json.dumps({"domain": domain, "model_id": model_id}),
    ) as span:
        span.set_attribute("strategy_type", config.strategy_type)

        if config.strategy_type == "url_template":
            search_url = config.search_url_template.replace(
                "{query}", quote_plus(model_id),
            )
            span.set_attribute("search_url", search_url)

            # Use the scraping pipeline to extract product links from the search results page
            from src.mcp_servers.web_scraper_mcp.scraper import scrape_page

            products = await scrape_page(
                browser, search_url, model_id,
                locale=locale, market=market,
                page_type_hint="search",
            )

            # Filter to products matching the queried model, with a
            # price, and tag them with the queried model_id.
            mid_lower = model_id.lower()
            products = [
                p for p in products
                if (mid_lower in p.name.lower()
                    or (p.model_id and mid_lower in p.model_id.lower()))
                and any(s.price is not None for s in p.sellers)
            ]
            for p in products:
                p.product_type = model_id

            span.set_attribute("product_count", len(products))
            span.set_attribute("summary",
                f"URL template search on {domain} for '{model_id}': {len(products)} products")

            if products:
                await reset_site_search_failures(domain)
            else:
                await record_site_search_failure(domain)

            return products

        elif config.strategy_type == "search_input":
            # Fallback: use Playwright to type into search box
            async with get_page(browser, locale=locale, market=market) as page:
                try:
                    # Navigate to the site homepage
                    await page.goto(
                        f"https://{domain}",
                        wait_until="domcontentloaded",
                        timeout=15000,
                    )
                    await dismiss_consent(page, domain)

                    # Find and fill the search input
                    search_input = await page.query_selector(config.search_input_selector)
                    if not search_input:
                        span.set_attribute("summary",
                            f"Search input '{config.search_input_selector}' not found — invalidating")
                        await invalidate_site_search(domain)
                        return []

                    await search_input.fill(model_id)

                    # Submit: press Enter or click submit button
                    if config.submit_selector:
                        submit_btn = await page.query_selector(config.submit_selector)
                        if submit_btn:
                            await submit_btn.click()
                        else:
                            await search_input.press("Enter")
                    else:
                        await search_input.press("Enter")

                    # Wait for navigation
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass

                    # Now scrape the search results page
                    search_url = page.url
                    span.set_attribute("search_url", search_url)
                except Exception as exc:
                    span.set_attribute("summary", f"Search input interaction failed: {exc}")
                    return []

            # Scrape the resulting search URL
            from src.mcp_servers.web_scraper_mcp.scraper import scrape_page

            products = await scrape_page(
                browser, search_url, model_id,
                locale=locale, market=market,
                page_type_hint="search",
            )

            # Filter to products matching the queried model, with a
            # price, and tag them with the queried model_id.
            mid_lower = model_id.lower()
            products = [
                p for p in products
                if (mid_lower in p.name.lower()
                    or (p.model_id and mid_lower in p.model_id.lower()))
                and any(s.price is not None for s in p.sellers)
            ]
            for p in products:
                p.product_type = model_id

            span.set_attribute("product_count", len(products))
            span.set_attribute("summary",
                f"Search input on {domain} for '{model_id}': {len(products)} products")

            if products:
                await reset_site_search_failures(domain)
            else:
                await record_site_search_failure(domain)

            return products

    return []
