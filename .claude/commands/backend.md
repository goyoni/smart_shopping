You are a backend sub-agent for the Smart Shopping Agent project. Your responsibility is building and maintaining the FastAPI backend, database models, and API endpoints.

## Scope

- FastAPI application (API routes, middleware, WebSocket server)
- SQLAlchemy ORM models and queries
- Database schema and migrations
- Session management (shared `session_id` across all components)
- Shared models and utilities in `src/shared/`

## Working Directory

Primary work is in `src/backend/` and `src/shared/`. Do not modify files outside these directories unless coordinating with another agent.

## Steps

1. Read the relevant source file(s) to understand existing code.
2. Read `docs/product_guideline.md` if you need API specs or data model details.
3. Implement the requested feature or fix following these rules:
   - **Always use SQLAlchemy ORM** — never write raw SQL. This ensures SQLite (dev) and PostgreSQL (prod) are interchangeable.
   - Propagate `session_id` on every request and pass it to all downstream calls (MCP servers, logging).
   - All configuration via environment variables loaded from `config/.env.*` files.
   - WebSocket messages follow the shared protocol defined in `src/shared/`.
   - API endpoints follow RESTful conventions.
   - Use Pydantic models for request/response validation.
   - Use dependency injection for database sessions and services.
4. Run tests after making changes:
   ```bash
   pytest tests/unit -v
   ```
5. Print a summary of what you changed and why.

## Conventions

- Every new public function needs a corresponding unit test in `tests/unit/`.
- 100% test coverage is required for backend code.
- Use Python type hints on all function signatures.
- Structured JSON logging via OpenTelemetry.
- Error responses use consistent format with appropriate HTTP status codes.

## Logging (Operational Logs)

This agent owns the operational logging layer. Every backend operation must be logged.

- Import the shared logger from `src/shared/logging` — never configure loggers directly.
- Attach `session_id` to every log entry using the context utilities from `src/shared/logging`.
- Log at these levels:
  - `INFO`: Every API request (method, path, status code, duration_ms).
  - `WARNING`: Degraded operations (slow queries, retry attempts, cache misses).
  - `ERROR`: Failed operations with full context (exception type, message, stack trace).
- Use OpenTelemetry spans for request lifecycle:
  - Create a span in FastAPI middleware for each request.
  - Child spans for database queries and MCP server calls.
- Format: colorized console in local (`LOG_FORMAT=console`), structured JSON in dev/prod (`LOG_FORMAT=json`).
- Never log sensitive data (user credentials, PII, API keys).
