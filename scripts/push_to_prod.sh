#!/usr/bin/env bash
# Merge a branch into main and push to production.
# Usage: ./scripts/push_to_prod.sh [branch]
# Default branch: develop

set -euo pipefail

BRANCH="${1:-develop}"
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)

echo "=== Push to Production ==="
echo "Merging '$BRANCH' into 'main'..."

# Ensure working tree is clean
if [ -n "$(git status --porcelain)" ]; then
    echo "ERROR: Working tree is not clean. Commit or stash changes first."
    exit 1
fi

# Fetch latest from remote
git fetch origin

# Check that the source branch exists
if ! git rev-parse --verify "$BRANCH" >/dev/null 2>&1; then
    echo "ERROR: Branch '$BRANCH' does not exist."
    exit 1
fi

# Switch to main and pull latest
git checkout main
git pull origin main

# Merge the source branch
echo "Merging '$BRANCH' into 'main'..."
if ! git merge "$BRANCH" --no-edit; then
    echo "ERROR: Merge conflict. Resolve conflicts, then run again."
    exit 1
fi

# Push to remote
git push origin main

echo "=== '$BRANCH' merged into 'main' and pushed to origin ==="

# Return to the original branch
git checkout "$CURRENT_BRANCH"
