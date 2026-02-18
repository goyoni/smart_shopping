"""Shared logging and tracing infrastructure.

Provides centralized logger and tracer factories with session ID
context propagation, environment-aware formatting, and OpenTelemetry
integration.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
from datetime import datetime, timezone

from opentelemetry import trace
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider

from src.shared.config import settings

# ---------------------------------------------------------------------------
# Session ID context
# ---------------------------------------------------------------------------

_session_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "session_id", default=""
)


def set_session_id(session_id: str) -> None:
    """Set the session ID in the current async context."""
    _session_id_var.set(session_id)


def get_session_id() -> str:
    """Retrieve the session ID from the current async context."""
    return _session_id_var.get()


# ---------------------------------------------------------------------------
# Logging classes
# ---------------------------------------------------------------------------

class SessionFilter(logging.Filter):
    """Inject ``session_id`` from *contextvars* into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = _session_id_var.get()  # type: ignore[attr-defined]
        return True


class JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON object on one line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, str] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "session_id": getattr(record, "session_id", ""),
        }
        if record.exc_info and record.exc_info[1] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


_LEVEL_COLORS: dict[str, str] = {
    "DEBUG": "\033[36m",     # cyan
    "INFO": "\033[32m",      # green
    "WARNING": "\033[33m",   # yellow
    "ERROR": "\033[31m",     # red
    "CRITICAL": "\033[35m",  # magenta
}
_RESET = "\033[0m"


class ConsoleFormatter(logging.Formatter):
    """Colorized console output with optional session prefix."""

    def format(self, record: logging.LogRecord) -> str:
        color = _LEVEL_COLORS.get(record.levelname, "")
        session_id: str = getattr(record, "session_id", "")
        session_part = f" [{session_id}]" if session_id else ""
        msg = record.getMessage()
        line = f"{color}{record.levelname}{_RESET}{session_part} {record.name}: {msg}"
        if record.exc_info and record.exc_info[1] is not None:
            line += "\n" + self.formatException(record.exc_info)
        return line


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

_initialized = False


def setup_logging() -> None:
    """One-time logging initialisation (idempotent).

    * Sets root logger level from ``settings.log_level``
    * Clears existing handlers, adds a single ``StreamHandler(stderr)``
    * Attaches :class:`SessionFilter` and the appropriate formatter
    * Quiets noisy third-party libraries
    """
    global _initialized  # noqa: PLW0603
    if _initialized:
        return
    _initialized = True

    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.DEBUG))

    # Clear default handlers
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(SessionFilter())

    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(ConsoleFormatter())

    root.addHandler(handler)

    # Quiet noisy libraries
    for lib in ("sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(lib).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a stdlib logger, ensuring the shared setup has run."""
    setup_logging()
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# OpenTelemetry tracer
# ---------------------------------------------------------------------------

_tracer_initialized = False


class SessionIdSpanProcessor(SpanProcessor):
    """Stamp ``session.id`` on every span from the contextvars session ID.

    Phoenix (and the OpenInference convention) uses the ``session.id``
    attribute to group spans into user sessions.
    """

    def on_start(self, span: trace.Span, parent_context: object = None) -> None:  # type: ignore[override]
        session_id = _session_id_var.get()
        if session_id:
            span.set_attribute("session.id", session_id)

    def on_end(self, span: trace.Span) -> None:  # type: ignore[override]
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def _init_tracer_provider() -> None:
    """One-time tracer provider initialisation (idempotent)."""
    global _tracer_initialized  # noqa: PLW0603
    if _tracer_initialized:
        return
    _tracer_initialized = True

    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": "smart-shopping-agent"})
    provider = TracerProvider(resource=resource)

    # Always add session ID processor first
    provider.add_span_processor(SessionIdSpanProcessor())

    # Determine OTLP endpoint
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or getattr(
        settings, "otel_exporter_endpoint", ""
    )

    if not endpoint and settings.phoenix_enabled:
        endpoint = f"http://localhost:{settings.phoenix_port}/v1/traces"

    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        exporter = OTLPSpanExporter(endpoint=endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))

    if settings.log_format == "console" and not endpoint:
        # Console-only mode when no OTLP endpoint is configured
        from opentelemetry.sdk.trace.export import (
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )

        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)


def shutdown_tracing() -> None:
    """Flush pending spans and shut down the tracer provider."""
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()


def get_tracer(name: str) -> trace.Tracer:
    """Return an OpenTelemetry tracer, ensuring the provider is initialised."""
    _init_tracer_provider()
    return trace.get_tracer(name)
