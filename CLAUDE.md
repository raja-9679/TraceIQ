# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is TraceIQ

TraceIQ is a UI Testing & Automation SaaS platform. Teams record Playwright browser test journeys, organise them into hierarchical suites, execute them in distributed workers, and receive AI-powered failure analysis. It is multi-tenant with RBAC at Workspace → Team → Project → TestCase levels.

A comprehensive architecture and data model reference lives in `info/CODEBASE.md`.

---

## Services

All services run via Docker Compose from the `infrastructure/` directory.

| Service | Stack | Port |
|---------|-------|------|
| `backend` | FastAPI + SQLModel + Celery | 8000 |
| `execution-worker` (×4) | Node.js + Playwright | — |
| `execution-engine` | Node.js (legacy CONTINUOUS mode) | 3000 |
| `postgres` | PostgreSQL 15 | 5432 |
| `redis` | Redis 7 | 6379 |
| `minio` | MinIO (S3-compatible) | 9000/9001 |
| `celery_worker` / `celery_aggregator` / `celery_beat` | Python Celery | — |
| `pgadmin` | pgAdmin 4 | 8014 |

---

## Commands

### Docker (run from `infrastructure/`)

```bash
# Local dev (hot-reload, volume mounts)
docker compose --env-file env.local -f docker-compose.yml up -d --build

# Production
docker compose --env-file env.prod -f docker-compose.prod.yml up -d --build

# Scale execution workers
docker compose -f docker-compose.yml up -d --scale execution-worker=8

# Rebuild a single service
docker compose -f docker-compose.yml rm -f -v backend && \
  docker compose -f docker-compose.yml build backend && \
  docker compose -f docker-compose.yml up -d backend

# Logs
docker compose logs -f backend
docker compose logs -f celery_worker
docker compose logs -f execution-worker
```

### Backend (run from `backend/`)

```bash
# Local dev without Docker (requires postgres/redis/minio running)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Celery worker
celery -A app.core.celery_app worker --loglevel=info -Q main-queue

# Run tests
pytest

# Run a single test file
pytest tests/test_stale_run_detection.py

# Database migrations
alembic revision --autogenerate -m "description"
alembic upgrade head
```

### Frontend (run from `frontend/`)

```bash
npm run dev          # Hot-reload dev server at http://localhost:5173
npm run build        # Production build
npm run build:dev    # Build for dev environment
npm run lint         # ESLint
```

### Execution Engine (run from `execution-engine/`)

```bash
npm run dev          # Dev server (legacy engine, port 3000)
npm run dev:worker   # Distributed worker in dev mode
npm run build        # TypeScript compile to dist/
```

---

## Architecture

### Request → Execution flow

1. Frontend calls `POST /api/runs?suite_id=X&browser=chromium`
2. Backend creates `TestRun` records (status=PENDING) and dispatches a Celery task (`run_test_suite`)
3. Celery worker (`backend/app/worker.py`) loads the suite, resolves settings inheritance, sets status=RUNNING
4. Jobs are pushed to Redis Stream `jobs:pending` via `JobDispatcher` (`backend/app/services/job_dispatcher.py`)
   - `SEPARATE` mode → 1 job per test case
   - `CONTINUOUS` mode → 1 job per suite (dispatched to legacy engine)
5. `execution-worker` (Node.js) claims jobs via `XREADGROUP`, runs Playwright steps, uploads artifacts to MinIO
6. Worker POSTs results to `POST /api/runs/{id}/webhook` then `POST /api/runs/{id}/finalize`
7. Backend writes `TestCaseResult` rows, triggers Celery notification tasks (email/Slack/Teams)
8. Frontend polls `GET /api/runs/{id}` for live status updates

### Key data relationships

```
Tenant → Workspace → Project → TestSuite → TestCase (steps: JSON array)
                                     ↓
                              TestRun → TestCaseResult
```

`TestSuite` is self-referential (`parent_id`) for nested module hierarchies. Settings (headers, params, auth) are inherited from parent to child unless `inherit_settings=false`.

### RBAC layers

Access checks are in `backend/app/services/access_service.py`:
1. Workspace admin → full access
2. `UserProjectAccess` direct grant
3. `TeamProjectAccess` via team membership
4. Deny

Minimum roles: **viewer** (read), **editor** (create/edit/run), **admin** (manage members/delete).

---

## Backend structure

```
backend/app/
├── main.py               # FastAPI app, router registration, lifespan hooks
├── models.py             # All SQLModel ORM models + Pydantic schemas
├── worker.py             # Celery task: run_test_suite, dispatch functions
├── api/
│   ├── auth.py           # JWT login/register/refresh
│   ├── workspaces.py     # Workspace + team + invite management
│   ├── projects.py       # Project CRUD
│   └── endpoints/
│       ├── test_suites.py
│       ├── test_cases.py
│       ├── test_runs.py  # Run lifecycle: create, webhook, finalize, force-complete
│       ├── schedules.py
│       └── websockets.py
├── core/
│   ├── config.py         # Pydantic settings (all env vars)
│   ├── database.py       # Async SQLAlchemy engine + session
│   ├── celery_app.py     # Celery config + beat schedule
│   └── storage.py        # MinIO client wrapper
├── services/
│   ├── access_service.py # RBAC access checks
│   ├── job_dispatcher.py # Redis Streams job dispatch
│   ├── test_service.py   # Recursive case collection, settings inheritance
│   └── workspace_service.py
└── tasks/
    ├── cleanup_tasks.py       # Stale run cleanup (Celery beat)
    ├── notification_tasks.py  # Email / Slack / Teams
    ├── result_aggregator.py   # Aggregates distributed results
    └── webhook_tasks.py       # Processes engine webhooks
```

---

## Frontend structure

React 18 + Vite + TypeScript + TanStack Query + React Router v6 + Tailwind CSS + shadcn/ui.

JWT is stored in `AuthContext` and attached as `Authorization: Bearer` on all requests via `src/lib/api.ts`.

The heaviest pages (all collocated in `src/pages/`):
- `SuiteDetails.tsx` — nested suites, test case list, run controls, settings, audit log
- `TestRunDetails.tsx` — step-level results, `TraceTimeline` Playwright trace viewer, AI analysis panel
- `Settings.tsx` — notification preferences, profile, workspace settings
- `WorkspacePage.tsx` — team management, member invites, RBAC assignment

---

## Execution Engine structure

```
execution-engine/src/
├── worker.ts      # Redis Stream consumer (main distributed worker)
├── runner.ts      # Playwright step orchestration
├── server.ts      # Express server for legacy POST /run endpoint
├── ai.ts          # OpenAI failure analysis
├── controller/    # Run lifecycle management
└── core/          # MinIO + Redis client setup
```

Step execution switch lives in `runner.ts`. Supported step types: `goto`, `click`, `fill`, `check`, `expect`, `http-request`, `feed-check`, `hover`, `screenshot`.

---

## Known issues to be aware of

- `DELETE /api/runs?all=true` is unscoped — any authenticated user can delete all runs (`test_runs.py:323`)
- Webhook and finalize endpoint security checks are warn-only, not enforced (`test_runs.py:399-427`)
- `PARALLEL` execution mode is defined in the enum but not yet implemented distinctly
- CORS is `["*"]` in default config — verify `BACKEND_CORS_ORIGINS` is set in production
- `/mock/bihar-election` stub endpoint is live in production (`main.py:56`)
- No refresh token — 30-minute JWT causes frequent logouts in long sessions
