# Smart Shopping Agent - Product Guideline

## Product Description
Smart Shopping Agent is an agentic app designed to help users easily shop online. It helps with tasks that are currently time-consuming to perform manually and/or perform poorly even with existing AI tools.

## Example Tasks

### Product Discovery with Auto-Criteria
- User searches for "black refrigerator that fits into a niche of 180hx95wx75d"
- Agent returns best results considering additional criteria like noise level, energy efficiency, manufacturing country, price
- User searches for "dark nirosta colored microwave"
- Agent returns results including semantically similar colors like matt black or charcoal, plus relevant microwave criteria

### Multi-Product Matching
- User searches for "A built-in stove and microwave set for a small family"
- Agent returns couples of stoves and microwaves that match in brand/color, fit family size, and are generally recommended

### Price Comparison
- User searches for "Model A, Model B, Model C"
- Agent returns list of sellers for each product, plus sellers offering multiple products for potential bulk discounts
- Results include contact information for each seller

### Adaptive Web Discovery (Core Innovation)
- Agent automatically discovers relevant e-commerce sites through web search
- Agent learns how to scrape each site on first visit
- Scraping strategies are stored and reused for future searches
- Agent re-learns when site structures change (self-healing)
- No hardcoded site-specific logic - works with ANY e-commerce site

### Manual Seller Contact (MVP Approach)
- Results display seller contact information (phone, email, website)
- "Contact Seller" button opens WhatsApp/Email client with pre-filled message
- User manually sends messages
- Future: Automated contact with user permission using WhatsApp bridge and Gmail API

## User Interface

### Navigation
Web app with tab-based navigation containing:

### Main "Product Search" Page
Search input for natural language queries:
- "I am looking for a quiet affordable refrigerator"
- "I am looking for matching built-in stove and microwave"
- "Find me the best prices for Model A12345"

Search history with status indicators. Ability to continue/refine searches through conversation.

**Search Refinement Examples:**
- "Narrow down the results to only black models"
- "Now find a microwave that matches this model"
- "Remove the noise level limitation"
- "Add a matching refrigerator"

### Shopping List Page
- User-selected models
- Manual add/remove items
- Bulk "price search" for entire list

### Results Display
Results format varies by search type:

**Single Product Discovery:**
- List of unique models
- For each model: criteria used for selection (noise level, price, weight, etc.)
- For each model: list of sellers with contact info, rating, website link, "Contact" button

**Multiple Products:**
- Suggested models for each product type
- Seller lists per model
- Aggregated sellers (offering multiple items)

**Specific Model Search:**
- Model header with list of sellers

**Multiple Specific Models:**
- Aggregated sellers section (selling multiple items)
- Model headers with seller lists per model

**Multi-Product Matching (e.g., stove + microwave):**
- Headers showing matched product pairs
- Seller lists under each pair

### User Experience Features
- Real-time status updates throughout search:
  - "🔍 Started search..."
  - "🌐 Searching on Amazon.com, Zap.co.il..."
  - "📊 Analyzing 25 products..."
  - "✅ Found 12 results"
- Persistent state (refresh returns to where user left off)
- Multi-language support including RTL (Hebrew, Arabic)
- Mobile-friendly responsive design
- Add products to shopping list from results

### Privacy and Security
- Input/output validation for safety
- Privacy filtering on user requests
- No access to or storage of sensitive user information
- PII detection and removal from results

## Technical Architecture

### Agent System (MCP-Based)

#### Main Agent
- Orchestrates entire workflow
- Manages conversation state with users
- Routes to specialized MCP servers
- Waits for user input and initiates agentic loop until results satisfy requirements

#### MCP Servers (Independently Developable)

**IO Validator MCP**
- Ensures user requests are privacy-safe
- Validates output format and structure
- Removes sensitive information from results

**Web Search MCP**
- Discovers e-commerce sites through web search
- Extracts URLs from search results
- Identifies e-commerce sites using heuristics
- Uses Playwright for headless browsing
- No paid third-party APIs

**Web Scraper MCP (Core Innovation)**
- Adaptively learns scraping strategies for new sites
- Stores successful strategies per domain in database
- Retrieves cached strategies for known sites
- Re-learns when scraping fails (self-healing)
- Tracks success rates per domain
- Multiple strategy approaches:
  - Common e-commerce patterns (CSS selectors)
  - Semantic analysis (find price-like patterns)
  - AI vision analysis (screenshot analysis as fallback)
- Tools: scrape page, get/save scraping instructions

**Product Criteria MCP**
- Defines criteria required for product types
- Uses web search/scrape to research important criteria
- Caches criteria per product category
- Merges user-specific criteria with general criteria
- Tools: get cached criteria, research criteria, merge user criteria

