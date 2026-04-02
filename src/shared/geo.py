"""IP geolocation for market auto-detection."""

from __future__ import annotations

import ipaddress
import subprocess

from src.shared.config import settings
from src.shared.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_MARKET = settings.default_market


def _is_private_ip(ip: str) -> bool:
    """Check if an IP address is private/loopback/link-local."""
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def _fetch_public_ip() -> str | None:
    """Fetch the machine's public IP via external service.

    Uses curl subprocess to avoid Python sandbox and proxy restrictions.
    """
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "3", "https://api.ipify.org"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            candidate = result.stdout.strip()
            ipaddress.ip_address(candidate)  # validate format
            return candidate
    except ValueError:
        logger.debug("Invalid IP format from api.ipify.org")
    except Exception:
        logger.debug("Failed to fetch public IP via curl")
    return None


def _geolocate_ip_api(ip_address: str) -> str | None:
    """Geolocate an IP using a free API. Used as fallback when the local
    GeoLite2 database is unavailable."""
    try:
        ipaddress.ip_address(ip_address)  # validate before external call
    except ValueError:
        logger.debug("Invalid IP address format: %s", ip_address)
        return None
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "3",
             f"https://ipinfo.io/{ip_address}/country"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            code = result.stdout.strip()
            if len(code) == 2 and code.isalpha():
                return code.lower()
    except Exception:
        logger.debug("ipinfo.io lookup failed for %s", ip_address)
    return None


def detect_market(ip_address: str) -> str:
    """Resolve IP address to a market code.

    When the IP is private (e.g. localhost in dev), fetches the machine's
    public IP first so geolocation reflects the user's actual location.

    Tries MaxMind GeoLite2 first, then falls back to ipinfo.io.

    Returns ISO country code lowercased (e.g. 'il', 'gr').
    Falls back to default_market if all lookups fail.
    """
    if _is_private_ip(ip_address):
        public_ip = _fetch_public_ip()
        if public_ip:
            logger.info("Private IP %s -> resolved public IP %s", ip_address, public_ip)
            ip_address = public_ip
        else:
            logger.warning("Private IP %s and could not resolve public IP, defaulting to '%s'", ip_address, _DEFAULT_MARKET)
            return _DEFAULT_MARKET

    # Try local GeoLite2 database first
    market = _geolocate_geoip2(ip_address)
    if market:
        return market

    # Fallback to free API
    market = _geolocate_ip_api(ip_address)
    if market:
        logger.info("ipinfo.io resolved %s -> market '%s'", ip_address, market)
        return market

    logger.warning("All geolocation methods failed for '%s', defaulting to '%s'", ip_address, _DEFAULT_MARKET)
    return _DEFAULT_MARKET


def _geolocate_geoip2(ip_address: str) -> str | None:
    """Try to geolocate using local MaxMind GeoLite2 database."""
    try:
        import geoip2.database
    except ImportError:
        logger.debug("geoip2 not installed, skipping local DB lookup")
        return None

    try:
        with geoip2.database.Reader(settings.geoip_db_path) as reader:
            response = reader.country(ip_address)
            country = response.country.iso_code
            if country:
                market = country.lower()
                logger.info("GeoIP resolved %s -> market '%s'", ip_address, market)
                return market
    except FileNotFoundError:
        logger.debug("GeoLite2 database not found at '%s'", settings.geoip_db_path)
    except Exception:
        logger.debug("GeoIP lookup failed for '%s'", ip_address)

    return None


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
