#!/usr/bin/env bash
# Check that staged files belong to a single sub-agent scope.
# Used as a Claude Code PreCommit hook.

set -euo pipefail

STAGED=$(git diff --cached --name-only)

if [ -z "$STAGED" ]; then
    exit 0
fi

SCOPES=""

if echo "$STAGED" | grep -qE '^src/(backend|agents)/'; then
    SCOPES="${SCOPES}backend "
fi

if echo "$STAGED" | grep -qE '^src/frontend/'; then
    SCOPES="${SCOPES}frontend "
fi

if echo "$STAGED" | grep -qE '^src/mcp_servers/'; then
    SCOPES="${SCOPES}mcp "
fi

if echo "$STAGED" | grep -qE '^tests/'; then
    SCOPES="${SCOPES}testing "
fi

if echo "$STAGED" | grep -qE '^scripts/'; then
    SCOPES="${SCOPES}deploy "
fi

if echo "$STAGED" | grep -qE '^evals/'; then
    SCOPES="${SCOPES}eval "
fi

SCOPE_COUNT=$(echo "$SCOPES" | wc -w | tr -d ' ')

if [ "$SCOPE_COUNT" -gt 1 ]; then
    echo "WARNING: Mixed sub-agent scopes detected in staged files: ${SCOPES}"
    echo "         Consider splitting into separate commits (one per sub-agent)."
    echo "         Staged files:"
    echo "$STAGED" | sed 's/^/           /'
fi
