# TraceIQ

**The regression-safety net for code that AI writes.** Teams record Playwright
browser-test journeys, organise them into hierarchical suites, execute them
across distributed workers, and consume the results as a *machine-callable*
verification layer — so an AI coding agent can ask "did my change break
anything?" and get a structured, actionable answer.

Multi-tenant SaaS. Backend in FastAPI + Celery, distributed workers in
Node.js + Playwright, storage in Postgres + Redis + MinIO. Everything runs
via Docker Compose.

---

## Where to start

| If you're… | Read this first |
|---|---|
| **Deploying to production** (or planning a release) | [`PRODUCTION_DEPLOY.md`](./PRODUCTION_DEPLOY.md) — full runbook: first-time Phase A→E upgrade, steady-state feature updates, rollback procedures, healthcheck script |
| **Trying to understand the architecture end-to-end** | [`ARCHITECTURE.md`](./ARCHITECTURE.md) — services, data model, run lifecycle, auth, AI integration map, RBAC, extension points |
| **An AI coding agent** (Claude Code, Cursor, etc.) authoring tests via MCP | [`integrations/mcp-server/AGENT_GUIDE.md`](./integrations/mcp-server/AGENT_GUIDE.md) — mental model, step-type reference, suite-organization conventions, 5 documented pitfalls, end-to-end Sarvajna example |
| **Claude Code working on this codebase** | [`CLAUDE.md`](./CLAUDE.md) — commands, structure, what's known-broken |
| **Tracking what's in flight vs. deferred** | [`SCOPE_NOTES.md`](./SCOPE_NOTES.md) — phase-by-phase changelog + future-work index |
| **Curious about historical migrations** | [`MIGRATION_GUIDE.md`](./MIGRATION_GUIDE.md) and `backend/scripts/MIGRATION_PARALLEL_EXECUTION.md` |
| **Just trying it locally** | The "Quick start" below |

---

## Quick start (local development)

Prereqs: Docker + Docker Compose. ~8 GB RAM, ~10 GB disk.

```bash
# 1. Clone + bring up TraceIQ
git clone https://github.com/raja-9679/TraceIQ.git
cd TraceIQ/infrastructure
docker compose --env-file env.local -f docker-compose.yml up -d --build

# 2. (Optional) Bring up the TodoLite demo app — a small SaaS-shaped app
#    used to exercise TraceIQ end-to-end. Joins the TraceIQ docker network
#    so the workers can drive it.
cd ../todolite
docker compose up -d --build
open http://localhost:8080   # log in as alice / wonderland

# 3. Frontend (Vite dev server)
cd ../frontend
npm install && npm run dev
open http://localhost:5173
```

Then create an account at `http://localhost:5173/register` and follow the
in-app flow to create a project and your first test suite.

---

## Repository layout

```
TraceIQ/
├── ARCHITECTURE.md             ← system architecture + lifecycle + extension points
├── PRODUCTION_DEPLOY.md        ← operator runbook (this is where you go to ship)
├── SCOPE_NOTES.md              ← what's done / deferred per phase
├── CLAUDE.md                   ← orientation for Claude Code sessions
├── MIGRATION_GUIDE.md          ← historical migration notes
├── README.md                   ← you are here
│
├── backend/                    ← FastAPI + SQLModel + Celery
│   ├── app/
│   │   ├── api/                ← REST endpoints
│   │   ├── core/               ← auth, config, db, celery setup
│   │   ├── services/           ← RBAC, dispatcher, test_service
│   │   ├── tasks/              ← Celery tasks (cleanup, notifications, webhooks, …)
│   │   ├── ai/                 ← LLM provider abstraction + engine
│   │   ├── runner/             ← in-process Playwright (legacy path)
│   │   ├── alembic/versions/   ← schema migrations
│   │   ├── models.py           ← all SQLModel ORM models
│   │   ├── worker.py           ← Celery: run_test_suite, dispatch_*_jobs
│   │   ├── main.py             ← FastAPI app + router registration
│   │   └── AGENT_GUIDE.md      ← bundled copy of the agent guide
│   ├── tests/                  ← pytest (root + e2e/ + integration/)
│   ├── scripts/                ← one-off maintenance scripts
│   └── Dockerfile, requirements.txt
│
├── execution-engine/           ← Node.js + Playwright
│   ├── src/
│   │   ├── worker.ts           ← Redis-stream consumer (distributed)
│   │   ├── runner.ts           ← step orchestration
│   │   ├── server.ts           ← legacy CONTINUOUS-mode HTTP API
│   │   ├── core/test-executor.ts  ← the step-type switch (source of truth)
│   │   ├── ai.ts               ← reactive selector heal
│   │   ├── llm-provider.ts     ← OpenAI / Anthropic / Null
│   │   ├── visual-diff.ts      ← pixelmatch-based screenshot diff
│   │   └── baseline-client.ts  ← visual-baseline fetcher
│   └── Dockerfile, Dockerfile.worker, package.json
│
├── frontend/                   ← React 19 + Vite + TypeScript + Tailwind + shadcn/ui
│   ├── src/
│   │   ├── pages/              ← Dashboard, SuiteDetails, TestRunDetails, …
│   │   ├── components/         ← shared UI
│   │   ├── api/, lib/api.ts    ← axios client
│   │   ├── context/AuthContext.tsx  ← JWT + (Phase A) refresh-token handling
│   │   └── App.tsx
│   └── Dockerfile, package.json, vite.config.ts
│
├── infrastructure/             ← Docker Compose configs + env files
│   ├── docker-compose.yml      ← local dev (hot-reload, volume mounts)
│   ├── docker-compose.prod.yml ← production
│   └── env.{local,dev,prod}
│
├── integrations/               ← agent / CI integrations
│   ├── mcp-server/             ← MCP server: 26 tools, stdio transport
│   │   ├── AGENT_GUIDE.md      ← canonical agent runbook (Sarvajna-anchored)
│   │   └── src/traceiq_mcp/
│   ├── github-action/          ← gate PRs on TraceIQ regression
│   └── browser-recorder/       ← MV3 Chrome ext that records → /api/cases
│
├── todolite/                   ← demo app used to exercise the platform e2e
│   ├── app.py, templates/index.html
│   ├── Dockerfile, docker-compose.yml
│   └── README.md
│
└── info/                       ← older / more detailed dev docs
    ├── CODEBASE.md             ← earlier system-wide reference
    ├── PRODUCTION_DB_RUNBOOK.md
    └── …
```

