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
    """Launch a headless browser for page scraping.

    Tries Chromium first, falls back to Firefox if Chromium fails to launch.
    """
    pw = await async_playwright().start()
    browser: Browser | None = None
    try:
        browser = await pw.chromium.launch(
            headless=settings.playwright_headless,
            channel="chrome",
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
    except Exception:
        # channel="chrome" requires Chrome installed; fall back to bundled Chromium
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
    }
    if geo:
        ctx_kwargs["geolocation"] = geo
        ctx_kwargs["permissions"] = ["geolocation"]

    context = await browser.new_context(**ctx_kwargs)

    if browser.browser_type.name == "chromium":
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
        """)
    page = await context.new_page()
    try:
        yield page
    finally:
        await page.close()
        await context.close()
