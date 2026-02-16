#!/usr/bin/env bash
# Deploy script for Smart Shopping Agent
# Usage: ./scripts/deploy.sh [local|dev|prod]

set -euo pipefail

ENV="${1:-local}"
echo "Deploying Smart Shopping Agent for environment: $ENV"

# Load environment config
if [ -f "config/.env.$ENV" ]; then
    export $(grep -v '^#' "config/.env.$ENV" | xargs)
else
    echo "Error: config/.env.$ENV not found"
    exit 1
fi

# Install Python dependencies
echo "Installing Python dependencies..."
pip install -e ".[dev]"

# Install Playwright browsers
echo "Installing Playwright browsers..."
playwright install chromium

# Install frontend dependencies
echo "Installing frontend dependencies..."
cd src/frontend
npm install
cd ../..

# Run database migrations (future)
echo "Database setup..."
# TODO: Add Alembic migrations

echo "Deployment complete for $ENV environment"
