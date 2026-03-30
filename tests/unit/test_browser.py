"""Unit tests for the shared browser utility."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.shared.browser import get_browser, get_page


@pytest.mark.asyncio
async def test_get_browser_lifecycle():
    mock_browser = AsyncMock()
    mock_pw = AsyncMock()
    mock_pw.chromium.launch.return_value = mock_browser

    mock_pw_ctx = AsyncMock()
    mock_pw_ctx.start.return_value = mock_pw

    with patch("src.shared.browser.async_playwright", return_value=mock_pw_ctx):
        async with get_browser() as browser:
            assert browser is mock_browser

        mock_pw.chromium.launch.assert_called_once()
        mock_browser.close.assert_awaited_once()
        mock_pw.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_browser_launches_headless():
    mock_browser = AsyncMock()
    mock_pw = AsyncMock()
    mock_pw.chromium.launch.return_value = mock_browser

    mock_pw_ctx = AsyncMock()
    mock_pw_ctx.start.return_value = mock_pw

    with patch("src.shared.browser.async_playwright", return_value=mock_pw_ctx):
        async with get_browser() as _browser:
            pass

        call_kwargs = mock_pw.chromium.launch.call_args
        assert call_kwargs.kwargs["headless"] is True
        assert "--no-sandbox" in call_kwargs.kwargs["args"]
        assert "--disable-blink-features=AutomationControlled" in call_kwargs.kwargs["args"]
        assert call_kwargs.kwargs["channel"] == "chrome"


@pytest.mark.asyncio
async def test_get_page_lifecycle():
    mock_page = AsyncMock()
    mock_context = AsyncMock()
    mock_context.new_page.return_value = mock_page
    mock_browser = AsyncMock()
    mock_browser.new_context.return_value = mock_context
    mock_browser.browser_type.name = "chromium"

    with (
        patch("src.shared.browser.get_browser_timezone", return_value="Asia/Jerusalem"),
        patch("src.shared.browser.get_browser_geolocation", return_value={"latitude": 32.0, "longitude": 34.7}),
        patch("src.shared.browser.get_browser_languages", return_value=["he-IL", "he", "en-US", "en"]),
    ):
        async with get_page(mock_browser) as page:
            assert page is mock_page

    mock_browser.new_context.assert_called_once()
    mock_context.add_init_script.assert_awaited_once()
    mock_page.close.assert_awaited_once()
    mock_context.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_page_uses_market_config():
    mock_page = AsyncMock()
    mock_context = AsyncMock()
    mock_context.new_page.return_value = mock_page
    mock_browser = AsyncMock()
    mock_browser.new_context.return_value = mock_context
    mock_browser.browser_type.name = "chromium"

    with (
        patch("src.shared.browser.get_browser_timezone", return_value="Europe/Berlin") as mock_tz,
        patch("src.shared.browser.get_browser_geolocation", return_value={"latitude": 52.5, "longitude": 13.4}) as mock_geo,
        patch("src.shared.browser.get_browser_languages", return_value=["de-DE", "de", "en"]) as mock_langs,
    ):
        async with get_page(mock_browser, locale="de-DE", market="de") as _page:
            pass

    # Verify market config functions were called with correct market code
    mock_tz.assert_called_once_with("de")
    mock_geo.assert_called_once_with("de")
    mock_langs.assert_called_once_with("de")

    call_kwargs = mock_browser.new_context.call_args.kwargs
    assert "user_agent" not in call_kwargs
    assert call_kwargs["locale"] == "de-DE"
    assert call_kwargs["viewport"] == {"width": 1920, "height": 1080}
    assert call_kwargs["timezone_id"] == "Europe/Berlin"
    assert call_kwargs["geolocation"] == {"latitude": 52.5, "longitude": 13.4}
