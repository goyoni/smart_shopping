"""Shared Playwright browser management with stealth configuration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from playwright.async_api import Browser, Page, async_playwright

from src.shared.config import settings
from src.shared.logging import get_logger

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
            args=["--no-sandbox"],
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
async def get_page(browser: Browser, locale: str = "en-US") -> AsyncIterator[Page]:
    """Create a new page with realistic viewport settings.

    Uses Playwright's default User-Agent (which matches the bundled
    Chromium version) to avoid fingerprint mismatches that trigger
    bot detection.
    """
    context = await browser.new_context(
        viewport={"width": 1920, "height": 1080},
        locale=locale,
    )
    # Remove navigator.webdriver flag (Chromium only; Firefox handles this differently)
    if browser.browser_type.name == "chromium":
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    page = await context.new_page()
    try:
        yield page
    finally:
        await page.close()
        await context.close()
