"""Shared Playwright browser management with stealth configuration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from playwright.async_api import Browser, Page, async_playwright

from src.shared.config import settings
from src.shared.logging import get_logger
from src.shared.market_config import (
    get_browser_geolocation,
    get_browser_languages,
    get_browser_timezone,
)

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

    # Try remote browser first
    ws = settings.browser_ws_endpoint
    if ws:
        try:
            browser = await pw.chromium.connect(ws)
            is_remote = True
            logger.info("Connected to remote browser at %s", ws)
        except Exception as exc:
            logger.warning("Remote browser connection failed (%s), falling back to local launch", exc)

    # Fall back to local launch
    if not browser:
        try:
            browser = await pw.chromium.launch(
                headless=settings.playwright_headless,
                channel="chrome",
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
        except Exception:
            try:
                browser = await pw.chromium.launch(
                    headless=settings.playwright_headless,
                    args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                )
            except Exception as exc:
                logger.warning("Chromium launch failed (%s), falling back to Firefox", exc)
                try:
                    browser = await pw.firefox.launch(
                        headless=settings.playwright_headless,
                    )
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
