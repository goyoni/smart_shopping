"""Shared Playwright browser management with anti-detection."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from playwright.async_api import Browser, Page, async_playwright

from src.shared.config import settings

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@asynccontextmanager
async def get_browser() -> AsyncIterator[Browser]:
    """Launch a headless Chromium browser with anti-detection measures."""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=settings.playwright_headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    )
    try:
        yield browser
    finally:
        await browser.close()
        await pw.stop()


@asynccontextmanager
async def get_page(browser: Browser, locale: str = "en-US") -> AsyncIterator[Page]:
    """Create a new page with realistic viewport and user-agent settings."""
    context = await browser.new_context(
        user_agent=_USER_AGENT,
        viewport={"width": 1920, "height": 1080},
        locale=locale,
    )
    # Remove navigator.webdriver flag
    await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    page = await context.new_page()
    try:
        yield page
    finally:
        await page.close()
        await context.close()
