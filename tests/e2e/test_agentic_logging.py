"""End-to-end test for the agentic logging convention.

Simulates a multi-level agent workflow using the span helpers from
``src/shared/logging.py`` and validates that the resulting OTEL spans
conform to the hierarchical nesting rules defined in the logging command.

The test launches its own isolated Phoenix instance on a separate port
with temporary storage so only test traces are visible.

How to run::

    source .venv/bin/activate
    pytest tests/e2e/test_agentic_logging.py -v -s

The test pauses after completion and prints a Phoenix URL.
Open it in your browser to inspect the trace tree, then press Enter
in the terminal to shut down Phoenix and finish the test.

Requires ``arize-phoenix``: install with ``pip install -e ".[dashboard]"``.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
)

from src.shared.logging import (
    agent_span,
    operation_span,
    set_session_id,
    set_span_token_counts,
    subagent_span,
    SessionIdSpanProcessor,
)

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

SESSION_ID = "test-agentic-logging-e2e"
TEST_PHOENIX_PORT = 6007

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _InMemoryExporter:
    """Minimal in-memory span exporter that collects finished spans."""

    def __init__(self) -> None:
        self.spans: list[Any] = []

    def export(self, spans: Any) -> Any:
        from opentelemetry.sdk.trace.export import SpanExportResult

        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def _launch_test_phoenix(storage_dir: str) -> tuple[Any, str]:
    """Launch an isolated Phoenix instance for this test run.

    Returns (phoenix_session, ui_url) or (None, "") if Phoenix is not installed.
    """
    try:
        import phoenix as px
    except ImportError:
        return None, ""

    os.environ["PHOENIX_WORKING_DIR"] = storage_dir
    os.environ["PHOENIX_PORT"] = str(TEST_PHOENIX_PORT)
    # Avoid gRPC port conflict with production Phoenix on 4317
    os.environ["PHOENIX_GRPC_PORT"] = str(TEST_PHOENIX_PORT + 10)

    session = px.launch_app(use_temp_dir=False)
    ui_url = f"http://localhost:{TEST_PHOENIX_PORT}"

    # Wait briefly for the server to be ready
    import urllib.request
    for _ in range(10):
        try:
            urllib.request.urlopen(ui_url, timeout=1)
            break
        except Exception:
            time.sleep(0.3)

    return session, ui_url


@pytest.fixture()
def tracing_harness(tmp_path):
    """Set up an isolated TracerProvider with in-memory + Phoenix exporters.

    Launches a dedicated Phoenix instance on port 6007 with temp storage
    so only this test's traces are visible.

    Yields ``(tracer, exporter, phoenix_ui_url)`` and tears down after.
    """
    resource = Resource.create({"service.name": "agentic-logging-test"})
    provider = TracerProvider(resource=resource)

    memory_exporter = _InMemoryExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory_exporter))
    provider.add_span_processor(SessionIdSpanProcessor())

    # Launch isolated Phoenix
    phoenix_dir = str(tmp_path / "phoenix_test_store")
    os.makedirs(phoenix_dir, exist_ok=True)
    phoenix_session, phoenix_ui_url = _launch_test_phoenix(phoenix_dir)

    if phoenix_ui_url:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            otlp_endpoint = f"{phoenix_ui_url}/v1/traces"
            exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        except ImportError:
            phoenix_ui_url = ""

    # Install as global provider so context propagation works
    old_provider = trace.get_tracer_provider()
    trace.set_tracer_provider(provider)

    tracer = provider.get_tracer("test.agentic_logging")

    yield tracer, memory_exporter, phoenix_ui_url

    provider.force_flush()
    provider.shutdown()
    trace.set_tracer_provider(old_provider)

    if phoenix_session is not None:
        try:
            phoenix_session.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Simulated agentic workflow
# ---------------------------------------------------------------------------


def _run_leaf_subagent_c(tracer: trace.Tracer) -> None:
    """SubAgentC — leaf agent with 2 operations, no further delegation."""
    with subagent_span(
        tracer,
        "SubAgentC",
        input="validate final data",
        system_prompt="You validate data for consistency.",
        user_prompt="Check these records for duplicates.",
        model="gpt-4o-mini",
    ) as span:
        with operation_span(tracer, "deduplicate_records", input="records batch") as op:
            time.sleep(0.01)
            op.set_attribute("output", json.dumps({"removed": 2}))
            op.set_attribute("record_count", 10)

        with operation_span(tracer, "format_output", input="clean records") as op:
            time.sleep(0.01)
            op.set_attribute("output", json.dumps({"formatted": True}))

        span.set_attribute("output", json.dumps({"valid": True, "count": 8}))
        set_span_token_counts(span, input_tokens=80, output_tokens=120)


def _run_subagent_b_deep(tracer: trace.Tracer) -> None:
    """SubAgentB — calls SubAgentC, plus its own start/end operations."""
    with subagent_span(
        tracer,
        "SubAgentB",
        input="process intermediate results",
        system_prompt="You process and delegate validation.",
        user_prompt="Process these intermediate results.",
        model="gpt-4o",
    ) as span:
        with operation_span(tracer, "prepare_data", input="raw intermediate") as op:
            time.sleep(0.01)
            op.set_attribute("output", json.dumps({"prepared": True}))

        # Delegate deeper
        _run_leaf_subagent_c(tracer)

        with operation_span(tracer, "summarize", input="validated data") as op:
            time.sleep(0.01)
            op.set_attribute("output", json.dumps({"summary": "all good"}))

        span.set_attribute("output", json.dumps({"processed": True}))
        set_span_token_counts(span, input_tokens=150, output_tokens=200)


def _run_subagent_1(tracer: trace.Tracer) -> None:
    """SubAgent1 — leaf agent with 3 operations."""
    with subagent_span(
        tracer,
        "SubAgent1",
        input="search for products",
        system_prompt="You search the web for products.",
        user_prompt="Find products matching: headphones",
        model="gpt-4o",
    ) as span:
        with operation_span(tracer, "build_query", input="headphones") as op:
            time.sleep(0.01)
            op.set_attribute("output", json.dumps({"query": "best headphones 2025"}))

        with operation_span(tracer, "execute_search", input="best headphones 2025") as op:
            time.sleep(0.02)
            op.set_attribute("output", json.dumps({"results": ["r1", "r2", "r3"]}))
            op.set_attribute("result_count", 3)

        with operation_span(tracer, "filter_results", input="3 results") as op:
            time.sleep(0.01)
            op.set_attribute("output", json.dumps({"filtered": ["r1", "r3"]}))

        span.set_attribute("output", json.dumps({"products": 2}))
        set_span_token_counts(span, input_tokens=200, output_tokens=350)


def _run_subagent_2(tracer: trace.Tracer) -> None:
    """SubAgent2 — calls SubAgentB which calls SubAgentC."""
    with subagent_span(
        tracer,
        "SubAgent2",
        input="scrape and process",
        system_prompt="You scrape pages and delegate processing.",
        user_prompt="Scrape these URLs and process results.",
        model="gpt-4o",
    ) as span:
        with operation_span(tracer, "scrape_pages", input="urls list") as op:
            time.sleep(0.02)
            op.set_attribute("output", json.dumps({"pages_scraped": 3}))

        # Delegate to SubAgentB (which delegates to SubAgentC)
        _run_subagent_b_deep(tracer)

        span.set_attribute("output", json.dumps({"scraped": 3, "processed": True}))
        set_span_token_counts(span, input_tokens=300, output_tokens=500)


def _run_subagent_3(tracer: trace.Tracer) -> None:
    """SubAgent3 — leaf agent with 2 operations."""
    with subagent_span(
        tracer,
        "SubAgent3",
        input="rank products",
        system_prompt="You rank and sort products by relevance.",
        user_prompt="Rank these products by price and quality.",
        model="gpt-4o-mini",
    ) as span:
        with operation_span(tracer, "compute_scores", input="product list") as op:
            time.sleep(0.01)
            op.set_attribute("output", json.dumps({"scores": [0.9, 0.7, 0.5]}))

        with operation_span(tracer, "sort_and_cap", input="scored products") as op:
            time.sleep(0.01)
            op.set_attribute("output", json.dumps({"top": ["p1", "p2"]}))

        span.set_attribute("output", json.dumps({"ranked": 2}))
        set_span_token_counts(span, input_tokens=100, output_tokens=150)


def run_full_agentic_flow(tracer: trace.Tracer) -> None:
    """Simulate the full agentic flow.

    Structure::

        MainAgent
        ├── operation: initialize
        ├── SubAgent1 (leaf: 3 ops)
        ├── SubAgent2
        │   ├── operation: scrape_pages
        │   └── SubAgentB
        │       ├── operation: prepare_data
        │       ├── SubAgentC (leaf: 2 ops)
        │       └── operation: summarize
        ├── SubAgent3 (leaf: 2 ops)
        └── operation: finalize
    """
    set_session_id(SESSION_ID)

    with agent_span(
        tracer,
        "MainAgent",
        input="find best headphones under $200",
        system_prompt="You are a shopping assistant that orchestrates sub-agents.",
        user_prompt="Find the best headphones under $200",
        model="gpt-4o",
    ) as span:
        # Start operation
        with operation_span(tracer, "initialize", input="user query") as op:
            time.sleep(0.01)
            op.set_attribute("output", json.dumps({"parsed": True, "category": "headphones"}))

        # Call 3 sub-agents
        _run_subagent_1(tracer)
        _run_subagent_2(tracer)
        _run_subagent_3(tracer)

        # End operation
        with operation_span(tracer, "finalize", input="all results") as op:
            time.sleep(0.01)
            op.set_attribute("output", json.dumps({"total_products": 5}))

        span.set_attribute("output", json.dumps({"products": 5, "status": "complete"}))
        set_span_token_counts(span, input_tokens=500, output_tokens=800)


# ---------------------------------------------------------------------------
# Span analysis helpers
# ---------------------------------------------------------------------------


def _build_span_index(spans: list) -> dict[str, Any]:
    """Build lookup dicts for span analysis."""
    by_id: dict[str, Any] = {}
    children: dict[str, list[str]] = {}

    for s in spans:
        sid = s.context.span_id
        by_id[sid] = s
        pid = s.parent.span_id if s.parent else None
        if pid:
            children.setdefault(pid, []).append(sid)

    return {"by_id": by_id, "children": children}


def _get_attr(span: Any, key: str) -> Any:
    return span.attributes.get(key)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAgenticLogging:
    """Validate the agentic logging convention end-to-end."""

    def test_full_flow(self, tracing_harness: Any) -> None:
        tracer, exporter, phoenix_ui_url = tracing_harness

        # Run the full simulated flow
        run_full_agentic_flow(tracer)

        # Force flush to ensure all spans are exported
        trace.get_tracer_provider().force_flush()  # type: ignore[union-attr]
        spans = exporter.spans
        assert len(spans) > 0, "No spans were recorded"

        index = _build_span_index(spans)
        by_id = index["by_id"]
        children = index["children"]

        # ----- 1. OTEL format validation -----
        self._validate_otel_format(spans)

        # ----- 2. Hierarchy validation -----
        self._validate_hierarchy(spans, by_id, children)

        # ----- 3. Required attributes validation -----
        self._validate_required_attributes(spans)

        # ----- 4. Timing validation -----
        self._validate_timing(spans, by_id, children)

        # ----- 5. Token count validation -----
        self._validate_token_counts(spans)

        # ----- 6. Session ID validation -----
        self._validate_session_id(spans)

        # ----- Print summary and Phoenix link -----
        self._print_summary(spans, index, phoenix_ui_url)

    # -- Validation methods --

    def _validate_otel_format(self, spans: list) -> None:
        """Every span must have valid trace_id, span_id, name, start/end times."""
        for s in spans:
            assert s.context.trace_id != 0, f"Span '{s.name}' has zero trace_id"
            assert s.context.span_id != 0, f"Span '{s.name}' has zero span_id"
            assert s.name, f"Span has empty name"
            assert s.start_time is not None, f"Span '{s.name}' missing start_time"
            assert s.end_time is not None, f"Span '{s.name}' missing end_time"
            assert s.end_time >= s.start_time, (
                f"Span '{s.name}' end_time < start_time"
            )
            assert s.status is not None, f"Span '{s.name}' missing status"

        # All spans share the same trace_id
        trace_ids = {s.context.trace_id for s in spans}
        assert len(trace_ids) == 1, (
            f"Expected 1 trace_id, got {len(trace_ids)}: spans are not in the same trace"
        )

        # All span_ids are unique
        span_ids = [s.context.span_id for s in spans]
        assert len(span_ids) == len(set(span_ids)), "Duplicate span_ids found"

    def _validate_hierarchy(
        self, spans: list, by_id: dict, children: dict
    ) -> None:
        """Validate parent-child relationships match the expected tree."""
        # Find root span (no parent)
        roots = [s for s in spans if s.parent is None]
        assert len(roots) == 1, f"Expected 1 root span, got {len(roots)}"
        root = roots[0]
        assert root.name == "MainAgent", f"Root span should be MainAgent, got '{root.name}'"

        # MainAgent should have children: initialize, SubAgent1, SubAgent2, SubAgent3, finalize
        root_children = children.get(root.context.span_id, [])
        root_child_names = sorted(by_id[c].name for c in root_children)
        assert "initialize" in root_child_names, f"Missing 'initialize' in root children: {root_child_names}"
        assert "finalize" in root_child_names, f"Missing 'finalize' in root children: {root_child_names}"
        assert "SubAgent1" in root_child_names, f"Missing 'SubAgent1' in root children: {root_child_names}"
        assert "SubAgent2" in root_child_names, f"Missing 'SubAgent2' in root children: {root_child_names}"
        assert "SubAgent3" in root_child_names, f"Missing 'SubAgent3' in root children: {root_child_names}"

        # SubAgent2 should have children: scrape_pages, SubAgentB
        sa2 = next(by_id[c] for c in root_children if by_id[c].name == "SubAgent2")
        sa2_children = children.get(sa2.context.span_id, [])
        sa2_child_names = sorted(by_id[c].name for c in sa2_children)
        assert "scrape_pages" in sa2_child_names, f"Missing 'scrape_pages' in SubAgent2 children: {sa2_child_names}"
        assert "SubAgentB" in sa2_child_names, f"Missing 'SubAgentB' in SubAgent2 children: {sa2_child_names}"

        # SubAgentB should have children: prepare_data, SubAgentC, summarize
        sab = next(by_id[c] for c in sa2_children if by_id[c].name == "SubAgentB")
        sab_children = children.get(sab.context.span_id, [])
        sab_child_names = sorted(by_id[c].name for c in sab_children)
        assert "prepare_data" in sab_child_names
        assert "SubAgentC" in sab_child_names
        assert "summarize" in sab_child_names

        # SubAgentC should have children: deduplicate_records, format_output
        sac = next(by_id[c] for c in sab_children if by_id[c].name == "SubAgentC")
        sac_children = children.get(sac.context.span_id, [])
        sac_child_names = sorted(by_id[c].name for c in sac_children)
        assert "deduplicate_records" in sac_child_names
        assert "format_output" in sac_child_names

        # SubAgent1 should have 3 leaf operations
        sa1 = next(by_id[c] for c in root_children if by_id[c].name == "SubAgent1")
        sa1_children = children.get(sa1.context.span_id, [])
        sa1_child_names = sorted(by_id[c].name for c in sa1_children)
        assert len(sa1_child_names) == 3, f"SubAgent1 should have 3 ops, got {sa1_child_names}"

        # SubAgent3 should have 2 leaf operations
        sa3 = next(by_id[c] for c in root_children if by_id[c].name == "SubAgent3")
        sa3_children = children.get(sa3.context.span_id, [])
        assert len(sa3_children) == 2, f"SubAgent3 should have 2 ops, got {len(sa3_children)}"

        # No operation span should be a root
        operation_spans = [s for s in spans if _get_attr(s, "operation.name")]
        for s in operation_spans:
            assert s.parent is not None, (
                f"Operation span '{s.name}' must not be a root span"
            )

    def _validate_required_attributes(self, spans: list) -> None:
        """Validate that each span type carries its required attributes."""
        for s in spans:
            # Agent spans (top-level or sub-agent)
            if _get_attr(s, "agent.name"):
                assert _get_attr(s, "agent.name"), f"Span '{s.name}' missing agent.name"
                assert _get_attr(s, "agent.input"), f"Agent span '{s.name}' missing agent.input"
                assert _get_attr(s, "output") is not None, f"Agent span '{s.name}' missing output"

                # LLM attributes required for agent spans
                assert _get_attr(s, "llm.system_prompt"), (
                    f"Agent span '{s.name}' missing llm.system_prompt"
                )
                assert _get_attr(s, "llm.user_prompt"), (
                    f"Agent span '{s.name}' missing llm.user_prompt"
                )
                assert _get_attr(s, "llm.model"), (
                    f"Agent span '{s.name}' missing llm.model"
                )
                assert _get_attr(s, "llm.token_count.prompt") is not None, (
                    f"Agent span '{s.name}' missing llm.token_count.prompt"
                )
                assert _get_attr(s, "llm.token_count.completion") is not None, (
                    f"Agent span '{s.name}' missing llm.token_count.completion"
                )
                assert _get_attr(s, "llm.token_count.total") is not None, (
                    f"Agent span '{s.name}' missing llm.token_count.total"
                )

            # Sub-agent spans carry additional attributes
            if _get_attr(s, "subagent.name"):
                assert _get_attr(s, "subagent.parent"), (
                    f"Subagent span '{s.name}' missing subagent.parent"
                )
                # Must have subagent.start and subagent.end events
                event_names = [e.name for e in s.events]
                assert "subagent.start" in event_names, (
                    f"Subagent span '{s.name}' missing subagent.start event"
                )
                assert "subagent.end" in event_names, (
                    f"Subagent span '{s.name}' missing subagent.end event"
                )

            # Operation spans
            if _get_attr(s, "operation.name"):
                assert _get_attr(s, "operation.input"), (
                    f"Operation span '{s.name}' missing operation.input"
                )
                assert _get_attr(s, "output") is not None, (
                    f"Operation span '{s.name}' missing output"
                )

    def _validate_timing(
        self, spans: list, by_id: dict, children: dict
    ) -> None:
        """Parent span must start before and end after all its children."""
        for parent_id, child_ids in children.items():
            parent = by_id[parent_id]
            for child_id in child_ids:
                child = by_id[child_id]
                assert parent.start_time <= child.start_time, (
                    f"Parent '{parent.name}' starts after child '{child.name}'"
                )
                assert parent.end_time >= child.end_time, (
                    f"Parent '{parent.name}' ends before child '{child.name}'"
                )

    def _validate_token_counts(self, spans: list) -> None:
        """Token counts must be positive integers and total = prompt + completion."""
        for s in spans:
            prompt = _get_attr(s, "llm.token_count.prompt")
            completion = _get_attr(s, "llm.token_count.completion")
            total = _get_attr(s, "llm.token_count.total")
            if prompt is not None or completion is not None:
                assert prompt > 0, f"Span '{s.name}' has non-positive prompt tokens"
                assert completion > 0, f"Span '{s.name}' has non-positive completion tokens"
                assert total == prompt + completion, (
                    f"Span '{s.name}' total ({total}) != prompt ({prompt}) + completion ({completion})"
                )

    def _validate_session_id(self, spans: list) -> None:
        """All spans must carry session.id attribute."""
        for s in spans:
            sid = _get_attr(s, "session.id")
            assert sid == SESSION_ID, (
                f"Span '{s.name}' session.id is '{sid}', expected '{SESSION_ID}'"
            )

    def _print_summary(
        self, spans: list, index: dict, phoenix_ui_url: str
    ) -> None:
        """Print a human-readable summary and Phoenix link."""
        by_id = index["by_id"]
        children = index["children"]

        print("\n" + "=" * 70)
        print("AGENTIC LOGGING E2E TEST — SPAN TREE")
        print("=" * 70)

        root = next(s for s in spans if s.parent is None)

        def _print_tree(span_id: str, depth: int = 0) -> None:
            span = by_id[span_id]
            prefix = "│   " * depth + ("├── " if depth > 0 else "")
            span_type = "agent" if _get_attr(span, "agent.name") else "operation"
            if _get_attr(span, "subagent.name"):
                span_type = "subagent"
            duration_ms = (span.end_time - span.start_time) / 1e6
            print(f"{prefix}[{span_type}] {span.name} ({duration_ms:.1f}ms)")
            for child_id in children.get(span_id, []):
                _print_tree(child_id, depth + 1)

        _print_tree(root.context.span_id)

        print(f"\nTotal spans: {len(spans)}")
        print(f"Trace ID: {format(root.context.trace_id, '032x')}")

        agent_spans = [s for s in spans if _get_attr(s, "agent.name")]
        op_spans = [s for s in spans if _get_attr(s, "operation.name")]
        subagent_spans = [s for s in spans if _get_attr(s, "subagent.name")]
        print(f"Agent spans: {len(agent_spans)} (top-level: 1, sub-agents: {len(subagent_spans)})")
        print(f"Operation spans: {len(op_spans)}")

        total_tokens = sum(
            _get_attr(s, "llm.token_count.total") or 0
            for s in spans
        )
        print(f"Total tokens across all agents: {total_tokens}")

        if phoenix_ui_url:
            trace_hex = format(root.context.trace_id, "032x")
            print(f"\n>>> Phoenix test instance (isolated, test-only traces):")
            print(f">>> Open: {phoenix_ui_url}")
            print(f">>> Trace ID: {trace_hex}")
            print("=" * 70)
            input("\nPress Enter to shut down Phoenix and finish the test...")
        else:
            print(f"\n>>> Phoenix (arize-phoenix) is not installed.")
            print(f">>> Install with: pip install -e \".[dashboard]\"")
            print(f">>> Then re-run this test to see the trace tree visually.")
            print("=" * 70)
