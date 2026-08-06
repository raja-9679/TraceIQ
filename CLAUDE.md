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

# Celery worker (main queue)
celery -A app.core.celery_app worker --loglevel=info -Q main-queue

# Celery aggregator worker (separate queue for result aggregation)
celery -A app.core.celery_app worker --loglevel=info -Q aggregator-queue

# Celery beat (scheduled tasks)
celery -A app.core.celery_app beat --loglevel=info

# Run tests
pytest

# Run a single test file (tests live at tests/ root, tests/e2e/, or tests/integration/)
pytest tests/test_stale_run_detection.py
pytest tests/e2e/test_parallel_execution.py
pytest tests/integration/test_async_execution.py

# Database migrations
alembic revision --autogenerate -m "description"
alembic upgrade head
```

### Frontend (run from `frontend/`)

```bash
npm run dev          # Hot-reload dev server at http://localhost:5173
npm run build        # Production build
npm run build:local  # Build with local env
npm run build:dev    # Build for dev environment
npm run build:prod   # Build with production mode
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
├── settings_models.py    # Pydantic settings models for inheritance
├── api/
│   ├── auth.py           # JWT login/register/refresh; refresh token issuance
│   ├── admin.py          # Tenant-admin user/assignment endpoints
│   ├── settings.py       # Settings endpoints
│   ├── api_keys.py       # Workspace API key CRUD (service accounts)
│   ├── workspace_webhooks.py  # Outbound webhook registry
│   ├── visual_baselines.py    # Visual regression baselines (Phase B scaffold)
│   ├── workspaces.py     # Workspace + team + invite management
│   ├── projects.py       # Project CRUD
│   └── endpoints/
│       ├── test_suites.py
│       ├── test_cases.py
│       ├── test_runs.py  # Run lifecycle: create, webhook, finalize, force-complete
│       ├── schedules.py
│       └── websockets.py
├── ai/                   # OpenAI / Anthropic failure analysis + heal
│   ├── providers.py      # LLMProvider abstraction (OpenAI / Anthropic / Null)
│   ├── engine.py         # AIEngine using the provider abstraction
│   └── trace_parser.py
├── runner/               # In-process Playwright runner (Celery path)
│   ├── browser_manager.py
│   ├── runner.py
│   └── smart_page.py
├── schemas/              # Pydantic request/response schemas
├── core/
│   ├── config.py         # Pydantic settings (all env vars)
│   ├── auth.py           # JWT/API-key principal, password hashing, token gen
│   ├── database.py       # Async SQLAlchemy engine + session
│   ├── celery_app.py     # Celery config + beat schedule
│   ├── limiter.py        # Rate limiting (slowapi)
│   ├── rbac_init.py      # Bootstraps default roles/permissions
│   ├── redis.py
│   └── storage.py        # MinIO client wrapper
├── services/
│   ├── access_service.py # RBAC access checks
│   ├── rbac_service.py   # Role/permission lookups
│   ├── job_dispatcher.py # Redis Streams job dispatch
│   ├── test_service.py   # Recursive case collection, settings inheritance
│   └── workspace_service.py
└── tasks/
    ├── cleanup_tasks.py       # Stale run cleanup (Celery beat)
    ├── notification_tasks.py  # Email / Slack / Teams
    ├── result_aggregator.py   # Aggregates distributed results
    ├── schedule_tasks.py      # Cron-driven suite scheduling
    ├── outbound_webhook_tasks.py  # Fan-out registered workspace webhooks on run events
    └── webhook_tasks.py       # Processes engine webhooks
