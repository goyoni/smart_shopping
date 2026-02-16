"""IO Validator MCP Server implementation."""

from __future__ import annotations

import re

from mcp.server import Server
from mcp.types import Tool, TextContent

server = Server("io-validator")

# Patterns that indicate PII
_PII_PATTERNS = [
    re.compile(r"\b\d{3}[-.]?\d{2}[-.]?\d{4}\b"),  # SSN-like
    re.compile(r"\b\d{9}\b"),  # ID numbers
]


def validate_input(text: str) -> tuple[bool, str]:
    """Check that user input doesn't contain sensitive information."""
    for pattern in _PII_PATTERNS:
        if pattern.search(text):
            return False, "Input may contain sensitive information. Please remove it."
    return True, ""


def sanitize_output(text: str) -> str:
    """Remove any PII that might appear in results."""
    sanitized = text
    for pattern in _PII_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="validate_input",
            description="Validate that user input is safe and privacy-compliant",
            inputSchema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        ),
        Tool(
            name="sanitize_output",
            description="Remove sensitive information from output text",
            inputSchema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "validate_input":
        is_valid, message = validate_input(arguments["text"])
        return [TextContent(type="text", text=f'{{"valid": {str(is_valid).lower()}, "message": "{message}"}}')]
    elif name == "sanitize_output":
        result = sanitize_output(arguments["text"])
        return [TextContent(type="text", text=result)]
    raise ValueError(f"Unknown tool: {name}")
