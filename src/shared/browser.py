"""Shared Playwright browser management with stealth configuration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import litellm
from playwright.async_api import Browser, Page, async_playwright
from sqlalchemy import select

from src.shared.config import settings
from opentelemetry import trace as otel_trace

from src.shared.logging import get_logger
from src.shared.market_config import (
    get_browser_geolocation,
    get_browser_languages,
    get_browser_timezone,
)
from src.shared.proxy import get_playwright_proxy

logger = get_logger(__name__)


@asynccontextmanager
async def get_browser() -> AsyncIterator[Browser]:
    """Get a browser instance for page scraping.

    If ``BROWSER_WS_ENDPOINT`` is set (e.g. ``ws://localhost:3000``),
    connects to a remote browser (Docker container).  Otherwise launches
    a local Chromium/Firefox instance.
    """
    pw = await async_playwright().start()
    browser: Browser | None = None
    is_remote = False

    span = otel_trace.get_current_span()

    # Try remote browser first
    ws = settings.browser_ws_endpoint
    if ws:
        try:
            browser = await pw.chromium.connect(ws)
            is_remote = True
            logger.info("Connected to remote browser at %s", ws)
            if span and span.is_recording():
                span.add_event("browser.connected", {
                    "type": "remote",
                    "ws_endpoint": ws,
                    "summary": f"Connected to remote browser at {ws}",
                })
        except Exception as exc:
            logger.warning("Remote browser connection failed (%s), falling back to local launch", exc)
            if span and span.is_recording():
                span.add_event("browser.remote_failed", {
                    "ws_endpoint": ws,
                    "error": str(exc)[:200],
                })

    # Fall back to local launch
    if not browser:
        try:
            browser = await pw.chromium.launch(
                headless=settings.playwright_headless,
                channel="chrome",
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            if span and span.is_recording():
                span.add_event("browser.connected", {
                    "type": "local",
                    "engine": "chromium-chrome",
                    "headless": settings.playwright_headless,
                    "summary": "Launched local Chrome browser",
                })
        except Exception:
            try:
                browser = await pw.chromium.launch(
                    headless=settings.playwright_headless,
                    args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                )
                if span and span.is_recording():
                    span.add_event("browser.connected", {
                        "type": "local",
                        "engine": "chromium-bundled",
                        "summary": "Launched local bundled Chromium",
                    })
            except Exception as exc:
                logger.warning("Chromium launch failed (%s), falling back to Firefox", exc)
                try:
                    browser = await pw.firefox.launch(
                        headless=settings.playwright_headless,
                    )
                    if span and span.is_recording():
                        span.add_event("browser.connected", {
                            "type": "local",
                            "engine": "firefox",
                            "summary": "Launched local Firefox (Chromium fallback failed)",
                        })
                except Exception as exc2:
                    await pw.stop()
                    raise RuntimeError("No browser could be launched") from exc2
    try:
        yield browser
    finally:
        if not is_remote:
            await browser.close()
        await pw.stop()


@asynccontextmanager
async def get_page(
    browser: Browser, locale: str = "en-US", market: str = "il",
    *, use_proxy: bool = False,
) -> AsyncIterator[Page]:
    """Create a new page with realistic viewport settings.

    Uses Playwright's default User-Agent (which matches the bundled
    Chromium version) to avoid fingerprint mismatches that trigger
    bot detection.  Market-specific settings (timezone, geolocation,
    language) are loaded from config/markets/.
    """
    geo = get_browser_geolocation(market)
    ctx_kwargs: dict = {
        "viewport": {"width": 1920, "height": 1080},
        "locale": locale,
        "timezone_id": get_browser_timezone(market),
        # Override HeadlessChrome UA to avoid bot detection by APIs like findbar.io
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    if geo:
        ctx_kwargs["geolocation"] = geo
        ctx_kwargs["permissions"] = ["geolocation"]

    if use_proxy:
        proxy = get_playwright_proxy(market)
        if proxy:
            ctx_kwargs["proxy"] = proxy
            ctx_kwargs["ignore_https_errors"] = True

    context = await browser.new_context(**ctx_kwargs)

    languages = get_browser_languages(market)
    langs_js = ", ".join(f"'{lang}'" for lang in languages)
    await context.add_init_script(f"""
        Object.defineProperty(navigator, 'webdriver', {{get: () => undefined}});
        Object.defineProperty(navigator, 'plugins', {{
            get: () => [1, 2, 3, 4, 5],
        }});
        Object.defineProperty(navigator, 'languages', {{
            get: () => [{langs_js}],
        }});
        window.chrome = {{ runtime: {{}} }};

        // Intercept fetch/XHR JSON responses for API data capture
        window.__capturedApiResponses = [];
        const _origFetch = window.fetch;
        window.fetch = async function(...args) {{
            const resp = await _origFetch.apply(this, args);
            try {{
                const ct = resp.headers.get('content-type') || '';
                if (ct.includes('json') && resp.ok) {{
                    const clone = resp.clone();
                    clone.json().then(data => {{
                        if (window.__capturedApiResponses.length < 20) {{
                            window.__capturedApiResponses.push({{
                                url: resp.url || String(args[0]),
                                data: data,
                            }});
                        }}
                    }}).catch(() => {{}});
                }}
            }} catch(e) {{}}
            return resp;
        }};
    """)
    page = await context.new_page()
    try:
        yield page
    finally:
        await page.close()
        await context.close()


# -- Universal consent selectors (tried before LLM fallback) --
_UNIVERSAL_CONSENT_SELECTORS: list[str] = [
    "[id*='CookiebotDialogBodyLevelButtonLevelOptinAllowAll']",
    "button[class*='cookie'][class*='accept']",
    "button[data-action='accept']",
    "button:has-text('Accept all')",
    "button:has-text('Accept')",
    "button:has-text('Αποδοχή')",  # Greek
    "button:has-text('הסכמה')",  # Hebrew
    "button:has-text('אישור')",  # Hebrew
    "button:has-text('Akzeptieren')",  # German
    "button:has-text('Tout accepter')",  # French
    "button:has-text('OK')",
]

# In-memory cache: domain -> selector (or None if no consent detected)
_consent_cache: dict[str, str | None] = {}


async def _load_cached_selector(domain: str) -> str | None:
    """Load a previously discovered consent selector from DB."""
    if domain in _consent_cache:
        return _consent_cache[domain]
    try:
        from src.backend.db.engine import async_session
        from src.backend.db.models import ConsentSelector

        async with async_session() as session:
            result = await session.execute(
                select(ConsentSelector.selector).where(
                    ConsentSelector.domain == domain
                )
            )
            selector = result.scalar_one_or_none()
            _consent_cache[domain] = selector
            return selector
    except Exception:
        return None


async def _save_selector(domain: str, selector: str, source: str = "universal") -> None:
    """Save a discovered consent selector to DB."""
    _consent_cache[domain] = selector
    try:
        from src.backend.db.engine import async_session
        from src.backend.db.models import ConsentSelector

        async with async_session() as session:
            result = await session.execute(
                select(ConsentSelector).where(ConsentSelector.domain == domain)
            )
            record = result.scalar_one_or_none()
            if record:
                record.selector = selector
                record.source = source
            else:
                session.add(ConsentSelector(
                    domain=domain, selector=selector, source=source,
                ))
            await session.commit()
    except Exception:
        logger.debug("Failed to save consent selector for %s", domain)


async def _discover_consent_via_llm(page: Page, domain: str) -> str | None:
    """Use LLM to identify the accept button in a consent overlay.

    Extracts the visible overlay HTML and asks the LLM for a CSS selector.
    Returns the selector string if found, None otherwise.
    """
    try:
        # Extract dialog/overlay HTML (common consent containers)
        overlay_html = await page.evaluate("""() => {
            const selectors = [
                '[class*="consent"]', '[class*="cookie"]', '[class*="gdpr"]',
                '[id*="consent"]', '[id*="cookie"]', '[id*="gdpr"]',
                '[role="dialog"]', '[class*="modal"]', '[class*="overlay"]',
                '[class*="banner"]',
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el && el.offsetHeight > 0) {
                    return el.outerHTML.substring(0, 3000);
                }
            }
            return null;
        }""")

        if not overlay_html:
            return None

        model = settings.scraper_llm_model or settings.llm_model
        response = await litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": (
                    "You are a web automation assistant. Given HTML of a cookie/consent banner, "
                    "return ONLY a CSS selector for the 'Accept All' or 'Accept' button. "
                    "Return just the selector string, nothing else. "
                    "If no accept button is found, return 'NONE'."
                )},
                {"role": "user", "content": f"Domain: {domain}\n\nHTML:\n{overlay_html}"},
            ],
            temperature=0.0,
        )
        selector = (response.choices[0].message.content or "").strip()
        if selector and selector != "NONE" and len(selector) < 200:
            return selector
    except Exception:
        logger.debug("LLM consent discovery failed for %s", domain)

    return None


async def dismiss_consent(page: Page, domain: str) -> bool:
    """Dismiss cookie/consent banners using a multi-stage approach.

    1. Check DB for a cached selector for this domain
    2. Try universal selectors
    3. If overlay detected but no button found, use LLM on DOM snapshot
    4. Save discovered selector to DB for future use

    Returns True if a consent banner was dismissed.
    """
    # Stage 1: Try cached selector from DB
    cached = await _load_cached_selector(domain)
    if cached:
        try:
            btn = page.locator(cached).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click(timeout=2000)
                logger.debug("Dismissed consent on %s using cached selector", domain)
                return True
        except Exception:
            pass

    # Stage 2: Try universal selectors
    for sel in _UNIVERSAL_CONSENT_SELECTORS:
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click(timeout=2000)
                await _save_selector(domain, sel, source="universal")
                logger.debug("Dismissed consent on %s using universal selector", domain)
                return True
        except Exception:
            continue

    # Stage 3: Use LLM to discover the accept button
    llm_selector = await _discover_consent_via_llm(page, domain)
    if llm_selector:
        try:
            btn = page.locator(llm_selector).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click(timeout=2000)
                await _save_selector(domain, llm_selector, source="llm")
                logger.info("Dismissed consent on %s using LLM-discovered selector: %s", domain, llm_selector)
                return True
        except Exception:
            pass

    return False