```

---

## Frontend structure

React 19 + Vite + TypeScript + TanStack Query + React Router v7 + Tailwind CSS + shadcn/ui.

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

## AI-agent integration surface (Phase A)

TraceIQ exposes integration points so AI coding agents can trigger and consume regression runs:

- **API key auth** — `POST /api/workspaces/{id}/api-keys` mints a `tiq_...` key. Pass via `X-API-Key` header on any endpoint; `get_current_principal` accepts either JWT or API key.
- **Git context on runs** — `POST /api/runs` accepts a JSON body with `git_commit`, `git_branch`, `git_pr_url`, `git_repo`, `triggered_by`, `agent_id`. These are stored on `TestRun` and surfaced in the finalize webhook payload.
- **Refresh tokens** — `POST /api/auth/refresh` (rotation-on-use). Family-based revocation on token replay.
- **Outbound webhooks** — `POST /api/workspaces/{id}/webhooks`. HMAC-SHA256 signed (`X-TraceIQ-Signature`). Fan-out happens in `app.tasks.outbound_webhook_tasks.dispatch_run_webhooks`.
- **LLM provider abstraction** — `app/ai/providers.py` (Python) and `execution-engine/src/llm-provider.ts` (TS). Switch via `LLM_PROVIDER=anthropic|openai|gemini|ollama|openai-compatible` + `LLM_MODEL=...`.
- **LLM usage metering** — every provider call records tokens/latency to `LLMUsageEvent` (context via `app/services/llm_usage.py:llm_call_context`); workers report via `POST /api/internal/llm-usage` (X-Worker-Secret). Stats at `GET /api/workspaces/{id}/llm-usage`; monthly totals roll into `UsageRecord` (metric `llm_tokens`) and are capped by the `monthly_llm_tokens` plan-limit key (absent/0 = unlimited — over-cap calls are skipped and fall back to heuristics). Frontend: `/ai-usage` page.
- **PARALLEL execution mode** — fully routed through `dispatch_parallel_jobs` in `app/worker.py`. Tune fan-out via `PARALLEL_MAX_CONCURRENCY`.
- **Local dev worker** — `POST /api/runs` body `local_worker_id` pins a run to a developer's polling worker (`npm run worker:local` in `execution-engine/`, API-key auth via `GET /api/jobs/poll` + `POST /api/jobs/result`) so agents/CI can test `localhost` before deploy. v1: single-test jobs, no artifact upload.
- **Mobile app testing (Phase MOB)** — cases with `executor=mobile_appium` run native Android/iOS journeys via Appium. Upload binaries at `POST /api/projects/{id}/app-builds` (MinIO-backed `MobileAppBuild` registry); pin a run via `app_build_id` in the `POST /api/runs` body. Mobile jobs ride a dedicated `jobs:mobile:pending` stream consumed by `execution-engine/src/mobile-worker.ts` (compose profile `mobile`; Appium at `APPIUM_URL`; needs an emulator/device attached). Step types are `mobile-*` in the `/api/step-types` catalog. Screenshots + screen recording upload to MinIO (video records at half resolution — emulator encoders fail at native size; `MOBILE_VIDEO_SIZE` overrides). iOS via device cloud only. Sample app: `todo-mobile/` (Express API + Flutter and Kotlin clients; its README documents the four Appium-testability rules).
- **Instance settings (DB-over-env)** — admin-editable runtime config stored in
  the `instance_settings` table (secrets Fernet-encrypted), resolved by
  `app/services/instance_settings.py:effective()` with a ~15s TTL cache:
  DB override wins, environment is the fallback; DB unavailable degrades to
  env-only. Only keys in its REGISTRY are storable — bootstrap config
  (DATABASE_URL, Redis, SECRET_KEY) is env-only by construction. Consumers:
  notifications/SMTP, LLM providers (module `provider` is now a re-resolving
  proxy), storage (lazy client, restart-required), OIDC, net_guard policy,
  retention. API: `/api/admin/instance-settings` (+ test-email / test-llm),
  guarded by `get_current_instance_admin` = admin of the FIRST tenant (plain
  tenant-admin is not enough — self-registration makes everyone admin of
  their own tenant). UI: Settings → "Instance (Admin)" tab. Execution workers
  still read their own env (LLM keys, MinIO) — worker-side config is not
  DB-driven.
- **Multi-provider AI registry** — `llm_provider_config` table: several saved
  LLM providers (Fernet-encrypted keys), multiple active at once, one default.
  `app/ai/providers.py:get_provider(provider_id)`; the module-level `provider`
  resolves the DB default first and falls back to the legacy single-provider
  instance settings when NO rows exist. Admin CRUD + per-provider test at
  `/api/admin/llm-providers`; user-facing picker list at
  `/api/llm-providers/active`; `POST /api/runs/{id}/analyze` re-runs failure
  analysis with a chosen `provider_id`. Usage events attribute per config
  *name* (so `/ai-usage` splits per saved provider). Workers still env-only.
- **Enterprise auth** (see `docs/ENTERPRISE_AUTH.md`) —
  `MFA_REQUIRED` instance setting: password logins must enrol TOTP before
  getting a session (challenge tokens carry `mfa_setup_pending` and are
  refused as session tokens by `_user_from_jwt` — as is `mfa_pending`; that
  rejection is a security fix, challenge tokens used to pass as full JWTs).
  Signup offers an optional enrollment step. `ADMIN_EMAIL`/`ADMIN_PASSWORD`
  env bootstrap a first-boot instance admin (create-only, never a rolling
  password authority). `users.is_instance_admin` is the explicit operator
  grant (`/api/admin/instance-admins`); first-tenant-admin remains a fallback.
  `PASSWORD_LOGIN_DISABLED` = SSO-only mode (refuses without OIDC saved;
  instance admins keep password break-glass, UI at `/login?password=1`).
  SSO JIT + LDAP + register all provision through
  `app/services/user_provisioning.py` (tenant + workspace + pending invites).
  LDAP login (`app/services/ldap_auth.py`, ldap3, bind-as-user, instance
  settings group `ldap`) at `POST /api/auth/ldap/login` — backend image
  rebuild needed for the ldap3 dep.
- **MCP server v2** — `integrations/mcp-server/` (pkg `traceiq-mcp-server`
  0.2.0, FastMCP, `mcp>=1.10,<2`): 51 tools, each with Pydantic input/output
  models → `outputSchema` + validated structured content. stdio
  (`traceiq-mcp`) + streamable HTTP (`traceiq-mcp-http`, POST /mcp on 8088,
  per-request `X-API-Key`/Bearer). Add a tool = output model in
  `schemas/<domain>.py` + client method in `client.py` + `@mcp.tool()` fn in
  `tools/<domain>.py` + name in `smoke_test.py:EXPECTED_TOOLS`
  (`python -m traceiq_mcp.smoke_test` asserts the full typed inventory).
  Impact analysis v2 returns per-case `suggested_action: run|review|
  run_then_review` + reasons; passing runs with `git_commit` stamp
  `TestCase.last_validated_commit` and `TestCaseResult.test_case_id`
  (aggregator; migration `f1a2b3c4d5e6`). Write policy: CaseProposal queue +
  workspace auto-apply threshold; accept/reject human-only. Suite settings
  (inherited headers/params/auth) are agent-visible: `create_suite` takes
  `settings`/`inherit_settings`, and changes go through
  `propose_update_suite_settings` (CaseProposalAction.UPDATE_SUITE_SETTINGS,
  migration `a3b4c5d6e7f8`) — never auto-applied, human review always. Case
  proposals also honor `tags`/`priority` in payload/patch.
- **GitHub Action** — `integrations/github-action/`. Gates PRs on TraceIQ regression results.

See `SCOPE_NOTES.md` for what's intentionally deferred (semantic selectors, full visual diff, browser recorder, test-from-intent).

## Known issues to be aware of

Historical issues now FIXED (kept here so stale docs elsewhere don't mislead):
run deletion is access-checked and editor-gated (single, `run_ids`, and `all=true`
paths); webhook/finalize enforce `X-TraceIQ-Secret` (403, not warn-only); CORS
defaults to localhost dev origins; the `/mock/bihar-election` stub is gone;
refresh tokens exist (rotation + family revocation); PARALLEL mode is routed.

Also FIXED 2026-07-28 (verified against a real Postgres and real image builds):
`is_active` is now checked in both the JWT and API-key principal paths, so
deactivating a user immediately revokes their tokens *and* their API keys;
`finalize` claims `TestRun.finalized_at` with a conditional UPDATE before
dispatching its six side-effecting tasks, so a retried webhook no longer sends
duplicate customer emails or re-fires outbound CI webhooks; `verify=False` is
gone from the backend and user-supplied URLs go through
`app/core/net_guard.py`; `Settings.validate_for_deployment()` refuses to boot a
production instance on weak/placeholder secrets, `minioadmin`, or CORS `*`.

Still open:
- Worker image bakes code at build time — new step types need an image rebuild.
  Workers now HARD-FAIL an unknown step type (the error lists the worker's
  supported types) rather than skipping it silently, so a backend-newer-than-
  worker version skew turns those cases red instead of faking a pass.
- `celery_beat` is required for run finalization (drains `jobs:results` every 2s)
- Credential hygiene: committed `.env` history was never scrubbed, and a
  tracked database dump at the repo root carries account data that should not be
  in version control. Both need a `git filter-repo` rewrite, and the affected
  credentials need rotating first — rotation is independent of the rewrite and
  should not wait for it. Treat anything that has ever been committed here as
  disclosed.
- **The Alembic chain cannot build a schema from scratch.** The baseline
  revision `1f266105057e` is an empty `pass` stub — it was stamped onto a
  database that `SQLModel.metadata.create_all()` had already built, and no
  revision creates the core tables. `alembic upgrade head` against an empty
  database fails at `CREATE INDEX ... ON testrun`. Use
  `python scripts/bootstrap_db.py` (what the container entrypoint runs), which
  creates the schema and stamps head on an empty database and upgrades an
  existing one. A real squashed initial migration is still worth writing.
- `executionmode` enum labels are lowercase in Postgres while `teststatus`
  labels are uppercase. Any raw SQL touching either must account for that; the
  `finalized_at` backfill uses `UPPER(status::text)` for exactly this reason.

---

## Self-hosted distribution (community edition)

Users pull prebuilt images rather than building from source. See
`SELF_HOSTING.md` for the user-facing guide.

- `infrastructure/docker-compose.community.yml` — no `build:` contexts; pulls
  `ghcr.io/<owner>/traceiq-{backend,frontend,execution-worker}`.
- `infrastructure/env.community.example` + `traceiq-setup.sh` — the setup script
  generates distinct secrets with `openssl rand -hex` and writes a `.env` at
  mode 600.
- **Every secret uses `${VAR:?message}` with no default.** A default in a
  published image is a shared secret across every deployment; for `SECRET_KEY`
  that means anyone could forge a JWT against any instance. CI asserts that the
  compose file still *refuses* to render with no secrets set — if that check
  ever passes, a default has crept back in.
- `.dockerignore` in both `backend/` and `frontend/` is load-bearing, not
  housekeeping. `backend/.env` was previously baked into the image (config.py
  sets `env_file = ".env"`), and Vite inlines `VITE_API_BASE_URL` at build time,
  so a developer's local `.env` would hardcode an API URL wrong for every user.
- The frontend defaults to a **relative** `/api` and nginx proxies it, so one
  image works on localhost, a LAN host, or a domain. `Login`, `Signup`,
  `AuthContext`, and `PublicStatus` previously read
  `import.meta.env.VITE_API_BASE_URL` with no fallback, which produced
  `undefined/auth/login` in a prebuilt image; they now all import `API_BASE_URL`
  from `lib/api.ts`. Use `apiWebSocketUrl()` for WebSocket URLs.
- The nginx proxy target is also runtime config: `frontend/nginx.conf` ships as
  `/etc/nginx/templates/default.conf.template` and the base image envsubsts
  `${BACKEND_URL}` at container start (default `http://backend:8000`; Dockerfile
  ENV). Split deployments set `FRONTEND_PROXY_BACKEND_URL` in the community
  `.env` or `-e BACKEND_URL=...` on `docker run`.