---

## What's on `main` vs. `feature/ai-agent-integration`

The active development branch ships substantial new agent-callable features.
The full breakdown is in `SCOPE_NOTES.md`. Headline items:

- **AI-callable foundation** — API-key auth, refresh tokens with rotation, git-context on every run, outbound webhooks, MCP server, GitHub Action, LLM provider abstraction (Phase A)
- **Resilience** — semantic selectors, personas, proactive heal proposals, visual regression diff, flake quarantine (Phase B)
- **Coverage authoring** — test-from-intent / test-from-OpenAPI, deployment-comparison runs, browser-recorder extension (Phase C)
- **Agent owns the suite** — `code_paths`-driven impact analysis, `CaseProposal` review queue (API keys can't auto-merge), tautology detector (Phase D)
- **Mode-1 MCP completion** — agent provenance, full read/structural-write/bulk MCP tools, the agent guide (Phase E)

Deploying any of this onto an existing production environment? Start with
[`PRODUCTION_DEPLOY.md`](./PRODUCTION_DEPLOY.md). It's the single source of
truth for the upgrade path.

---

## Common commands

### Build + run
```bash
# Local dev (hot-reload, volume mounts)
cd infrastructure
docker compose --env-file env.local -f docker-compose.yml up -d --build

# Production
docker compose --env-file env.prod -f docker-compose.prod.yml up -d --build

# Scale execution workers
docker compose -f docker-compose.yml up -d --scale execution-worker=8

# Rebuild a single service (after editing its deps)
docker compose -f docker-compose.yml rm -f -v backend && \
  docker compose -f docker-compose.yml build backend && \
  docker compose -f docker-compose.yml up -d backend
```

### Logs
```bash
docker compose logs -f backend
docker compose logs -f celery_worker
docker compose logs -f execution-worker
```

### Backend (without Docker — needs postgres/redis/minio running)
```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
celery -A app.core.celery_app worker --loglevel=info -Q main-queue
celery -A app.core.celery_app worker --loglevel=info -Q aggregator-queue
celery -A app.core.celery_app beat --loglevel=info
pytest                                                 # all tests
pytest tests/e2e/test_parallel_execution.py            # single file
alembic upgrade head
alembic revision --autogenerate -m "description"
```

### Frontend
```bash
cd frontend
npm run dev          # Vite at http://localhost:5173
npm run build        # production build
npm run lint
```

### MCP server (for an agent driving TraceIQ)
```bash
cd integrations/mcp-server
pip install -e .
export TRACEIQ_BASE_URL=http://localhost:8000
export TRACEIQ_API_KEY=tiq_...
export TRACEIQ_AGENT_ID=claude-code
python -m traceiq_mcp.smoke_test   # sanity-check: lists projects
```

---

## Contributing

- New backend endpoint? Depend on `get_current_principal` (preferred) or
  `get_current_user`. Use `access_service.has_project_access(...)` before
  any mutation. Register in `app/main.py`.
- New step type for the runner? Add a case to
  `execution-engine/src/core/test-executor.ts`, update the curated
  catalogue at `backend/app/api/agent_reference.py:_STEP_TYPES`, and
  surface in `integrations/mcp-server/AGENT_GUIDE.md` if it's user-facing.
- Changing the data model? Edit `backend/app/models.py`, write an Alembic
  migration in `backend/app/alembic/versions/`, update the migration table
  in `PRODUCTION_DEPLOY.md` §3.2.
- Adding a deploy step or env var? Update the relevant section of
  `PRODUCTION_DEPLOY.md` in the same commit.

For Claude Code specifically: see `CLAUDE.md` for the orientation a session
should start with.

---

## License

(Add license here.)
