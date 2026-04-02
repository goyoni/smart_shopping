"""Unit tests for geo detection module."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.shared.config import settings
from src.shared.geo import _is_private_ip, _geolocate_ip_api, detect_market, get_client_ip


class TestIsPrivateIp:
    def test_localhost_is_private(self):
        assert _is_private_ip("127.0.0.1") is True

    def test_rfc1918_is_private(self):
        assert _is_private_ip("192.168.1.1") is True
        assert _is_private_ip("10.0.0.5") is True

    def test_public_ip_is_not_private(self):
        assert _is_private_ip("8.8.8.8") is False

    def test_invalid_ip_returns_false(self):
        assert _is_private_ip("not-an-ip") is False


class TestDetectMarket:
    def _make_geoip_mock(self, iso_code: str = "IL"):
        mock_reader_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.country.iso_code = iso_code
        mock_reader_instance.country.return_value = mock_response
        mock_reader_instance.__enter__ = MagicMock(return_value=mock_reader_instance)
        mock_reader_instance.__exit__ = MagicMock(return_value=False)
        mock_geoip2 = MagicMock()
        mock_geoip2.database.Reader.return_value = mock_reader_instance
        return mock_geoip2

    def test_returns_country_code_from_geoip(self):
        mock_geoip2 = self._make_geoip_mock("IL")
        with patch.dict("sys.modules", {"geoip2": mock_geoip2, "geoip2.database": mock_geoip2.database}):
            result = detect_market("1.2.3.4")
        assert result == "il"

    def test_private_ip_resolves_public_ip_then_geolocates(self):
        mock_geoip2 = self._make_geoip_mock("GR")
        with (
            patch("src.shared.geo._fetch_public_ip", return_value="8.8.8.8"),
            patch.dict("sys.modules", {"geoip2": mock_geoip2, "geoip2.database": mock_geoip2.database}),
        ):
            result = detect_market("127.0.0.1")
        assert result == "gr"

    def test_private_ip_falls_back_when_public_ip_unavailable(self):
        with patch("src.shared.geo._fetch_public_ip", return_value=None):
            result = detect_market("192.168.1.1")
        assert result == settings.default_market

    def test_falls_back_to_api_when_geoip2_db_missing(self):
        with (
            patch("src.shared.geo._geolocate_geoip2", return_value=None),
            patch("src.shared.geo._geolocate_ip_api", return_value="gr"),
        ):
            result = detect_market("8.8.8.8")
        assert result == "gr"

    def test_falls_back_to_default_when_all_fail(self):
        with (
            patch("src.shared.geo._geolocate_geoip2", return_value=None),
            patch("src.shared.geo._geolocate_ip_api", return_value=None),
        ):
            result = detect_market("8.8.8.8")
        assert result == settings.default_market

    def test_falls_back_when_geoip2_not_installed(self):
        with (
            patch("src.shared.geo._geolocate_geoip2", return_value=None),
            patch("src.shared.geo._geolocate_ip_api", return_value=None),
        ):
            result = detect_market("1.2.3.4")
        assert result == settings.default_market


class TestGeolocateIpApi:
    def test_returns_country_code(self):
        mock_result = MagicMock(returncode=0, stdout="GR\n")
        with patch("src.shared.geo.subprocess.run", return_value=mock_result):
            assert _geolocate_ip_api("8.8.8.8") == "gr"

    def test_returns_none_on_failure(self):
        mock_result = MagicMock(returncode=1, stdout="")
        with patch("src.shared.geo.subprocess.run", return_value=mock_result):
            assert _geolocate_ip_api("8.8.8.8") is None

    def test_rejects_invalid_response(self):
        mock_result = MagicMock(returncode=0, stdout="Rate limited\n")
        with patch("src.shared.geo.subprocess.run", return_value=mock_result):
            assert _geolocate_ip_api("8.8.8.8") is None


class TestGetClientIp:
    def test_extracts_from_x_forwarded_for(self):
        request = SimpleNamespace(
            headers={"x-forwarded-for": "203.0.113.50, 70.41.3.18"},
            client=SimpleNamespace(host="10.0.0.1"),
        )
        assert get_client_ip(request) == "203.0.113.50"

    def test_single_x_forwarded_for(self):
        request = SimpleNamespace(
            headers={"x-forwarded-for": "203.0.113.50"},
            client=SimpleNamespace(host="10.0.0.1"),
        )
        assert get_client_ip(request) == "203.0.113.50"

    def test_falls_back_to_client_host(self):
        request = SimpleNamespace(
            headers={},
            client=SimpleNamespace(host="192.168.1.1"),
        )
        assert get_client_ip(request) == "192.168.1.1"

    def test_falls_back_to_localhost_when_no_client(self):
        request = SimpleNamespace(headers={})
        assert get_client_ip(request) == "127.0.0.1"