- Images are published by `.github/workflows/release-images.yml`. Tag `v*.*.*`
  for `:latest`; pushes to `main` publish `:edge` only. Docker Hub is a second
  publish target that activates only when the `DOCKERHUB_USERNAME` +
  `DOCKERHUB_TOKEN` repo secrets exist; self-hosters switch registries via
  `TRACEIQ_REGISTRY` in `.env` (default `ghcr.io/raja-9679`).
- `install.sh` (repo root) is the one-line installer
  (`curl … /install.sh | bash`): fetches the three community files, runs
  `traceiq-setup.sh`, pulls, starts, waits for `/health/ready`. Idempotent —
  an existing `.env` is never overwritten.
- Secret/setting precedence everywhere: **user-supplied → auto-generated**.
  `traceiq-setup.sh` honors pre-set env vars and has `--interactive` (prompts
  via `/dev/tty` so it works under curl|bash); `install.sh` adds
  `--configure` (write `.env`, stop before starting); the AIO entrypoint
  accepts `-e` secrets on first boot only and warns on later mismatches.
  User-supplied `POSTGRES_PASSWORD`/`REDIS_PASSWORD` are restricted to
  `[A-Za-z0-9._~-]` because compose embeds them in connection URLs verbatim.
- `infrastructure/aio/` is the all-in-one **evaluation** image
  (`traceiq-aio`): postgres+redis+minio+backend+celery+worker+nginx under
  supervisord, single `docker run`, secrets generated on first boot into the
  `/data` volume (never baked in). It builds from the **repo root** context;
  the root `.dockerignore` is deny-by-default and is the only thing keeping
  `.git` and `dump.sql` out of that context — treat it like the other two.
  Chromium-only, one worker; production stays on the compose file.