**Results Processor MCP**
- Validates results meet desired criteria
- Checks result sufficiency (signals to continue searching if needed)
- Aggregates sellers offering multiple products
- Triggers additional searches for multi-product requests
- Formats final results

**Negotiator MCP (Future - Not in MVP)**
- Automatically contacts sellers
- Manages conversations for pricing/availability
- Requires user permission before sending messages
- WhatsApp (via local bridge) - primary method
- Email (via Gmail API) - fallback method

### Backend Server (Python FastAPI)
- Serves web app static files
- Manages WebSocket connections for real-time updates
- Handles RESTful API calls
- Session management (shared session_id across all logging)
- API endpoints for:
  - Search initiation and status
  - Shopping list management
  - Seller contact (future)
  - Real-time updates via WebSocket

### Frontend (Next.js)
- Next.js 14 with App Router
- Tab-based navigation
- RTL support for Hebrew and Arabic
- Mobile-first responsive design
- Real-time status updates via WebSocket
- Persistent state using IndexedDB (syncs with backend)
- Pages: Product Search (main), Shopping List, Individual Search Results

### Database and Caching

**Local Development:** SQLite
**Production:** PostgreSQL
**Critical:** Use SQLAlchemy ORM for drop-in replacement capability

**Database Stores:**
- Users and preferences
- Search history and results
- Product criteria (cached per category)
- Scraping instructions (per domain with success rates)
- Seller information
- Future: Seller conversation history

**Caching Strategy:**
- Hash-based cache keys: `hash(input + mcp_version)`
- Manual cache clearing when needed
- TTL-based expiration
- Extensible design for future strategies
- Agentic responses cached per input and agent version

### Logging (OpenTelemetry-Based)

**Shared Session ID** passed across all components for end-to-end debugging.

**Three Logging Layers:**

1. **Agentic Logs (OTEL Traces/Spans)**
   - Every agent, sub-agent, and MCP tool call logged
   - Inputs, outputs, configuration, reasoning captured
   - Nested sessions and spans for DAG visualization
   - Configurable storage (local, third-party)
   - Admin-only access

2. **Operational Logs**
   - API calls, errors, warnings
   - Configurable storage (local, third-party)
   - JSON structured format

3. **Engagement Logs**
   - User action tracking for debugging and metrics
   - Configurable storage (local, third-party)

**Development Environment:** All logs to console (colorized)

### Configuration and Deployment

**Configuration Management**
- All settings via environment variables
- Separate configs for: local, dev, test, prod environments
- Environment-specific .env files

**Deployment**
- Bash deployment scripts
- Local, dev, and production deployment support

## Development Methodology

### File Structure
```
/scripts          - Deployment, testing, evaluation, cache management scripts
/config           - Environment-specific configuration files
/src
  /mcp_servers    - Each MCP server in separate directory
    /io_validator_mcp
    /web_search_mcp
    /web_scraper_mcp    - Includes strategy implementations
    /product_criteria_mcp
    /results_processor_mcp
    /negotiator_mcp (future)
  /agents         - Main agent and prompts
  /backend        - API, database, WebSocket
  /frontend       - Next.js app (components, pages, lib)
  /shared         - Shared models and utilities
/tests
  /unit           - Backend unit tests (100% coverage)
  /e2e            - Frontend end-to-end tests (Jest)
  /mcp            - Per-MCP server tests
/evals
  /test_cases     - JSON files with test scenarios
  /eval_agent.py  - Calls API and judges results
  /results        - Generated reports
/logs             - Local logs (gitignored)
/.claude          - Claude Code configuration
/README.md
```

### Source Control (Git)

**Branches:**
- `main` - Production
- `develop` - Development baseline
- `feature/<agent-name>/<feature>` - New features
- `fix/<module>/<issue>` - Bug fixes

**Module Tags:** API, WebApp, MCP-Search, MCP-Scraper, Agent, etc.

**Commit Requirements:**
- Clear description of changes
- Test plan including:
  - Unit tests run (pytest/npm test)
  - E2E tests run
  - Evaluations run (if agents changed)
  - Screenshots for UI changes (before/after)

### Testing Strategy

#### Unit Tests (Backend)
- 100% coverage required
- pytest framework
- Every new function needs corresponding test
- Run on every commit

#### End-to-End Tests (Frontend)
- Jest + Playwright
- User flow coverage
- Use test environment with cached agent responses
- Run on every commit

#### MCP Server Tests
- Per-server test suite
- Mock external calls (web requests, Playwright)
- Validate tool inputs/outputs

#### Agent Evaluation Framework
- **Purpose:** Ensure agent performance doesn't regress
- **Structure:**
  - Test cases stored as JSON in code repository
  - Evaluation agent calls API and judges results
  - Tests include:
    - Real production scenarios
    - Edge cases (empty results, slow responses)
    - Regression tests for known bugs
    - Multi-language tests (Hebrew, Arabic)
    - Live site tests (real e-commerce websites)
