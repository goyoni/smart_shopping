"""IP geolocation for market auto-detection."""

from __future__ import annotations

import logging

from src.shared.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_MARKET = "us"


def detect_market(ip_address: str) -> str:
    """Resolve IP address to market code using MaxMind GeoLite2.

    Returns ISO country code lowercased (e.g. 'il', 'us').
    Falls back to 'us' if lookup fails or database is unavailable.
    """
    try:
        import geoip2.database
    except ImportError:
        logger.warning("geoip2 not installed, defaulting to market '%s'", _DEFAULT_MARKET)
        return _DEFAULT_MARKET

    try:
        with geoip2.database.Reader(settings.geoip_db_path) as reader:
            response = reader.country(ip_address)
            country = response.country.iso_code
            if country:
                return country.lower()
    except FileNotFoundError:
        logger.warning(
            "GeoLite2 database not found at '%s', defaulting to market '%s'",
            settings.geoip_db_path,
            _DEFAULT_MARKET,
        )
    except Exception:
        logger.warning("GeoIP lookup failed for '%s', defaulting to market '%s'", ip_address, _DEFAULT_MARKET)

    return _DEFAULT_MARKET


def get_client_ip(request: object) -> str:
    """Extract client IP from a FastAPI Request.

    Checks X-Forwarded-For header first (for reverse proxies),
    then falls back to request.client.host.
    """
    # Access headers if available (FastAPI Request)
    headers = getattr(request, "headers", {})
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        # X-Forwarded-For may contain multiple IPs; the first is the client
        return forwarded.split(",")[0].strip()

    client = getattr(request, "client", None)
    if client:
        return client.host

    return "127.0.0.1"
