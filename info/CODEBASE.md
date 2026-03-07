# TraceIQ — Codebase Reference

> **Purpose:** This is the living reference document for the TraceIQ platform. Any AI agent or developer can read this file to get a complete picture of the architecture, data model, API surface, execution pipeline, known issues, and development workflows. Update this file whenever significant changes are made.
>
> **Last updated:** 2026-03-06

---

## 1. Platform Overview

**TraceIQ** is a production-grade **UI Testing & Automation SaaS** platform. It enables teams to:

- Record Playwright browser test journeys (steps: click, fill, goto, expect, etc.)
- Organise tests into hierarchical suites/modules with inherited settings
- Execute tests in distributed, isolated workers across multiple browsers and devices
- Capture artifacts (video, trace, screenshots) stored in MinIO object storage
- Get AI-powered failure analysis per test run
- Manage access via multi-tenant RBAC (Workspace → Team → Project → TestCase)
- Receive notifications via Email / Slack / Teams on failures

The platform is **fully Dockerized**. Infrastructure is defined in `infrastructure/`.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          CONTROL PLANE                              │
│                                                                     │
│  ┌──────────────┐        ┌──────────────┐       ┌───────────────┐  │
│  │   Frontend   │ REST   │   Backend    │ ORM   │  PostgreSQL   │  │
│  │  React/Vite  │───────▶│  FastAPI     │──────▶│  (port 5432) │  │
│  │  TypeScript  │        │  (port 8000) │       └───────────────┘  │
│  └──────────────┘        └──────┬───────┘                          │
│                                 │ Celery tasks                      │
│                          ┌──────▼───────┐       ┌───────────────┐  │
│                          │    Redis     │       │    MinIO      │  │
│                          │  (port 6379) │       │  (port 9000)  │  │
│                          │  Broker +    │       │  Artifacts    │  │
│                          │  Job Streams │       └───────────────┘  │
│                          └──────┬───────┘                          │
└─────────────────────────────────┼───────────────────────────────────┘
                                  │ Redis Streams (jobs:pending)
                    ┌─────────────▼──────────────┐
                    │        DATA PLANE           │
                    │                             │
                    │  ┌────────────────────────┐ │
                    │  │  execution-workers ×4  │ │
                    │  │  Node.js + Playwright  │ │
                    │  │  (distributed workers) │ │
                    │  └──────────┬─────────────┘ │
                    │             │ results        │
                    │  ┌──────────▼─────────────┐ │
                    │  │  execution-engine      │ │
                    │  │  (legacy CONTINUOUS)   │ │
                    │  │  Node.js, port 3000    │ │
                    │  └────────────────────────┘ │
                    └─────────────────────────────┘
```

---

## 3. Docker Services

| Service | Image/Build | Ports | Role |
|---------|------------|-------|------|
| `postgres` | `postgres:15-alpine` | 5432 | Primary relational DB |
| `redis` | `redis:7-alpine` | 6379 | Celery broker + Redis Streams job queue |
| `minio` | `minio/minio` | 9000 (API), 9001 (Console) | Artifact object storage (S3-compatible) |
| `backend` | `./backend` | 8000 | FastAPI REST API |
| `celery_worker` | `./backend` | — | Celery worker (main-queue, 4 concurrency) |
| `celery_aggregator` | `./backend` | — | Celery worker (aggregator-queue, 2 concurrency) |
| `celery_beat` | `./backend` | — | Scheduled task runner |
| `execution-worker` | `./execution-engine` | — | Playwright test runner (4 replicas by default) |
| `execution-engine` | `./execution-engine` | 3000 | Legacy engine (CONTINUOUS shared-browser mode) |
| `pgadmin` | `dpage/pgadmin4` | 8014 | DB admin UI |

### Docker Compose Files

| File | Use Case |
|------|----------|
| `docker-compose.yml` | Local dev (hot-reload, volume mounts) |
| `docker-compose.prod.yml` | Production (optimized, no volume mounts) |
| `docker-compose.distributed.yml` | Scale-out (explicit worker replica control) |

### Key Build Commands (from `infrastructure/`)

```bash
# Local dev
docker compose --env-file env.local -f docker-compose.yml up -d --build

# Production
docker compose --env-file env.prod -f docker-compose.prod.yml up -d --build

# Distributed mode
docker compose --env-file env.local -f docker-compose.distributed.yml up -d --build