- **Execution:**
  - Run before every merge to main
  - Generate HTML test results reports
  - Track success rates over time
- **Example Test Case:**
  - Input: "quiet affordable refrigerator"
  - Expected: Results include noise_level and price criteria, minimum 5 results

### Git Hooks
**Pre-commit:**
- Prevent direct commits to main branch
- Run relevant tests based on changed files
- If agent files changed, run appropriate evaluations

**Pre-push (to main):**
- Run full evaluation suite
- Block push if evaluations fail

**Post-agent-completion:**
- If MCP server changed, run its evaluations
- Run relevant unit/e2e tests
- Review agent checks code before commit

## Claude Code Agent Team

### Team Members (Specialized Agents)
- **Main Coordinator** - Orchestrates team, assigns tasks, manages workflow
- **Frontend Agent** - Next.js, UI/UX, RTL support, responsive design
- **Backend Agent** - FastAPI, database, WebSocket, API design
- **MCP Agent** - Builds and improves MCP servers, adaptive scraping logic
- **Deployment Agent** - Bash scripts, database migrations, environment setup
- **Testing Agent** - Unit tests, e2e tests, test coverage
- **Eval Agent** - Creates evaluation test cases, maintains eval suite
- **Review Agent** - Code review before commits, quality checks
- **Sanity Agent** - End-to-end local testing, validates full workflows

### Agent Workflow
Each agent works in separate branch tagged with name.

**Development Flow:**
1. Main Coordinator assigns task to specialized agent
2. Agent creates feature branch
3. Agent implements feature
4. Testing Agent writes tests
5. Review Agent checks code
6. Eval Agent adds evaluation cases (if agents changed)
7. Sanity Agent validates end-to-end flow
8. Merge to develop, eventually to main

### Required Claude Hooks
- Validate code not committed to main branch
- Run appropriate evals when agent files change
- Run relevant tests and e2e tests before commit
- Review agent checks code after tests pass

## MVP Implementation Plan

### Phase 1: Basic Infrastructure
- Project skeleton with all directories
- SQLite database with basic schema
- FastAPI serving Next.js
- WebSocket connection working
- Basic Main Agent echo functionality
- Playwright setup

### Phase 2: Intelligent Web Discovery
- Web Search MCP: Google search, URL extraction, e-commerce detection
- Web Scraper MCP: Learn scraping strategy for first site
- Store/retrieve scraping instructions from database

### Phase 3: Multi-Site Scraping
- Scrape 3-5 different e-commerce sites
- Product Criteria MCP: Extract criteria from web
- Results Processor MCP: Aggregate multi-site results
- Frontend displays 10+ results from multiple sites
- Cache scraping instructions

### Phase 4: Refinement & Learning
- Handle pagination for multi-page results
- Re-learning when scraping fails
- Success rate tracking per domain
- Search history with status indicators
- Shopping list (add/remove)
- Manual "Contact Seller" buttons

### Phase 5: Polish & Deploy
- RTL support (Hebrew, Arabic)
- Mobile responsive design
- Multi-language product search
- Full test coverage (mock Playwright in tests)
- Evaluation suite (test on 10+ real e-commerce sites)
- Production deployment scripts

## Key Design Principles

### Adaptive Web Automation
- No hardcoded site-specific logic
- Agent discovers e-commerce sites through search
- Agent learns scraping strategies on first visit
- Strategies stored and reused for performance
- Self-healing when sites change
- Works with ANY e-commerce site (Israeli, US, niche stores)

### Modular MCP Architecture
- Each MCP server independently developable
- Separate testing per MCP server
- Clear tool interfaces between components
- Version tracking for cache invalidation

### User Experience First
- Real-time feedback on agent progress
- Conversational refinement of searches
- Multi-language and RTL support
- Mobile-friendly design
- Persistent state across sessions

### Quality Assurance
- 100% backend test coverage
- Full frontend e2e coverage
- Agent evaluation framework
- Multi-layer logging for debugging
- Git hooks enforce quality gates

## Target Markets and Languages

**Primary Markets:**
- Israel (Hebrew RTL, currency: NIS ₪)
- United States (English, currency: USD $)

**Language Support:**
- English
- Hebrew (RTL)
- Arabic (RTL)
- Agent performs searches in user's language
- Agent discovers regionally relevant e-commerce sites

## Future Enhancements (Post-MVP)
- Automated seller contact with user permission
- WhatsApp bridge integration
- Gmail API integration
- Advanced price tracking and alerts
- Product comparison tools
- User preference learning
- Recommendation engine based on purchase history
