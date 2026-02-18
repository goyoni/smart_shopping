"""Tests for the shared logging and tracing infrastructure."""

from __future__ import annotations

import json
import logging

import pytest

# Reset module-level initialisation flags before each test so that
# setup_logging / _init_tracer_provider can be re-exercised.


@pytest.fixture(autouse=True)
def _reset_logging_state():
    """Reset the shared logging module's initialisation flags."""
    import src.shared.logging as log_mod

    log_mod._initialized = False
    log_mod._tracer_initialized = False
    # Clear any session ID left over from a previous test
    log_mod._session_id_var.set("")
    yield
    # Restore root logger to avoid polluting other tests
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Logger factory
# ---------------------------------------------------------------------------


def test_get_logger_returns_logger():
    from src.shared.logging import get_logger

    logger = get_logger("test")
    assert isinstance(logger, logging.Logger)
    assert logger.name == "test"


def test_setup_logging_idempotent():
    from src.shared.logging import setup_logging

    setup_logging()
    handler_count = len(logging.getLogger().handlers)

    setup_logging()
    assert len(logging.getLogger().handlers) == handler_count


# ---------------------------------------------------------------------------
# Session ID context
# ---------------------------------------------------------------------------


def test_session_id_context():
    from src.shared.logging import get_session_id, set_session_id

    set_session_id("abc")
    assert get_session_id() == "abc"


def test_session_id_default():
    from src.shared.logging import get_session_id

    assert get_session_id() == ""


# ---------------------------------------------------------------------------
# SessionFilter
# ---------------------------------------------------------------------------


def test_session_filter_injects_id():
    from src.shared.logging import SessionFilter, set_session_id

    set_session_id("sess-42")
    filt = SessionFilter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="hello", args=(), exc_info=None,
    )
    result = filt.filter(record)
    assert result is True
    assert record.session_id == "sess-42"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# JsonFormatter
# ---------------------------------------------------------------------------


def test_json_formatter_output():
    from src.shared.logging import JsonFormatter, SessionFilter, set_session_id

    set_session_id("json-test")
    formatter = JsonFormatter()
    filt = SessionFilter()
    record = logging.LogRecord(
        name="mylogger", level=logging.INFO, pathname="", lineno=0,
        msg="test message", args=(), exc_info=None,
    )
    filt.filter(record)
    output = formatter.format(record)
    data = json.loads(output)
    assert data["level"] == "INFO"
    assert data["logger"] == "mylogger"
    assert data["message"] == "test message"
    assert data["session_id"] == "json-test"
    assert "timestamp" in data
    assert "exception" not in data


def test_json_formatter_with_exception():
    from src.shared.logging import JsonFormatter, SessionFilter

    formatter = JsonFormatter()
    filt = SessionFilter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="exc", level=logging.ERROR, pathname="", lineno=0,
        msg="err", args=(), exc_info=exc_info,
    )
    filt.filter(record)
    output = formatter.format(record)
    data = json.loads(output)
    assert "exception" in data
    assert "boom" in data["exception"]


# ---------------------------------------------------------------------------
# ConsoleFormatter
# ---------------------------------------------------------------------------


def test_console_formatter_output():
    from src.shared.logging import ConsoleFormatter, SessionFilter, set_session_id

    set_session_id("console-test")
    formatter = ConsoleFormatter()
    filt = SessionFilter()
    record = logging.LogRecord(
        name="mylogger", level=logging.INFO, pathname="", lineno=0,
        msg="hello world", args=(), exc_info=None,
    )
    filt.filter(record)
    output = formatter.format(record)
    assert "INFO" in output
    assert "[console-test]" in output
    assert "mylogger" in output
    assert "hello world" in output


def test_console_formatter_no_session():
    from src.shared.logging import ConsoleFormatter, SessionFilter

    formatter = ConsoleFormatter()
    filt = SessionFilter()
    record = logging.LogRecord(
        name="mylogger", level=logging.DEBUG, pathname="", lineno=0,
        msg="no session", args=(), exc_info=None,
    )
    filt.filter(record)
    output = formatter.format(record)
    assert "[" not in output or "[]" not in output
    assert "mylogger" in output
    assert "no session" in output


# ---------------------------------------------------------------------------
# OpenTelemetry tracer
# ---------------------------------------------------------------------------


def test_get_tracer_returns_tracer():
    from opentelemetry import trace

    from src.shared.logging import get_tracer

    tracer = get_tracer("test")
    assert isinstance(tracer, trace.Tracer)


# ---------------------------------------------------------------------------
# Log level from settings
# ---------------------------------------------------------------------------


def test_log_level_from_settings(monkeypatch):
    from src.shared import config
    from src.shared.logging import setup_logging

    monkeypatch.setattr(config.settings, "log_level", "WARNING")
    setup_logging()
    root = logging.getLogger()
    assert root.level == logging.WARNING


# ---------------------------------------------------------------------------
# SessionIdSpanProcessor
# ---------------------------------------------------------------------------


def test_session_id_span_processor_sets_attribute():
    """Verify session.id is set on a span when a session ID is active."""
    from unittest.mock import MagicMock

    from src.shared.logging import SessionIdSpanProcessor, set_session_id

    set_session_id("test-session-42")
    processor = SessionIdSpanProcessor()
    span = MagicMock()
    processor.on_start(span)
    span.set_attribute.assert_called_once_with("session.id", "test-session-42")


def test_session_id_span_processor_no_session():
    """No attribute should be set when session ID is empty."""
    from unittest.mock import MagicMock

    from src.shared.logging import SessionIdSpanProcessor

    processor = SessionIdSpanProcessor()
    span = MagicMock()
    processor.on_start(span)
    span.set_attribute.assert_not_called()


def test_session_id_in_exported_spans():
    """End-to-end: session.id appears in spans captured by a collecting exporter."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        SimpleSpanProcessor,
        SpanExporter,
        SpanExportResult,
    )

    from src.shared.logging import SessionIdSpanProcessor, set_session_id

    class CollectingExporter(SpanExporter):
        def __init__(self):
            self.spans = []

        def export(self, spans):
            self.spans.extend(spans)
            return SpanExportResult.SUCCESS

    exporter = CollectingExporter()
    provider = TracerProvider()
    provider.add_span_processor(SessionIdSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    tracer = provider.get_tracer("test")
    set_session_id("e2e-session")

    with tracer.start_as_current_span("test_span"):
        pass

    assert len(exporter.spans) == 1
    attrs = dict(exporter.spans[0].attributes or {})
    assert attrs["session.id"] == "e2e-session"

    provider.shutdown()


# ---------------------------------------------------------------------------
# shutdown_tracing
# ---------------------------------------------------------------------------


def test_shutdown_tracing_calls_provider_shutdown():
    """shutdown_tracing should call provider.shutdown() when available."""
    from unittest.mock import MagicMock, patch

    from src.shared.logging import shutdown_tracing

    mock_provider = MagicMock()
    with patch("src.shared.logging.trace.get_tracer_provider", return_value=mock_provider):
        shutdown_tracing()
    mock_provider.shutdown.assert_called_once()