# Scale workers
docker compose -f docker-compose.distributed.yml up -d --scale execution-worker=8

# Rebuild a single service
docker compose -f docker-compose.distributed.yml rm -f -v backend && \
docker compose -f docker-compose.distributed.yml build backend && \
docker compose -f docker-compose.distributed.yml up -d backend
```

---

## 4. Repository Structure

```
TraceIQ/
├── backend/                  # FastAPI Python backend
│   ├── app/
│   │   ├── main.py           # App entry point, router registration, lifespan
│   │   ├── models.py         # SQLModel ORM + Pydantic schemas (all in one)
│   │   ├── worker.py         # Celery task: run_test_suite + dispatch functions
│   │   ├── settings_models.py
│   │   ├── api/              # Route handlers
│   │   │   ├── auth.py       # JWT login, register, refresh
│   │   │   ├── admin.py      # Tenant admin: user listing, org assignment
│   │   │   ├── workspaces.py # Workspace, team, invite management
│   │   │   ├── projects.py   # Project CRUD + member access
│   │   │   ├── settings.py   # User settings (notifications)
│   │   │   └── endpoints/
│   │   │       ├── test_suites.py  # Suite CRUD, settings, move
│   │   │       ├── test_cases.py   # Case CRUD
│   │   │       ├── test_runs.py    # Run creation, querying, webhook, finalize
│   │   │       └── websockets.py   # WebSocket (run progress)
│   │   ├── core/
│   │   │   ├── config.py     # Pydantic settings (env vars)
│   │   │   ├── database.py   # Async SQLAlchemy engine + session
│   │   │   ├── auth.py       # JWT utilities, get_current_user
│   │   │   ├── celery_app.py # Celery app config
│   │   │   ├── redis.py      # Async Redis client
│   │   │   ├── storage.py    # MinIO client wrapper
│   │   │   └── rbac_init.py  # Seed roles and permissions
│   │   ├── services/
│   │   │   ├── test_service.py       # Recursive case collection, settings inheritance
│   │   │   ├── workspace_service.py  # Workspace/team/user/invite business logic
│   │   │   ├── access_service.py     # RBAC access checks
│   │   │   ├── rbac_service.py       # Role/permission management
│   │   │   └── job_dispatcher.py     # Async JobDispatcher (Redis Streams)
│   │   ├── tasks/
│   │   │   ├── __init__.py           # Task registration
│   │   │   ├── cleanup_tasks.py      # Stale run cleanup
│   │   │   ├── notification_tasks.py # Email / Slack / Teams notifications
│   │   │   ├── result_aggregator.py  # Aggregates distributed execution results
│   │   │   └── webhook_tasks.py      # Processes execution engine webhooks
│   │   ├── ai/               # AI failure analysis helpers
│   │   └── runner/           # (legacy runner utilities)
│   ├── alembic/              # DB migrations (2 versions currently)
│   ├── tests/                # Backend test suite (pytest)
│   ├── Dockerfile
│   └── requirements.txt
│
├── execution-engine/          # Node.js Playwright runner
│   ├── src/
│   │   ├── worker.ts          # Redis Stream consumer (main distributed worker, ~32k)
│   │   ├── runner.ts          # Playwright orchestration (~22k)
│   │   ├── server.ts          # Express HTTP server (legacy endpoint POST /run)
│   │   ├── ai.ts              # AI failure analysis
│   │   ├── controller/        # Execution lifecycle controller
│   │   ├── core/              # MinIO + Redis clients
│   │   └── utils/
│   ├── Dockerfile             # Prod image
│   ├── Dockerfile.worker      # Distributed worker image (used by execution-worker service)
│   └── package.json
│
├── frontend/                  # React + Vite + TypeScript SPA
│   ├── src/
│   │   ├── App.tsx            # Router setup (12 routes)
│   │   ├── pages/             # Page-level components
│   │   │   ├── Dashboard.tsx         # Metrics, recent runs
│   │   │   ├── TestMatrix.tsx        # All runs, filters, pagination
│   │   │   ├── TestRunDetails.tsx    # Run result, trace viewer, AI analysis
│   │   │   ├── TestSuites.tsx        # Suite listing
│   │   │   ├── SuiteDetails.tsx      # Cases, sub-modules, run controls
│   │   │   ├── TestBuilder.tsx       # Step recorder/editor
│   │   │   ├── WorkspacePage.tsx     # Teams, members, invites
│   │   │   ├── UsersPage.tsx         # Workspace user management
│   │   │   ├── AdminUsersPage.tsx    # Tenant-admin user/org assignment
│   │   │   ├── Settings.tsx          # Profile, notification prefs
│   │   │   ├── Login.tsx             # JWT login
│   │   │   └── Signup.tsx            # Registration
│   │   ├── components/
│   │   │   ├── TraceTimeline.tsx     # Playwright trace step viewer
│   │   │   ├── PrivateRoute.tsx      # Auth guard
│   │   │   ├── layout/DashboardLayout.tsx
│   │   │   ├── test-builder/         # Step builder sub-components
│   │   │   └── ui/                   # shadcn/ui primitives
│   │   ├── context/AuthContext.tsx   # JWT auth context + provider
│   │   ├── hooks/                    # Custom React hooks
│   │   └── lib/                      # Utility helpers
│   ├── Dockerfile
│   ├── nginx.conf
│   └── package.json
│
├── infrastructure/            # Docker Compose configs + env files
│   ├── docker-compose.yml
│   ├── docker-compose.prod.yml
│   ├── docker-compose.distributed.yml
│   ├── env.local              # Local dev env vars
│   ├── env.dev
│   └── env.prod               # Production env vars (secrets NOT committed)
│
├── alembic/                   # Root-level alembic config (mirrors backend/alembic)
├── info/                      # Developer documentation
│   ├── CODEBASE.md            # ← This file
│   ├── todo.md                # Product roadmap
│   ├── guide.txt              # Original developer guide
│   ├── build.info             # Common docker commands
│   ├── scripting_guide.md     # Test step scripting reference
│   └── test-steps.md          # Step type documentation
└── docs/
```

---

## 5. Data Model

### Entity Relationships

```
Tenant (1) ───── (*) Workspace ───── (*) Project ───── (*) TestSuite ───── (*) TestCase
                     |    |               |                  |    |               |
                     |    └── (*) Team    └── UserProject    |    └── sub_modules  └── steps (JSON array)
                     |             |           Access        └── created_by/updated_by (User FK)
                     └── Users     └── TeamProject
                                       Access (role_id)

