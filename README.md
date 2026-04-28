# Sushmi MCP — Multi-tenant agentic copilot for freelancers

A production-grade demonstration of the **Model Context Protocol** (Anthropic 2024-11-05 spec) powering a per-user agent loop. Every user brings their own GitHub / Gmail / Razorpay credentials; an LLM orchestrator calls MCP tools scoped exclusively to that tenant.

- **Live frontend:** https://freelance-mcp-c3b42.web.app
- **Live backend API:** https://sushmi-mcp.vercel.app/api
- **AI service:** deployable via `render.yaml` — see `DEPLOY.md`

## What's inside

- **7 MCP servers** exposing 17 tools — all spec-compliant (`list_tools`, `call_tool`, JSON-Schema args, `TextContent` results, `isError` on failure)
  - `firestore` — user's projects, invoices, alerts, dashboard
  - `github` — repos, PRs, commits, weekly activity (reads user's PAT)
  - `gmail` — recent/search/get (reads user's IMAP app password)
  - `calendar` — list/search/draft Google Calendar events
  - `razorpay` — invoices, payments, customers (reads user's test keys)
  - `expenses` — log expenses against a project
  - `knowledge_base` — semantic search over workspace + indexed inbox
- **Multi-agent system** — six agents across two roles:
  - **Chat-time** (request-driven): `Planner` → `Executor` two-step loop. The Planner (`python_ai/app/planner.py`) produces a 1-5 step plan; the Executor (`agent.py`, LangChain `AgentExecutor`) follows it with real tool-calling. Trivial messages skip planning to keep latency down.
  - **Proactive** (scheduled, no user prompt — `python_ai/app/agents/`):
    - `InboxTriageAgent` — LLM-based urgent/normal/low classification, single grouped nudge for urgent items
    - `ProjectMonitorAgent` — rule-based health score from commits × days-left × budget burn; flags at-risk projects
    - `AnomalyDetectorAgent` — silent clients (no email in 14d), overdue invoices past grace, burnout signal (off-hours activity), scope creep (spend ≥ 90% of budget)
    - `RecurringWorkflowsAgent` — Monday-morning weekly digest, 1st-of-month invoice reminder
  - Scheduler: `POST /agents/run?user_id=…` (auth: `X-Cron-Secret` header). Wired to a free **GitHub Actions cron** (`.github/workflows/cron.yml`) that runs every 30 min and iterates over all users. Each agent is idempotent within its dedupe window; all push notifications go through the existing in-app bell via Node's `/api/internal/notifications/push`.
- **Proactive Agents** — Four specialized background agents (`python_ai/app/agents/`) that run on a schedule to monitor user data:
  - **Inbox Triage**: Uses Gemini to classify incoming emails by priority and pushes bundled notifications for urgent items.
  - **Project Monitor**: Computes a health score for every project based on commit cadence, deadlines, and budget burn.
  - **Anomaly Detector**: Identifies silent clients (no recent emails), overdue invoices, scope creep, and potential burnout patterns.
  - **Recurring Workflows**: Generates weekly summaries every Monday morning and invoicing reminders on the 1st of each month.
- **RAG with caching** — Gemini embeddings + Chroma Cloud (or in-memory numpy fallback). Per-user index keyed by data signature; rebuilds only when the user's data changes.
- **Guardrails** (`python_ai/app/guardrails.py`)
  - Input: max length, prompt-injection pattern detection
  - Per-user sliding-window rate limit (30/hour)
  - Output: heuristic PII redaction (email / phone / card-shaped runs)
- **Observability** (`python_ai/app/observability.py`)
  - Structured JSON logs with auto-attached `request_id` + `user_id`
  - Per-request `X-Request-Id` middleware
  - Prometheus-format `/metrics` endpoint (chats, tool calls, planner/executor latency, guardrail violations, PII redactions)
- **Multi-tenant isolation** enforced at four layers:
  - Firestore rules deny all client access; backend Admin SDK only
  - Per-user integration credentials encrypted with **AES-256-GCM** at rest
  - MCP servers constructed with the tenant's `NodeClient` — can't reach another tenant's data by design
  - HS256 service JWTs (5-min TTL, `userId` claim) authenticate Node → Python

## Testing

Both stacks have automated tests; CI runs them on every push (`.github/workflows/ci.yml`).

```bash
# Python (95 tests — pytest)
cd python_ai && python -m pytest

# Node (6 tests — node:test, no devDeps)
npm test
```

Coverage:

| Suite | What it covers |
|-------|----------------|
| `tests/test_agents.py` | all 4 proactive agents: signals, nudges, bundled notifications, time-windows |
| `tests/test_guardrails.py` | input validation, injection patterns, PII redaction, rate limiter |
| `tests/test_security.py` | JWT sign/verify roundtrip, expiry, bad signatures, `require_user` dep |
| `tests/test_rag.py` | chunker, doc-signature cache key, numpy backend search, builders |
| `tests/test_observability.py` | JSON formatter, context vars, metrics counters & histograms |
| `tests/test_main.py` | `/health`, `/metrics`, auth on `/chat`, guardrails wiring (mocked Orchestrator), PII redaction in responses, rate-limit 429 |
| `tests/test_mcp_servers.py` | every MCP server: metadata + tool schema validation + unknown-tool error |
| `server/tests/jwt.test.js` | service-token roundtrip, bad token, wrong secret, TTL |
| `server/tests/warmup.test.js` | `/api/chat/warmup` contract — both upstream up and down |

## Docs

| File                 | What                                                               |
|----------------------|--------------------------------------------------------------------|
| `ARCHITECTURE.md`    | System diagram, MCP protocol choices, multi-tenancy proofs          |
| `DEMO.md`            | 5-minute demo script mapping to grading rubric                      |
| `DEPLOY.md`          | Full deploy runbook for all three services                          |

## Quick start (local)

```bash
# 1. Install deps
npm install                                    # frontend + Node backend deps (hoisted)
cd python_ai && python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd ..

# 2. Configure .env at repo root
cp .env.example .env
# fill in GEMINI_API_KEY, FIREBASE_SERVICE_ACCOUNT, etc.

# 3. Run — three terminals
npm run dev                                    # frontend  http://localhost:5174
cd server && npm run dev                       # backend   http://localhost:3001
cd python_ai && .venv/bin/uvicorn app.main:app --port 8001  # AI

# 4. Open http://localhost:5174, sign up, connect a GitHub PAT in Integrations,
#    click "Ask Sushmi" and ask anything
```

## Tech

| Layer      | Stack                                                                |
|------------|----------------------------------------------------------------------|
| Frontend   | Vue 3, Vite, Tailwind, Pinia, Vue Router                             |
| Backend    | Node 20, Express, Firebase Admin, jsonwebtoken, axios                |
| AI service | Python 3.11, FastAPI, LangChain, langchain-google-genai, httpx       |
| LLM        | Gemini 2.0 Flash + `gemini-embedding-001`                            |
| Data       | Firestore (per-tenant subcollections), in-memory FAISS-style RAG     |
| Hosting    | Firebase Hosting, Vercel, Render (Docker)                            |

## License

Private — RagWorks assignment submission.
