"""Unit tests for geo detection module."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.shared.geo import detect_market, get_client_ip


class TestDetectMarket:
    def test_returns_country_code_from_geoip(self):
        mock_reader_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.country.iso_code = "IL"
        mock_reader_instance.country.return_value = mock_response
        mock_reader_instance.__enter__ = MagicMock(return_value=mock_reader_instance)
        mock_reader_instance.__exit__ = MagicMock(return_value=False)

        mock_geoip2 = MagicMock()
        mock_geoip2.database.Reader.return_value = mock_reader_instance

        with patch.dict("sys.modules", {"geoip2": mock_geoip2, "geoip2.database": mock_geoip2.database}):
            result = detect_market("1.2.3.4")

        assert result == "il"

    def test_falls_back_when_db_not_found(self):
        mock_geoip2 = MagicMock()
        mock_reader_instance = MagicMock()
        mock_reader_instance.__enter__ = MagicMock(side_effect=FileNotFoundError)
        mock_geoip2.database.Reader.return_value = mock_reader_instance

        with patch.dict("sys.modules", {"geoip2": mock_geoip2, "geoip2.database": mock_geoip2.database}):
            result = detect_market("1.2.3.4")

        assert result == "us"

    def test_falls_back_when_geoip2_not_installed(self):
        with patch.dict("sys.modules", {"geoip2": None}):
            # Force re-import failure
            with patch("builtins.__import__", side_effect=ImportError):
                result = detect_market("1.2.3.4")

        assert result == "us"

    def test_falls_back_on_lookup_error(self):
        mock_reader_instance = MagicMock()
        mock_reader_instance.__enter__ = MagicMock(return_value=mock_reader_instance)
        mock_reader_instance.__exit__ = MagicMock(return_value=False)
        mock_reader_instance.country.side_effect = ValueError("invalid IP")

        mock_geoip2 = MagicMock()
        mock_geoip2.database.Reader.return_value = mock_reader_instance

        with patch.dict("sys.modules", {"geoip2": mock_geoip2, "geoip2.database": mock_geoip2.database}):
            result = detect_market("bad-ip")

        assert result == "us"


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