TestRun ──── (FK) TestSuite
         ──── (FK, optional) TestCase   ← set when running a single case
         ──── (*) TestCaseResult        ← cascade delete
         ──── (FK, optional) User       ← who triggered the run
```

### Key Database Tables

| Table | Key Fields | Notes |
|-------|-----------|-------|
| `tenant` | `id`, `name`, `owner_id` | Top-level multi-tenancy isolation |
| `workspace` | `id`, `name`, `tenant_id` | Org-level grouping |
| `project` | `id`, `name`, `workspace_id` | Groups test suites |
| `team` | `id`, `name`, `workspace_id` | User groups with project access |
| `testsuite` | `id`, `name`, `parent_id`, `execution_mode`, `settings`, `inherit_settings` | Self-referential hierarchy |
| `testcase` | `id`, `name`, `test_suite_id`, `steps` (JSON) | Contains list of `TestStep` |
| `testrun` | `id`, `status`, `test_suite_id`, `test_case_id`, `browser`, `device`, `ai_analysis` | Execution record |
| `testcaseresult` | `id`, `test_run_id`, `test_name`, `status`, `duration_ms`, `trace_url`, `video_url` | Per-test result |
| `auditlog` | `id`, `entity_type`, `entity_id`, `action`, `changes` (JSON) | Change history |
| `role` / `permission` | RBAC tables | Scope: global / org / project |
| `userworkspace` | `user_id`, `workspace_id`, `role_id` | User↔Workspace mapping |
| `teamprojectaccess` | `team_id`, `project_id`, `role_id` | Team→Project access |
| `userprojectaccess` | `user_id`, `project_id`, `role_id` | Direct user→Project access |
| `workspaceinvitation` | `email`, `token`, `expires_at` | Token-based invite emails |

### Execution Modes (enum `ExecutionMode`)

| Value | Behavior |
|-------|---------|
| `continuous` | All test cases for a suite dispatched as individual jobs; workers run in parallel |
| `separate` | Each test case (or sub-suite) gets its own isolated worker job |
| `parallel` | Declared in enum but **not yet implemented** as a distinct path in worker routing |

### TestStep Structure (JSON in `testcase.steps`)

```json
{
  "id": "step-uuid",
  "type": "goto | click | fill | check | expect | http-request | feed-check | hover | screenshot",
  "selector": "CSS selector or text",
  "value": "input value or URL",
  "params": {}
}
```

---

## 6. Backend API Reference

### Authentication

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/auth/login` | Public | Returns JWT access token |
| POST | `/api/auth/register` | Public | Creates new user |
| GET | `/api/auth/me` | JWT | Current user info |

