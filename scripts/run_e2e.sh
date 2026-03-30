#!/usr/bin/env bash
# Run e2e tests with all required services.
#
# Usage:
#   ./scripts/run_e2e.sh [test_name]              # ensure services, run test
#   ./scripts/run_e2e.sh --test-only [test_name]   # skip service management, just run test
#
# Examples:
#   ./scripts/run_e2e.sh test_five_model_kitchen_bundle_il
#   ./scripts/run_e2e.sh --test-only test_five_model_kitchen_bundle_il
#   ./scripts/run_e2e.sh                           # run all e2e tests

set -euo pipefail

cd "$(dirname "$0")/.."

SKIP_SERVICES=false
TEST_NAME=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --test-only) SKIP_SERVICES=true; shift ;;
        *) TEST_NAME="$1"; shift ;;
    esac
done

BACKEND_PID=""
PHOENIX_PID=""

cleanup() {
    echo ""
    echo "Cleaning up..."
    [[ -n "$BACKEND_PID" ]] && kill "$BACKEND_PID" 2>/dev/null && echo "Stopped backend (pid $BACKEND_PID)"
    [[ -n "$PHOENIX_PID" ]] && kill "$PHOENIX_PID" 2>/dev/null && echo "Stopped Phoenix (pid $PHOENIX_PID)"
    exit
}
trap cleanup EXIT INT TERM

# Activate venv
if [[ -f .venv/bin/activate ]]; then
    source .venv/bin/activate
else
    echo "ERROR: .venv not found. Run: python -m venv .venv && pip install -e '.[dev]'"
    exit 1
fi

# Force OTEL traces to local Phoenix (override any corporate/system endpoint)
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:6006/v1/traces"

if [[ "$SKIP_SERVICES" == true ]]; then
    # Preflight: verify services are reachable
    if ! curl -s http://localhost:8000/api/health | grep -q '"ok"' 2>/dev/null; then
        echo "ERROR: Backend not running on port 8000. Start it or run without --test-only."
        exit 1
    fi
    echo "Backend: OK"
    if curl -s http://localhost:6006 &>/dev/null; then
        echo "Phoenix: OK"
    else
        echo "Phoenix: not running (traces won't be recorded)"
    fi
else
    # Check Playwright browsers
    if ! playwright install --dry-run chromium &>/dev/null; then
        echo "Installing Playwright browsers..."
        playwright install
    fi

    # Start Phoenix first (backend needs it for OTEL export)
    if curl -s http://localhost:6006 &>/dev/null; then
        echo "Phoenix already running."
    else
        echo "Starting Phoenix..."
        python -m phoenix.server.main serve &>/dev/null &
        PHOENIX_PID=$!
        sleep 2
    fi

    # Start backend (after Phoenix so traces connect)
    if curl -s http://localhost:8000/api/health | grep -q '"ok"' 2>/dev/null; then
        echo "Backend already running on port 8000."
    else
        echo "Starting backend on port 8000..."
        uvicorn src.backend.main:app --port 8000 --log-level warning &
        BACKEND_PID=$!
        sleep 3

        if ! curl -s http://localhost:8000/api/health | grep -q '"ok"'; then
            echo "ERROR: Backend failed to start."
            exit 1
        fi
        echo "Backend is ready."
    fi
fi

# Build test selector
if [[ -n "$TEST_NAME" ]]; then
    TEST_TARGET="tests/e2e/test_price_search.py::${TEST_NAME}"
else
    TEST_TARGET="tests/e2e/test_price_search.py"
fi

echo ""
echo "Running: pytest $TEST_TARGET"
echo "=========================================="

pytest "$TEST_TARGET" -v -s -m e2e --timeout=300 --no-cov
