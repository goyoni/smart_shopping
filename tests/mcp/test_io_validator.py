"""Unit tests for the IO Validator MCP server."""

from __future__ import annotations

from src.mcp_servers.io_validator_mcp.server import sanitize_output, validate_input


def test_validate_input_clean():
    is_valid, message = validate_input("I want a black refrigerator")
    assert is_valid is True
    assert message == ""


def test_validate_input_with_ssn():
    is_valid, message = validate_input("My SSN is 123-45-6789")
    assert is_valid is False
    assert "sensitive" in message.lower()


def test_sanitize_output_clean():
    text = "Product costs $299"
    assert sanitize_output(text) == text


def test_sanitize_output_with_ssn():
    text = "Call 123-45-6789 for info"
    result = sanitize_output(text)
    assert "[REDACTED]" in result