### Workspaces & Projects

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET/POST | `/api/workspaces` | JWT | List/create workspaces |
| GET/PATCH/DELETE | `/api/workspaces/{id}` | JWT | Workspace CRUD |
| POST | `/api/workspaces/{id}/invite` | JWT | Send workspace invite |
| GET/POST | `/api/workspaces/{id}/teams` | JWT | Teams in workspace |
| GET/POST | `/api/projects` | JWT | List/create projects |
| GET/PATCH/DELETE | `/api/projects/{id}` | JWT | Project CRUD |

### Test Suites & Cases

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/api/suites` | List/create suites |
| GET/PATCH/DELETE | `/api/suites/{id}` | Suite CRUD |
| POST | `/api/suites/{id}/move` | Move suite to another parent |
| GET/POST | `/api/cases` | List/create test cases |
| GET/PATCH/DELETE | `/api/cases/{id}` | Case CRUD |

### Test Runs

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/runs?suite_id=&case_id=&browser=&device=` | Create and queue run(s) |
| GET | `/api/runs?project_id=&limit=&offset=&search=&status=&browser=&device=` | List runs (paginated) |
| GET | `/api/runs/{id}` | Get single run with results |
| DELETE | `/api/runs/{id}` | Delete run + artifacts |
| DELETE | `/api/runs?all=true` or `?run_ids=` | Bulk delete |
| GET | `/api/artifacts/{path}` | Get presigned MinIO URL |
| POST | `/api/runs/{id}/webhook` | Execution engine result callback |
| POST | `/api/runs/{id}/finalize` | Final result + AI analysis + notifications |
| POST | `/api/runs/{id}/force-complete` | Manually complete a stuck run |
| GET | `/api/audit/{entity_type}/{entity_id}` | Audit log for entity |

### Admin (Tenant Admin only)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/admin/users` | List all users |
| POST | `/api/admin/users/{id}/assignments` | Assign user to workspaces/roles |

---

## 7. Execution Flow (End-to-End)

```
1. User clicks "Run" in frontend
   │
   ▼
2. POST /api/runs?suite_id=X&browser=chromium
   │  → Validates project access (EDITOR required)
   │  → Creates TestRun records in DB (PENDING status)
   │     · SEPARATE mode → 1 TestRun per test case
   │     · CONTINUOUS mode → 1 TestRun per suite
   │  → Commits to PostgreSQL
   │
   ▼
3. Celery task dispatched: run_test_suite.delay(run_id)
   │
   ▼
4. Celery worker picks up task (backend/app/worker.py)
   │  → Loads TestRun, TestSuite, TestCases from DB
   │  → Resolves effective settings (headers/params inheritance)
   │  → Sets run.status = RUNNING
   │
   ▼
5. Job dispatch to Redis Streams ('jobs:pending')
   │
   │  SEPARATE mode → dispatch_separate_jobs()
   │    · 1 job per test case (or sub-suite)
   │    · Each job: { job_id, run_id, test_case, browser, device, settings }
   │
   │  CONTINUOUS mode → dispatch_continuous_jobs()
   │    · 1 job per test case (parallelized)
   │
   │  Single case run → dispatch_separate_jobs_legacy()
   │
   │  Progress tracked in Redis: runs:{run_id}:progress
   │    { total, completed, passed, failed, status }
   │
   ▼
6. execution-worker (Node.js) claims job from Redis Stream
   │  → xreadgroup NOACK 'jobs:pending' 'execution-workers' consumer
   │  → Launches Playwright browser
   │  → Iterates steps (goto → click → fill → expect → ...)
   │  → Captures: video recording, Playwright trace, screenshots
   │  → Uploads artifacts to MinIO bucket 'test-artifacts'
   │
   ▼
7. execution-worker POSTs result back
   │  → POST /api/runs/{run_id}/webhook  (result data)
   │  → POST /api/runs/{run_id}/finalize (AI analysis + trigger notifications)
   │
   ▼
8. Backend processes result (test_service.process_test_run_result)
   │  → Creates TestCaseResult records
   │  → Updates TestRun status (passed/failed/error)
   │  → Updates run progress counters
   │
   ▼
9. Celery notification_tasks sends alerts (if enabled & failure)
   │  → Email via SMTP
   │  → Slack webhook
   │  → Teams webhook
   │
   ▼
10. Frontend polls GET /api/runs/{id} to show live status
    → TestRunDetails page displays results, trace viewer, AI analysis
```

