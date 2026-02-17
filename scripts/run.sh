#!/usr/bin/env bash
# Set up environment and run the app locally.
# Usage: ./scripts/run.sh [--skip-setup]

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

SKIP_SETUP=false
if [ "${1:-}" = "--skip-setup" ]; then
    SKIP_SETUP=true
fi

# Load local environment config
if [ -f "config/.env.local" ]; then
    set -a
    source config/.env.local
    set +a
else
    echo "Warning: config/.env.local not found, using defaults"
fi

PORT="${BACKEND_PORT:-8000}"

# --- Setup ---
if [ "$SKIP_SETUP" = false ]; then
    echo "=== Setting up environment ==="

    # Python virtual environment
    if [ ! -d ".venv" ]; then
        echo "Creating Python virtual environment..."
        python3 -m venv .venv
    fi
    source .venv/bin/activate

    echo "Upgrading pip and setuptools..."
    pip install --upgrade pip setuptools --quiet

    echo "Installing Python dependencies..."
    pip install -e ".[dev]" --quiet

    echo "Installing Playwright browsers..."
    playwright install chromium --with-deps 2>/dev/null || playwright install chromium

    # Frontend dependencies
    echo "Installing frontend dependencies..."
    cd src/frontend
    npm install --silent
    cd "$REPO_ROOT"

    echo "=== Setup complete ==="
else
    source .venv/bin/activate
    echo "=== Skipping setup (--skip-setup) ==="
fi

# --- Run ---
echo ""
echo "Starting Smart Shopping Agent..."
echo "  Backend:  http://localhost:$PORT"
echo "  Frontend: http://localhost:3000"
echo ""

# Start backend in background
uvicorn src.backend.main:app --reload --host "${BACKEND_HOST:-0.0.0.0}" --port "$PORT" &
BACKEND_PID=$!

# Start frontend in background
cd src/frontend
npm run dev &
FRONTEND_PID=$!
cd "$REPO_ROOT"

# Trap to clean up both processes on exit
cleanup() {
    echo ""
    echo "Shutting down..."
    kill "$BACKEND_PID" 2>/dev/null || true
    kill "$FRONTEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
    wait "$FRONTEND_PID" 2>/dev/null || true
    echo "Done."
}
trap cleanup EXIT INT TERM

# Wait for either process to exit
wait
