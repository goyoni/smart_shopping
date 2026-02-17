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

The following custom slash commands are available as specialized sub-agents. Use `/project:coordinator` for multi-domain tasks, or invoke a specific agent directly when the task clearly falls within one domain.

### Agent Roster

| Agent | Command | Scope |
|-------|---------|-------|
| **Coordinator** | `/project:coordinator` | Orchestrates team, breaks tasks into subtasks, delegates to agents |
| **Frontend** | `/project:frontend` | Next.js 14 App Router, TypeScript, RTL, responsive design, WebSocket client |
| **Backend** | `/project:backend` | FastAPI, SQLAlchemy ORM, API endpoints, WebSocket server, session management |
| **MCP** | `/project:mcp` | MCP servers, adaptive scraping, tool interfaces, cache strategy |
| **Testing** | `/project:testing` | Unit tests (pytest), e2e tests (Jest + Playwright), MCP server tests |
| **Eval** | `/project:eval` | Agent evaluation test cases, eval suite, regression detection |
| **Deploy** | `/project:deploy-agent` | Shell scripts, environment setup, CI/CD, process management |
| **Review** | `/project:review` | Code review before commits, security checks, convention validation |
| **Sanity** | `/project:sanity` | End-to-end local validation, service health checks, workflow testing |
| **Logging** | `/project:logging` | Shared logging infrastructure, OTEL setup, session_id context |

### Delegation Rules

- **Multi-domain tasks:** Use `/project:coordinator` to analyze the task, break it into subtasks, and delegate to the right agents in order.
- **Single-domain tasks:** Invoke the specific agent directly (e.g., `/project:frontend` for a UI-only change).
- **After implementation:** Always run `/project:testing` if the implementing agent didn't write tests.
- **Agent/MCP changes:** Run `/project:eval` to add evaluation test cases when agent behavior changes.
- **Before every commit:** Run `/project:review` to check for security issues, missing tests, and convention violations.
- **Before merging:** Run `/project:sanity` to validate the full system works end-to-end.

## Commit Workflow
Before creating any commit, **always** run `/project:review` first. This launches a review sub-agent that analyzes staged changes for security issues, missing tests, and project convention violations. Only proceed with the commit if the review passes (no errors). Warnings are informational and do not block commits.

## Languages & Markets
- Primary: Hebrew (RTL), English
- Secondary: Arabic (RTL)
- Currencies: NIS (₪), USD ($)