---

## 8. Configuration (Environment Variables)

Key variables used in `backend/app/core/config.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | required | PostgreSQL async URL (`postgresql+asyncpg://...`) |
| `CELERY_BROKER_URL` | required | Redis URL for Celery |
| `CELERY_RESULT_BACKEND` | required | Redis URL for Celery results |
| `MINIO_ENDPOINT` | required | MinIO host (e.g., `minio:9000`) |
| `MINIO_PUBLIC_URL` | `http://localhost:9000` | Public URL for presigned artifact URLs |
| `MINIO_ACCESS_KEY` | required | MinIO access key |
| `MINIO_SECRET_KEY` | required | MinIO secret key |
| `MINIO_BUCKET_NAME` | `test-artifacts` | Artifact bucket name |
| `SECRET_KEY` | required | JWT signing secret |
| `ALGORITHM` | `HS256` | JWT algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | JWT token lifetime |
| `OPENAI_API_KEY` | `""` | For AI failure analysis (optional) |
| `USE_DISTRIBUTED_EXECUTION` | `true` | Routes to Redis Streams vs legacy engine |
| `NOTIFICATIONS_ENABLED` | `false` | Master notification switch |
| `EMAIL_NOTIFICATIONS_ENABLED` | `false` | SMTP email alerts |
| `SLACK_NOTIFICATIONS_ENABLED` | `false` | Slack webhook alerts |
| `TEAMS_NOTIFICATIONS_ENABLED` | `false` | Teams webhook alerts |
| `NOTIFY_ON_FAILURE_ONLY` | `true` | Only notify on failures |
| `SMTP_HOST/PORT/USER/PASSWORD` | — | SMTP server config |
| `SLACK_WEBHOOK_URL` | — | Slack incoming webhook |
| `TEAMS_WEBHOOK_URL` | — | Teams incoming webhook |

---

## 9. Frontend Details

- **Tech stack:** React 18, Vite, TypeScript, TanStack Query, React Router v6, Tailwind CSS, shadcn/ui
- **Auth:** JWT stored in context (`AuthContext`). Token attached as `Authorization: Bearer` on all API calls.
- **Data fetching:** TanStack Query used throughout pages.
- **Key frontend pages:**
  - `SuiteDetails.tsx` (112k) — most complex page; handles nested suites, test case list, run controls, settings, audit log
  - `TestRunDetails.tsx` (65k) — run results, step-level results, trace viewer (`TraceTimeline`), AI analysis panel
  - `Settings.tsx` (65k) — notification preferences, profile, workspace settings
  - `WorkspacePage.tsx` (84k) — team management, member invites, RBAC assignment

---

## 10. RBAC Model

Access is checked at three layers:

```
Workspace-level  →  UserWorkspace.role_id  (admin / member)
Project-level    →  UserProjectAccess.role_id  OR  TeamProjectAccess.role_id
TestCase-level   →  UserTestCaseAccess.access_level  (editor / viewer)
```

Access check hierarchy in `access_service.py`:
1. Is user a workspace admin? → full access
2. Does user have direct project access via `UserProjectAccess`?
3. Is user in a team that has project access via `TeamProjectAccess`?
4. Deny.

Minimum role required per action:
- **viewer** — read test cases, view run results
- **editor** — create/edit cases, trigger runs, delete runs
- **admin** — manage members, delete projects

---

## 11. Known Issues & Technical Debt

### 🔴 Critical

| # | Issue | Location |
|---|-------|---------|
| 1 | `/mock/bihar-election` stub endpoint in production code | `backend/app/main.py:55` |
| 2 | Webhook security check is warn-only — NOT enforced | `backend/app/api/endpoints/test_runs.py:399-403` |
| 3 | `/finalize` internal service verification is NOT enforced | `test_runs.py:425-427` |
| 4 | `DELETE /runs?all=true` has no project/user scoping — any authed user can delete ALL runs | `test_runs.py:323-335` |