- Mobile testing ships as an optional `mobile` profile in the community
  compose (emulator + appium + mobile-worker; worker runs
  `node dist/mobile-worker.js` from the same execution-worker image). The
  emulator needs `/dev/kvm` and runs privileged (deliberately outside the
  hardening anchor). Device-cloud mode (`MOBILE_DEVICE_PROVIDER`) works
  without KVM. See SELF_HOSTING.md → "Optional: mobile app testing".
- Base images are pinned to `python:3.11-slim-bookworm`: the un-suffixed tag
  floated to Debian trixie, which drops `postgresql-15` and renames chromium
  runtime libs that `backend/Dockerfile` lists manually.
- **Source protection in published images** (deterrence, not absolute):
  backend + AIO compile `app/` to 3.11 bytecode and delete the `.py` files
  (`ARG STRIP_SOURCE=1`; build with `0` for readable tracebacks). `app/alembic`
  and `scripts/` stay source — alembic globs `versions/*.py` and the
  entrypoint runs scripts by path. No working decompiler exists for 3.11+
  bytecode, but it can be disassembled. Worker/engine `dist/` JS is
  esbuild-minified per file (no bundling). `LICENSE-COMMUNITY.md` carries the
  community-edition terms (draft — counsel review pending). Note `app/` and
  `app/core/` are NAMESPACE packages (no `__init__.py`) — tooling that infers
  module names from packages (e.g. cythonize) gets them wrong there.
