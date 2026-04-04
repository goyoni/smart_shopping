"""Web search via DuckDuckGo HTML for product discovery."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from urllib.parse import quote_plus, unquote

import litellm
from curl_cffi.requests import AsyncSession
from opentelemetry import trace

from src.shared.config import settings
from src.shared.logging import get_logger, get_tracer, operation_span, set_span_token_counts
from src.shared.market_config import (
    get_buy_online_suffix,
    get_google_domain,
    get_language_name,
    get_market_language,
    get_market_name,
    get_region_code,
)
from src.shared.models import QueryAttribute

logger = get_logger(__name__)
_tracer = get_tracer(__name__)

_SEARCH_URL = "https://html.duckduckgo.com/html/"


async def _translate_query(query: str, target_lang: str) -> str | None:
    """Translate a search query to the target language using LLM.

    Returns None when the API key is not configured or on any failure.
    """
    if not settings.llm_api_key:
        return None

    target_name = get_language_name(target_lang)
    with operation_span(
        _tracer, "translate_query",
        input=query,
        target_language=target_name,
    ) as span:
        try:
            response = await litellm.acompletion(
                model=settings.llm_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"Translate the following product search query to {target_name}. "
                            "Return ONLY the translated query, nothing else."
                        ),
                    },
                    {"role": "user", "content": query},
                ],
                temperature=0.1,
                api_key=settings.llm_api_key,
            )
            translated = (response.choices[0].message.content or "").strip()
            usage = response.get("usage") or {}
            set_span_token_counts(
                span,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            )
            if translated:
                logger.info("Translated query to %s: '%s' -> '%s'", target_name, query, translated)
                span.set_attribute("output", translated)
                span.set_attribute("summary", f"Translated '{query}' -> '{translated}' ({target_name})")
                return translated
            span.set_attribute("summary", f"Translation returned empty for '{query}'")
        except Exception as exc:
            logger.warning("Query translation to %s failed", target_name, exc_info=True)
            span.set_attribute("summary", f"Translation failed: {type(exc).__name__}: {str(exc)[:200]}")
        return None


_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

_REQUEST_TIMEOUT = 20.0

# Technical search terms for each attribute criterion+direction pair
_ATTRIBUTE_SEARCH_TERMS: dict[tuple[str, str], str] = {
    ("noise_level", "low"): "low noise dB",
    ("noise_level", "high"): "high power noise",
    ("capacity", "high"): "large capacity",
    ("capacity", "low"): "compact small",
    ("energy_rating", "high"): "energy efficient",
    ("energy_rating", "low"): "",
    ("weight", "low"): "lightweight",
    ("weight", "high"): "heavy duty",
    ("power", "high"): "high power",
    ("power", "low"): "low power",
    ("price", "low"): "affordable",
    ("price", "high"): "premium",
}


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str


def build_refined_query(
    query: str,
    category: str | None,
    attributes: list[QueryAttribute],
) -> str:
    """Build a search query informed by extracted category and user-intent attributes.

    - Uses canonical category name instead of user's alias (e.g. "refrigerator" not "fridge")
    - Appends technical search terms for each attribute
    - Keeps user's non-attribute/non-category words
    """
    if not category and not attributes:
        return query

    text = query.lower().strip()

    # Collect words to remove (attribute keywords and category aliases)
    remove_words: set[str] = set()
    for attr in attributes:
        remove_words.add(attr.display_label.lower())

    # Build technical terms from attributes
    technical_terms: list[str] = []
    for attr in attributes:
        term = _ATTRIBUTE_SEARCH_TERMS.get(
            (attr.criterion_key, attr.direction), ""
        )
        if term:
            technical_terms.append(term)
        elif attr.display_label:
            # LLM-discovered attributes: use display_label as search term
            technical_terms.append(attr.display_label)

    # Remove attribute keywords from user query
    remaining = text
    for word in sorted(remove_words, key=lambda w: -len(w)):
        remaining = remaining.replace(word, " ")
    # Clean up extra whitespace
    remaining = " ".join(remaining.split())

    # If we have a category, remove category aliases from remaining text too
    if category:
        from src.shared.market_config import get_all_category_aliases

        all_aliases = list(get_all_category_aliases().keys())
        # Also include the canonical name itself
        all_aliases.append(category)
        all_aliases.append(category.replace("_", " "))
        for alias in sorted(all_aliases, key=lambda a: -len(a)):
            if alias.lower() in remaining:
                remaining = remaining.replace(alias.lower(), " ")
        remaining = " ".join(remaining.split())

    # Assemble: category + technical terms + remaining user words
    parts: list[str] = []
    if category:
        parts.append(category.replace("_", " "))
    parts.extend(technical_terms)
    if remaining:
        parts.append(remaining)

    return " ".join(parts)


def build_search_url(
    query: str,
    language: str = "en",
    market: str = "us",
    refined_query: str | None = None,
) -> str:
    """Build the augmented search query URL.

    Augments query with 'buy online' in the appropriate language to bias
    toward shopping results.  Returns the DuckDuckGo HTML endpoint URL.

    If ``refined_query`` is provided, it is used instead of the raw ``query``
    for URL construction.
    """
    search_text = refined_query if refined_query else query
    suffix = get_buy_online_suffix(language=language, market=market)
    augmented_query = f"{search_text} {suffix}"
    encoded = quote_plus(augmented_query)
    kl = get_region_code(market)
    return f"{_SEARCH_URL}?q={encoded}&kl={kl}"


def _extract_ddg_url(raw_url: str) -> str:
    """Extract the actual destination URL from a DuckDuckGo redirect link.

    DDG wraps results in ``//duckduckgo.com/l/?uddg=<encoded_url>&…``.
    """
    if "uddg=" in raw_url:
        match = re.search(r"uddg=([^&]+)", raw_url)
        if match:
            return unquote(match.group(1))
    # Direct URL (no redirect wrapper)
    if raw_url.startswith("//"):
        return "https:" + raw_url
    return raw_url


def extract_search_results(html: str) -> list[SearchResult]:
    """Extract URLs, titles, and snippets from DuckDuckGo HTML search results."""
    results: list[SearchResult] = []
    seen_urls: set[str] = set()

    # DuckDuckGo uses <a class="result__a" href="...">TITLE</a>
    link_pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )

    # Snippets are in <a class="result__snippet" ...>TEXT</a>
    snippet_pattern = re.compile(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )

    link_matches = link_pattern.findall(html)
    snippet_matches = snippet_pattern.findall(html)

    for i, (raw_url, raw_title) in enumerate(link_matches):
        url = _extract_ddg_url(raw_url)
        title = re.sub(r"<[^>]+>", "", raw_title).strip()
        if not title or not url:
            continue

        # Skip DuckDuckGo-internal links
        if "duckduckgo.com" in url and "/l/?" not in raw_url:
            continue

        if url in seen_urls:
            continue
        seen_urls.add(url)

        snippet = ""
        if i < len(snippet_matches):
            snippet = re.sub(r"<[^>]+>", "", snippet_matches[i]).strip()

        results.append(SearchResult(url=url, title=title, snippet=snippet))

    return results


async def _run_single_search(
    url: str,
    query: str,
    *,
    _max_attempts: int = 2,
) -> list[SearchResult]:
    """Execute one DuckDuckGo HTML search and return parsed results."""
    with operation_span(
        _tracer, "http_search",
        input=query,
        search_url=url,
    ) as span:
        for attempt in range(1, _max_attempts + 1):
            try:
                async with AsyncSession(
                    timeout=_REQUEST_TIMEOUT,
                    headers=_REQUEST_HEADERS,
                    impersonate="chrome",
                ) as client:
                    response = await client.get(url, allow_redirects=True)
            except Exception as exc:
                logger.warning(
                    "HTTP request failed for '%s' (attempt %d/%d)",
                    query, attempt, _max_attempts,
                )
                span.add_event("search.http_error", {
                    "attempt": attempt,
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                })
                if attempt == _max_attempts:
                    span.set_attribute("summary", f"All {_max_attempts} attempts failed with HTTP errors")
                    return []
                continue

            span.set_attribute("http_status", response.status_code)
            if response.status_code != 200:
                logger.warning(
                    "Search returned HTTP %d for '%s' (attempt %d/%d)",
                    response.status_code, query, attempt, _max_attempts,
                )
                span.add_event("search.bad_status", {
                    "attempt": attempt,
                    "status_code": response.status_code,
                })
                if attempt == _max_attempts:
                    span.set_attribute("summary", f"Search failed: HTTP {response.status_code}")
                    return []
                continue

            html = response.text
            span.set_attribute("response_length", len(html))

            # Check for DuckDuckGo rate-limiting indicators
            if "captcha" in html.lower() or "unusual traffic" in html.lower():
                span.add_event("search.rate_limited", {"indicator": "captcha_or_traffic_warning"})
                span.set_attribute("rate_limited", True)

            results = extract_search_results(html)
            span.set_attribute("result_count", len(results))
            if not results:
                logger.warning("No results extracted from HTML for '%s'", query)
                span.set_attribute("summary", f"HTTP 200 but 0 results parsed from {len(html)} chars HTML (possible rate-limit or empty page)")
            else:
                logger.info("Found %d search results for '%s'", len(results), query)
                top_domains = [r.url.split("/")[2] if "/" in r.url else r.url for r in results[:5]]
                span.set_attribute("summary", f"Found {len(results)} results. Top: {', '.join(top_domains)}")
            return results

        span.set_attribute("summary", "All attempts exhausted with no results")
        return []


async def search_products(
    query: str,
    language: str = "en",
    market: str = "us",
    *,
    refined_query: str | None = None,
    _max_attempts: int = 2,
) -> list[SearchResult]:
    """Search for products and return results.

    Uses DuckDuckGo's HTML endpoint which returns server-rendered HTML
    (no JavaScript required).  Retries once on transient failures.
    Returns empty list on HTTP errors or network issues.

    If ``refined_query`` is provided, it is used for search URL construction
    instead of the raw ``query``.

    When the market's dominant language differs from the query language,
    a second search is run using the market's language suffix and the
    results are merged (local results first, deduped by URL).
    """
    url = build_search_url(query, language, market, refined_query=refined_query)
    span = trace.get_current_span()
    span.set_attribute("search_url", url)

    logger.info("Starting search for '%s' (language=%s, market=%s)", query, language, market)

    # Determine if a second localized search is needed
    market_lang = get_market_language(market)
    need_local_search = market_lang is not None and market_lang != language
    span.set_attribute("search_mode", "dual" if need_local_search else "single")

    if need_local_search:
        # Translate the query to the market's language for local results
        translated = await _translate_query(
            refined_query or query, market_lang,
        )
        if translated:
            local_url = build_search_url(
                translated, market_lang, market,
            )
            span.set_attribute("local_search_url", local_url)
            span.set_attribute("translated_query", translated)

            # Run searches sequentially to avoid DuckDuckGo rate-limiting
            primary_results = await _run_single_search(url, query, _max_attempts=_max_attempts)
            await asyncio.sleep(1.0)
            local_results = await _run_single_search(local_url, translated, _max_attempts=_max_attempts)

            # Merge: local results first (deduped by URL)
            seen_urls: set[str] = set()
            merged: list[SearchResult] = []
            for r in local_results + primary_results:
                if r.url not in seen_urls:
                    seen_urls.add(r.url)
                    merged.append(r)

            span.set_attribute("result_count", len(merged))
            span.set_attribute("local_result_count", len(local_results))
            span.set_attribute("primary_result_count", len(primary_results))
            span.set_attribute("summary",
                f"Dual search: {len(local_results)} local ({market_lang}) + {len(primary_results)} primary ({language}) = {len(merged)} merged")
            logger.info(
                "Dual search: %d local + %d primary = %d merged for '%s'",
                len(local_results), len(primary_results), len(merged), query,
            )
            return merged

    # Single search (market matches query language)
    results = await _run_single_search(url, query, _max_attempts=_max_attempts)
    span.set_attribute("result_count", len(results))
    if not results:
        span.set_attribute("exit_reason", "no_results_extracted")
        span.set_attribute("summary", f"Single search returned 0 results for '{query}'")
    else:
        span.set_attribute("summary", f"Single search found {len(results)} results for '{query}'")
    return results


async def search_products_via_browser(
    browser: object,
    query: str,
    language: str = "en",
    market: str = "us",
    *,
    refined_query: str | None = None,
) -> list[SearchResult]:
    """Search for products using a Playwright browser with proper locale.

    Uses the country-specific Google domain so that the search engine
    returns local sellers with local-currency prices — the same results
    the user would see in their own browser.

    Falls back to :func:`search_products` (HTTP-based DuckDuckGo) when the
    browser search yields no results.
    """
    from src.shared.browser import get_page  # avoid circular at module level

    search_text = refined_query or query
    google_domain = get_google_domain(market)
    suffix = get_buy_online_suffix(language=language, market=market)
    search_url = f"https://www.{google_domain}/search?q={quote_plus(f'{search_text} {suffix}')}"

    locale = f"{language}-{market.upper()}"
    market_lang = get_market_language(market)
    if market_lang and market_lang != language:
        locale = f"{market_lang}-{market.upper()}"

    span = trace.get_current_span()
    span.set_attribute("browser_search_url", search_url)
    span.set_attribute("browser_locale", locale)
    span.set_attribute("browser_google_domain", google_domain)
    logger.info("Browser search: '%s' on %s (locale=%s)", search_text, google_domain, locale)

    try:
        async with get_page(browser, locale=locale) as page:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=15000)

            # Accept Google consent if prompted
            from src.shared.browser import dismiss_consent
            google_domain_name = google_domain.replace("www.", "")
            if await dismiss_consent(page, google_domain_name):
                await page.wait_for_load_state("domcontentloaded", timeout=5000)

            # Extract search result links
            results: list[SearchResult] = []
            seen_urls: set[str] = set()

            anchors = page.locator("a[href]")
            count = await anchors.count()

            for i in range(min(count, 100)):
                try:
                    anchor = anchors.nth(i)
                    href = await anchor.get_attribute("href") or ""
                    if not href or href.startswith("#") or href.startswith("javascript:"):
                        continue

                    # Skip Google's own links
                    from urllib.parse import urlparse
                    parsed = urlparse(href)
                    host = parsed.hostname or ""
                    if any(g in host for g in ("google.", "gstatic.", "googleapis.", "youtube.")):
                        continue
                    if not parsed.scheme or parsed.scheme not in ("http", "https"):
                        continue

                    # Resolve Google redirect URLs (/url?q=...)
                    if "/url?" in href and "q=" in href:
                        from urllib.parse import parse_qs
                        qs = parse_qs(parsed.query)
                        actual = qs.get("q", [""])[0] or qs.get("url", [""])[0]
                        if actual:
                            href = actual
                        else:
                            continue

                    if href in seen_urls:
                        continue
                    seen_urls.add(href)

                    title = (await anchor.inner_text()).strip()[:200]
                    if not title:
                        continue

                    results.append(SearchResult(
                        url=href,
                        title=title,
                        snippet="",
                    ))
                except Exception:
                    continue

            # Extract Google Shopping results (product cards with prices)
            shopping_count = 0
            try:
                shopping_cards = page.locator("[data-docid], .sh-dgr__content, .commercial-unit-desktop-top a[href*='/shopping/']")
                card_count = await shopping_cards.count()
                for i in range(min(card_count, 20)):
                    try:
                        card = shopping_cards.nth(i)
                        card_anchor = card.locator("a[href]").first
                        if await card_anchor.count() == 0:
                            # The card itself might be the anchor
                            card_anchor = card if await card.get_attribute("href") else None
                            if not card_anchor:
                                continue

                        href = await card_anchor.get_attribute("href") or ""
                        if not href or href in seen_urls:
                            continue

                        # Resolve Google redirect URLs
                        if "/url?" in href and "q=" in href:
                            from urllib.parse import parse_qs
                            parsed_href = urlparse(href)
                            qs = parse_qs(parsed_href.query)
                            actual = qs.get("q", [""])[0] or qs.get("url", [""])[0]
                            if actual:
                                href = actual

                        parsed_href = urlparse(href)
                        host = parsed_href.hostname or ""
                        if any(g in host for g in ("google.", "gstatic.", "googleapis.")):
                            continue
                        if not parsed_href.scheme or parsed_href.scheme not in ("http", "https"):
                            continue

                        seen_urls.add(href)
                        title = (await card.inner_text()).strip()[:200]
                        results.append(SearchResult(
                            url=href,
                            title=f"[Shopping] {title}" if title else "[Shopping result]",
                            snippet="",
                        ))
                        shopping_count += 1
                    except Exception:
                        continue
            except Exception:
                pass

            span.set_attribute("browser_result_count", len(results))
            span.set_attribute("shopping_result_count", shopping_count)
            logger.info("Browser search found %d results (%d shopping) for '%s'",
                       len(results), shopping_count, query)

            if results:
                span.set_attribute("summary",
                    f"Browser search on {google_domain} found {len(results)} results ({shopping_count} shopping)")
                return results

    except Exception as exc:
        logger.warning("Browser search failed for '%s'", query, exc_info=True)
        span.set_attribute("browser_search_error", "true")
        span.set_attribute("browser_error_detail", f"{type(exc).__name__}: {str(exc)[:200]}")

    # Fallback to HTTP-based search
    span.set_attribute("summary", f"Browser search failed/empty, falling back to HTTP search")
    logger.info("Falling back to HTTP search for '%s'", query)
    return await search_products(query, language, market, refined_query=refined_query)


async def get_aggregator_urls(
    model_id: str,
    market: str,
    category: str | None = None,
) -> list[dict[str, str]]:
    """Return direct aggregator search URLs for a model in the given market.

    Queries the DB for aggregators matching the market and category.
    Each returned dict has ``domain`` and ``url`` keys.
    """
    from src.mcp_servers.web_search_mcp.aggregator_db import (
        build_aggregator_url,
        get_aggregators,
    )

    templates = await get_aggregators(market, category)
    return [
        {"domain": t["domain"], "url": build_aggregator_url(t["url_template"], model_id)}
        for t in templates
    ]


async def discover_aggregators(
    market: str,
    category: str,
) -> list[dict[str, str]]:
    """Use LLM to discover aggregator/price-comparison sites for a market+category.

    Returns a list of ``{"domain": ..., "url_template": ...}`` dicts.
    Saves discovered sites to DB for future use.
    """
    if not settings.llm_api_key:
        return []

    market_name = get_market_name(market)
    with operation_span(
        _tracer, "discover_aggregators",
        input=f"{market_name}/{category}",
        market=market,
        category=category,
    ) as span:
        try:
            system_prompt = (
                "You are a shopping research expert. Given a country and product category, "
                "return a JSON array of price-comparison or marketplace websites popular in "
                "that country for that category. Each entry should have:\n"
                '"domain": the site domain (e.g. "zap.co.il")\n'
                '"url_template": the search URL with {query} as placeholder\n'
                '"categories": array of category keywords this site covers\n'
                "Return ONLY the JSON array, no other text. "
                "Include 3-5 sites. Only include real, well-known sites."
            )
            user_prompt = f"Country: {market_name}\nProduct category: {category}"
            span.set_attribute("llm.system_prompt", system_prompt)
            span.set_attribute("llm.user_prompt", user_prompt)
            span.set_attribute("llm.model", settings.llm_model)

            response = await litellm.acompletion(
                model=settings.llm_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                api_key=settings.llm_api_key,
            )
            raw = (response.choices[0].message.content or "").strip()
            usage = response.get("usage") or {}
            set_span_token_counts(
                span,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            )
            span.set_attribute("llm.raw_response", raw[:1000])

            # Strip markdown fences
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

            sites = json.loads(raw)
            if not isinstance(sites, list):
                span.set_attribute("summary", "LLM returned non-list JSON")
                return []

            from src.mcp_servers.web_search_mcp.aggregator_db import save_aggregator

            saved: list[dict[str, str]] = []
            for site in sites:
                domain = site.get("domain", "")
                url_tpl = site.get("url_template", "")
                cats = site.get("categories", [])
                if not domain or not url_tpl or "{query}" not in url_tpl:
                    continue
                await save_aggregator(domain, url_tpl, market, cats, source="llm")
                saved.append({"domain": domain, "url_template": url_tpl})

            domains = [s["domain"] for s in saved]
            span.set_attribute("output", json.dumps(saved, ensure_ascii=False))
            span.set_attribute("summary",
                f"Discovered {len(saved)} aggregators for {market_name}/{category}: {', '.join(domains)}")
            logger.info(
                "Discovered %d aggregators for %s/%s via LLM",
                len(saved), market, category,
            )
            return saved

        except Exception as exc:
            logger.warning(
                "LLM aggregator discovery failed for %s/%s",
                market, category, exc_info=True,
            )
            span.set_attribute("summary", f"Failed: {type(exc).__name__}: {str(exc)[:200]}")
            return []


async def search_on_site(
    model_id: str,
    domain: str,
    language: str = "en",
    market: str = "us",
) -> list[SearchResult]:
    """Search for a model ID on a specific seller's website via DuckDuckGo ``site:`` prefix."""
    site_query = f"site:{domain} {model_id}"
    url = build_search_url(site_query, language, market)
    with operation_span(
        _tracer, "search_on_site",
        input=site_query,
        domain=domain,
        model_id=model_id,
    ) as span:
        results = await _run_single_search(url, site_query)
        filtered = [r for r in results if domain in r.url]
        span.set_attribute("raw_result_count", len(results))
        span.set_attribute("filtered_result_count", len(filtered))
        span.set_attribute("summary",
            f"site:{domain} search for '{model_id}': {len(results)} raw -> {len(filtered)} on-domain results")
        return filtered
