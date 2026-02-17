# Smart Shopping Agent

## Project Overview
An agentic app that helps users shop online by discovering products, comparing prices, and matching multi-product sets. Uses adaptive web scraping (no hardcoded site logic) with MCP-based architecture.

For the full product guideline including example tasks, UI specs, technical architecture, MVP phases, and agent team structure, see [docs/product_guideline.md](docs/product_guideline.md).

## Tech Stack
- **Backend**: Python 3.12+, FastAPI, SQLAlchemy ORM, SQLite (dev) / PostgreSQL (prod)
- **Frontend**: Next.js 14 (App Router), TypeScript, WebSocket for real-time updates
- **Agent Framework**: MCP (Model Context Protocol) servers
- **Browser Automation**: Playwright (headless)
- **Testing**: pytest (backend), Jest + Playwright (frontend e2e)
- **Logging**: OpenTelemetry traces/spans

## Project Structure
```
/scripts          - Deployment, testing, evaluation, cache management
/config           - Environment-specific configuration files
/src
  /mcp_servers    - Each MCP server in its own directory
    /io_validator_mcp
    /web_search_mcp
    /web_scraper_mcp
    /product_criteria_mcp
    /results_processor_mcp
    /negotiator_mcp (future)
  /agents         - Main orchestrator agent and prompts
  /backend        - FastAPI app (API, DB, WebSocket)
  /frontend       - Next.js app
  /shared         - Shared models and utilities
/tests
  /unit           - Backend unit tests (100% coverage required)
  /e2e            - Frontend e2e tests (Jest + Playwright)
  /mcp            - Per-MCP server tests
/evals
  /test_cases     - JSON test scenarios
  /results        - Generated evaluation reports
/logs             - Local logs (gitignored)
```

## Key Design Principles
1. **Adaptive Web Automation** - No hardcoded site-specific logic. Agent discovers, learns, and caches scraping strategies.
2. **Modular MCP Architecture** - Each MCP server is independently developable and testable.
3. **Database Portability** - Always use SQLAlchemy ORM so SQLite and PostgreSQL are interchangeable.
4. **Cache Strategy** - Hash-based cache keys: `hash(input + mcp_version)`. TTL-based expiration.

## Development Rules
- All settings via environment variables (see `/config`)
- Shared `session_id` across all components for end-to-end tracing
- Every new backend function needs a corresponding unit test
- Run `pytest` before committing backend changes
- Run `npm test` in `/src/frontend` before committing frontend changes
- If agent files changed, run evaluations before merging

## Git Conventions
- **Branches**: `main` (prod), `develop` (dev baseline), `feature/<module>/<feature>`, `fix/<module>/<issue>`
- **Module Tags**: API, WebApp, MCP-Search, MCP-Scraper, Agent, etc.
- **Commits**: Clear description + test plan (what tests were run)
- Never commit directly to `main`

## Environment Setup
```bash
# Backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Frontend
cd src/frontend
npm install

# Playwright
playwright install
```

## Running the App
```bash
# Backend server
uvicorn src.backend.main:app --reload --port 8000

# Frontend dev server
cd src/frontend && npm run dev
```

## Running Tests
```bash
# Backend unit tests
pytest tests/unit -v

# MCP server tests
pytest tests/mcp -v

# Frontend e2e tests
cd src/frontend && npm test

# Evaluations
python evals/eval_agent.py
```

## Sub-Agents
The following custom slash commands are available as sub-agents. Use them when their scope matches the task:

- **`/project:review`** — Code review agent. Run before every commit to check staged changes for security issues, missing tests, and convention violations.
- **`/project:deploy-agent`** — Deployment and DevOps agent. Use for writing or fixing shell scripts in `scripts/`, environment setup issues, process management, and build/release automation.

## Commit Workflow
Before creating any commit, **always** run `/project:review` first. This launches a review sub-agent that analyzes staged changes for security issues, missing tests, and project convention violations. Only proceed with the commit if the review passes (no errors). Warnings are informational and do not block commits.

## Languages & Markets
- Primary: Hebrew (RTL), English
- Secondary: Arabic (RTL)
- Currencies: NIS (₪), USD ($)
