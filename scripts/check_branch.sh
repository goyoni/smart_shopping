#!/usr/bin/env bash
# Block commits directly to the main branch.
# Used as a Claude Code PreCommit hook.

set -euo pipefail

BRANCH=$(git rev-parse --abbrev-ref HEAD)

if [ "$BRANCH" = "main" ]; then
    echo "ERROR: Direct commits to 'main' are not allowed."
    echo "Create a feature branch first: git checkout -b feature/<module>/<feature>"
    exit 1
fi
