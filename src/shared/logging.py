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


# ---------------------------------------------------------------------------
# Agentic span helpers
# ---------------------------------------------------------------------------

import contextlib
from collections.abc import Generator
from typing import Any

_SPAN_NAME_INPUT_MAX = 40


def _span_display_name(name: str, input_preview: str) -> str:
    """Build a span name with a truncated input preview for trace tree readability.

    Example: ``"MainAgent: find best headphones under $2..."``
    """
    if not input_preview:
        return name
    preview = input_preview.replace("\n", " ").strip()
    if len(preview) > _SPAN_NAME_INPUT_MAX:
        preview = preview[:_SPAN_NAME_INPUT_MAX] + "..."
    return f"{name}: {preview}"


@contextlib.contextmanager
def agent_span(
    tracer: trace.Tracer,
    agent_name: str,
    *,
    input: str = "",
    system_prompt: str = "",
    user_prompt: str = "",
    model: str = "",
    **extra_attributes: Any,
) -> Generator[trace.Span, None, None]:
    """Top-level span for an agent's entire unit of work.

    Usage::

        with agent_span(tracer, "MainAgent", input=query, model="gpt-4") as span:
            # ... agent work ...
            span.set_attribute("output", result_json)
            set_span_token_counts(span, input_tokens=120, output_tokens=340)

    The span automatically records ``start_time`` and ``end_time``.  On
    exception the span is marked ERROR and the exception is recorded.

    Attributes set automatically:
        - ``agent.name``
        - ``agent.input`` (if provided)
        - ``llm.system_prompt`` / ``llm.user_prompt`` / ``llm.model`` (if provided)
    """
    attrs: dict[str, Any] = {"agent.name": agent_name}
    if input:
        attrs["agent.input"] = input
    if system_prompt:
        attrs["llm.system_prompt"] = system_prompt
    if user_prompt:
        attrs["llm.user_prompt"] = user_prompt
    if model:
        attrs["llm.model"] = model
    attrs.update(extra_attributes)

    display_name = _span_display_name(agent_name, input)
    with tracer.start_as_current_span(display_name, attributes=attrs) as span:
        try:
            yield span
        except Exception as exc:
            span.set_status(trace.StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise


@contextlib.contextmanager
def operation_span(
    tracer: trace.Tracer,
    operation_name: str,
    *,
    input: str = "",
    **extra_attributes: Any,
) -> Generator[trace.Span, None, None]:
    """Child span for a single operation within an agent's workflow.

    Automatically becomes a child of the current active span (set by
    ``agent_span`` or another ``operation_span``).

    Usage::

        with operation_span(tracer, "search_web", input=query) as span:
            results = await search(query)
            span.set_attribute("output", json.dumps(results))
            span.set_attribute("result_count", len(results))

    On exception the span is marked ERROR and the exception is recorded.
    """
    attrs: dict[str, Any] = {"operation.name": operation_name}
    if input:
        attrs["operation.input"] = input
    attrs.update(extra_attributes)

    display_name = _span_display_name(operation_name, input)
    with tracer.start_as_current_span(display_name, attributes=attrs) as span:
        try:
            yield span
        except Exception as exc:
            span.set_status(trace.StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise


@contextlib.contextmanager
def subagent_span(
    tracer: trace.Tracer,
    subagent_name: str,
    *,
    input: str = "",
    system_prompt: str = "",
    user_prompt: str = "",
    model: str = "",
    **extra_attributes: Any,
) -> Generator[trace.Span, None, None]:
    """Span for a sub-agent invocation within a parent agent's workflow.

    Works like ``agent_span`` but carries ``subagent.name`` and
    ``subagent.parent`` attributes to make the delegation visible in
    trace viewers.

    The sub-agent should use ``operation_span`` for its own child
    operations — those will nest under this span automatically.
    """
    parent = trace.get_current_span()
    parent_name = ""
    if parent and parent.is_recording():
        parent_name = parent.name

    attrs: dict[str, Any] = {
        "agent.name": subagent_name,
        "subagent.name": subagent_name,
        "subagent.parent": parent_name,
    }
    if input:
        attrs["agent.input"] = input
    if system_prompt:
        attrs["llm.system_prompt"] = system_prompt
    if user_prompt:
        attrs["llm.user_prompt"] = user_prompt
    if model:
        attrs["llm.model"] = model
    attrs.update(extra_attributes)

    display_name = _span_display_name(subagent_name, input)
    with tracer.start_as_current_span(display_name, attributes=attrs) as span:
        span.add_event("subagent.start", {"subagent.name": subagent_name})
        try:
            yield span
        except Exception as exc:
            span.set_status(trace.StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise
        finally:
            span.add_event("subagent.end", {"subagent.name": subagent_name})


def set_span_token_counts(
    span: trace.Span,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    """Set LLM token count attributes on a span.

    Uses OpenInference semantic conventions so Phoenix/Arize can display
    token usage correctly.
    """
    if input_tokens:
        span.set_attribute("llm.token_count.prompt", input_tokens)
    if output_tokens:
        span.set_attribute("llm.token_count.completion", output_tokens)
    if input_tokens or output_tokens:
        span.set_attribute(
            "llm.token_count.total", input_tokens + output_tokens,
        )