### 🟡 Medium

| # | Issue | Location |
|---|-------|---------|
| 5 | `PARALLEL` execution mode defined in enum but not routed distinctly | `worker.py:92` |
| 6 | Circular import workaround: `from app.worker import run_test_suite` inside request handler | `test_runs.py:168` |
| 7 | `dispatch_separate_jobs_legacy` and `dispatch_continuous_jobs` are near-identical (code duplication) | `worker.py:254, 332` |
| 8 | `UserWorkspace.role` field deprecated but still present, causing confusion | `models.py:57` |
| 9 | No refresh token implementation — 30-min JWT causes frequent logouts | `config.py:22` |
| 10 | `browser` field on `TestRunBase` is a bare `str`, not validated against known values | `models.py:298` |
| 11 | No rate limiting on `/api/auth/login` or `/api/auth/register` | `auth.py` |
| 12 | CORS is `["*"]` in default config — must verify production override | `config.py:17` |

### 🟢 Low / DX

| # | Issue | Location |
|---|-------|---------|
| 13 | `models.py.bak` stale backup file in production backend | `backend/app/models.py.bak` |
| 14 | `temp_validation.json` debug file in project root | `/temp_validation.json` |
| 15 | Only 2 Alembic migration files — schema drift likely since iterative development | `alembic/versions/` |

---

## 12. How to Add Features

### A. Add a New Test Step Type

1. **Frontend** (`src/pages/TestBuilder.tsx`): Add new step option to the step builder UI
2. **Execution Engine** (`execution-engine/src/runner.ts`): Add case handler in the step execution switch:
   ```typescript
   case 'dblclick':
     await page.locator(step.selector).dblclick();
     break;
   ```
3. **Backend**: No schema changes needed (steps are stored as JSON)
4. Update `info/test-steps.md` with the new step type documentation

### B. Add a New Backend Endpoint

1. Add handler in the appropriate `backend/app/api/` or `backend/app/api/endpoints/` file
2. Register router in `backend/app/main.py` if it's a new module
3. Add access control via `access_service.has_project_access()`
4. Add audit log entry via `session.add(AuditLog(...))`

### C. Add a Scheduled Task

1. DB: Add `Schedule` model to `models.py`, generate Alembic migration
2. Task: Add Celery task to `backend/app/tasks/`
3. Beat config: Register in `backend/app/core/celery_app.py` with cron schedule
4. Infra: `celery_beat` service is already running — no Docker changes needed

### D. Add a New Notification Channel

1. Add config vars to `backend/app/core/config.py`
2. Implement sender in `backend/app/tasks/notification_tasks.py`
3. Add env vars to all docker-compose files and env files

### E. Scale Execution Workers

```bash
# Temporarily scale up
docker compose -f docker-compose.distributed.yml up -d --scale execution-worker=12

# Or set default replicas in docker-compose.yml under execution-worker.deploy.replicas
```

---

## 13. Development Workflows

### Local Development (without Docker for backend/frontend)

```bash
# Frontend (hot-reload)
cd frontend && npm run dev   # http://localhost:5173

# Backend (hot-reload)  
cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Start infrastructure services only
cd infrastructure && docker compose --env-file env.local up -d postgres redis minio

# Celery worker (separate terminal)
cd backend && celery -A app.core.celery_app worker --loglevel=info -Q main-queue
```

### Database Migrations

```bash
# Create a new migration
cd backend && alembic revision --autogenerate -m "description"

# Apply migrations
cd backend && alembic upgrade head

# Manual schema patch (emergency only)
docker exec infrastructure-postgres-1 psql -U user -d quality_intelligence -c "ALTER TABLE ..."
```

### Viewing Logs

```bash
cd infrastructure
docker compose logs -f backend
docker compose logs -f celery_worker
docker compose logs -f execution-worker
docker compose logs -f --tail=50 execution-engine
```

---

## 14. Product Roadmap (Summary)

See `info/todo.md` for full detail.

| Phase | Goal | Key Features |
|-------|------|-------------|
| Phase 1 | Usability | Journey recorder, persona system, auto-login/session, smart retry |
| Phase 2 | Production protection | Scheduled runs, API contract validation, deployment comparison, visual regression |
| Phase 3 | Differentiation | OpenTelemetry trace correlation, root cause classification, selector self-healing, CI/CD release guard |
