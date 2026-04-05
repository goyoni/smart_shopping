"""Per-market proxy resolution.

Replaces the ``{country}`` placeholder in ``settings.proxy_url`` with the
two-letter market code so that requests are routed through an IP in the
target country.  Works with any residential-proxy provider that supports
country targeting via the URL (Bright Data, Oxylabs, SmartProxy, etc.).

When ``PROXY_URL`` is empty (default), all helpers return ``None`` and
no proxy is used — local development without a proxy still works.
"""

from __future__ import annotations

from urllib.parse import urlparse

from src.shared.config import settings


def get_proxy_for_market(market: str) -> str | None:
    """Return the proxy URL for *market*, or ``None`` if proxying is disabled."""
    template = settings.proxy_url
    if not template:
        return None
    return template.replace("{country}", market.lower())


def get_playwright_proxy(market: str) -> dict[str, str] | None:
    """Return a Playwright-compatible proxy dict, or ``None``.

    Playwright requires ``username`` and ``password`` as separate fields
    rather than embedded in the URL, so we parse them out.
    """
    url = get_proxy_for_market(market)
    if not url:
        return None
    parsed = urlparse(url)
    server = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        server += f":{parsed.port}"
    result: dict[str, str] = {"server": server}
    if parsed.username:
        result["username"] = parsed.username
    if parsed.password:
        result["password"] = parsed.password
    return result
