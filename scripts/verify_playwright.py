"""Verify that Playwright is installed and can launch a browser."""

from __future__ import annotations

import asyncio
import sys


async def main() -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("FAIL: playwright is not installed")
        return 1

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto("about:blank")
            title = await page.title()
            await browser.close()
        print(f"OK: Playwright launched Chromium successfully (page title: '{title}')")
        return 0
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
