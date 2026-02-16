#!/usr/bin/env bash
# Run agent evaluations as a warning-only check.
# Always exits 0 so it doesn't block commits.
# Will be tightened once real implementations exist.

set -uo pipefail

cd "$(git rev-parse --show-toplevel)"

echo "=== Running Agent Evaluations (warning-only) ==="
python evals/eval_agent.py 2>&1 || true
echo "=== Evals complete (warnings above are informational) ==="

exit 0
