# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is TraceIQ

TraceIQ is a UI Testing & Automation SaaS platform. Teams record Playwright browser test journeys, organise them into hierarchical suites, execute them in distributed workers, and receive AI-powered failure analysis. It is multi-tenant with RBAC at Workspace → Team → Project → TestCase levels.

A comprehensive architecture and data model reference lives in `info/CODEBASE.md`.

**Resuming work in progress: read `info/HANDOFF.md` first.** It records where the
regulated-readiness effort stands (`info/REGULATED_READINESS.md`), what is next,
how to run the test suites on a fresh machine, and the traps that are expensive
to rediscover.

---

## Services

All services run via Docker Compose from the `infrastructure/` directory.

| Service | Stack | Port |
|---------|-------|------|
| `backend` | FastAPI + SQLModel + Celery | 8000 |
| `execution-worker` (×4) | Node.js + Playwright | — |
| `execution-engine` | Node.js legacy engine — DEPRECATED, compose profile `legacy` only | 3000 |
| `postgres` | PostgreSQL 15 | 5432 |
| `redis` | Redis 7 | 6379 |
| `object-store` | RustFS (S3-compatible; network alias `minio`) | 9000 |
| `celery_worker` / `celery_aggregator` / `celery_beat` | Python Celery | — |
| `pgadmin` | pgAdmin 4 | 8014 |

The object store is RustFS (`rustfs/rustfs:1.0.1`, pinned by digest; console
disabled; volume `object_store_data`). It replaced MinIO in 2026-10; app
settings keep their `MINIO_*` names and the server reads `MINIO_ROOT_USER` /
`MINIO_ROOT_PASSWORD`. Old MinIO data is copied with
`backend/scripts/migrate_object_store.py` — see `SELF_HOSTING.md` → "Object
storage".

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
# Local dev without Docker (requires postgres/redis/object-store running)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Celery worker (main queue)
celery -A app.core.celery_app worker --loglevel=info -Q main-queue

# Celery aggregator worker (separate queue for result aggregation)
celery -A app.core.celery_app worker --loglevel=info -Q aggregator-queue

# Celery beat (scheduled tasks)
celery -A app.core.celery_app beat --loglevel=info

# Run tests. The host may have Python >3.11, for which psycopg2-binary ships
# no wheel; ./run-tests.sh borrows the backend image (3.11 + every runtime dep)
# and mounts the tree over it. Plain `pytest` works if your venv is 3.11.
./run-tests.sh                      # whole unit suite (what CI runs)
./run-tests.sh tests/test_redaction.py -q
pytest

# Tests that need a real Postgres. Creates a SCRATCH database inside the
# running postgres container, bootstraps the schema, runs pytest, drops it.
# tests/integration/ modules skip themselves unless TRACEIQ_LIVE_DB is set,
# which this script does. Not in CI yet (no Postgres service — workstream I1).
./run-tests-live.sh
./run-tests-live.sh tests/integration/test_federated_provisioning_db.py -q

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
npm run dev          # DEPRECATED legacy engine (server.ts, port 3000)
npm run dev:worker   # Distributed worker in dev mode
npm run build        # TypeScript compile to dist/
npm test             # node:test via ts-node; *.test.ts are excluded from the build
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
5. `execution-worker` (Node.js) claims jobs via `XREADGROUP`, runs Playwright steps, uploads artifacts to the object store
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
│   └── storage.py        # S3 object-store wrapper (`minio_client` / `object_store`)
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
├── server.ts      # DEPRECATED legacy engine (POST /run); removal planned
├── ai.ts          # OpenAI failure analysis
├── controller/    # Run lifecycle management
└── core/          # Object store + Redis client setup
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
- **Mobile app testing (Phase MOB)** — cases with `executor=mobile_appium` run native Android/iOS journeys via Appium. Upload binaries at `POST /api/projects/{id}/app-builds` (object-store-backed `MobileAppBuild` registry); pin a run via `app_build_id` in the `POST /api/runs` body. Mobile jobs ride a dedicated `jobs:mobile:pending` stream consumed by `execution-engine/src/mobile-worker.ts` (compose profile `mobile`; Appium at `APPIUM_URL`; needs an emulator/device attached). Step types are `mobile-*` in the `/api/step-types` catalog. Screenshots + screen recording upload to the object store (video records at half resolution — emulator encoders fail at native size; `MOBILE_VIDEO_SIZE` overrides). iOS via device cloud only. Sample app: `todo-mobile/` (Express API + Flutter and Kotlin clients; its README documents the four Appium-testability rules).
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
  still read their own env (LLM keys, object store) — worker-side config is not
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
  **SAML 2.0 (workstream F3, 2026-09-07)** — `app/services/saml_auth.py`
  (python3-saml, `strict`; no apt deps — `xmlsec` ships manylinux wheels,
  pinned with lxml because a libxml2 mismatch segfaults at import) behind
  `/api/auth/saml/{metadata,login,acs}`, instance settings group `saml`,
  `docs/ENTERPRISE_AUTH.md`. SP-initiated: AuthnRequest id parked in Redis
  under a nonce in a signed RelayState so the ACS insists on `InResponseTo`;
  assertion ids cached in Redis for their validity window (replay); Redis down
  fails closed. Destination/Recipient are checked against the CONFIGURED ACS
  URL, never request headers. **python3-saml only compares `InResponseTo`
  when handed a request id** (no `rejectUnsolicitedResponsesWithInResponseTo`
  like the PHP toolkit), so `process_acs` refuses an unsolicited Response that
  carries one itself — without that, IdP-initiated mode would accept a
  replayed SP-initiated Response. Settings validated (metadata fetched via
  net_guard) at save time. `sso/status` now returns `providers[]`; the login
  page renders a button per protocol; SSO-only mode accepts SAML or OIDC.
  Tested with a self-signed mock IdP (`tests/test_saml_auth.py`), not yet
  against a real tenant. No SLO, no artifact binding.
  **Federated provisioning (workstream F1)** — `app/services/federation.py`
  decides where an IdP-authenticated user lands, replacing the old
  "SSO/LDAP call `provision_standalone_user`" behaviour that gave every
  federated user their own Tenant *and made them its Tenant Admin* (SSO for a
  500-person org produced 500 isolated tenants). Instance settings group
  `federation`: `FEDERATED_PROVISIONING_MODE` =
  `standalone` (legacy, still the DEFAULT so existing installs are unchanged) |
  `workspace` (join `FEDERATED_WORKSPACE_ID`, no tenant of their own) |
  `deny` (no JIT at all — 403 for unknown emails), plus
  `FEDERATED_DEFAULT_ROLE` and `FEDERATED_GROUP_{ROLE,TEAM}_MAP`.
  Two non-obvious commitments: **misconfiguration fails closed** (503, never a
  silent fall back to standalone — that would recreate the bug in a deployment
  that believes it is fixed), and **group maps are re-applied on EVERY login**
  via `sync_federated_access`, so losing an IdP group actually removes the role.
  With no role map configured the role is never overwritten, so an in-app
  promotion survives. Group→role is restricted to `Workspace Admin`/`Workspace
  Member` — directory groups are often self-service, so allowing `Tenant Admin`
  would be a privilege-escalation path. Teams are the reason group→team exists:
  `Workspace Member` carries no project access by design.
  Also validated at save time in `PUT /api/admin/instance-settings` (including
  that the workspace exists) so a typo surfaces on the form, not at 9am.
  OIDC groups come from `OIDC_GROUPS_CLAIM` (default `groups`); LDAP from
  `memberOf`, DN-reduced to CNs. The SSO callback now also refuses a
  deactivated account instead of issuing tokens for it.
  **SCIM 2.0 (workstream F2)** — `app/api/scim.py` + `app/services/scim.py`
  (pure wire-format mapping), migration `d9e0f1a2b3c4` adds
  `users.scim_external_id` / `team.scim_external_id`. Mounted at BOTH
  `/scim/v2/...` and `/api/scim/v2/...` on purpose: the shipped nginx only
  proxies `/api/`. Guarded by the `SCIM_TOKEN` instance setting — blank means
  the endpoints answer **404**, so an unconfigured instance is not an open
  provisioning API; comparison is `compare_digest`.
  Users land in `FEDERATED_WORKSPACE_ID` (SCIM refuses to run without it) and
  everything is scoped to that workspace. `DELETE` deactivates and never
  destroys (IdPs DELETE routinely on offboarding; a hard delete would orphan
  runs/results/audit history), and deactivation **revokes live refresh tokens** —
  without that, deactivation only stops new logins while an existing session
  keeps rotating. A `POST` for an existing email **adopts** that account rather
  than 409ing forever, so SCIM can be switched on for an instance that already
  has users. Groups map to Teams; deleting a Group never touches its members'
  accounts. Note SCIM Groups and `FEDERATED_GROUP_TEAM_MAP` both write team
  membership and will fight over the same team — use one or the other.
  Provider quirks are the real work: Okta sends `{"op":"replace","value":
  {"active":false}}` with no path, Entra sends `{"op":"Replace","path":"active",
  "value":"False"}` — a **string**, and `bool("False")` is `True`, which would
  silently leave a deprovisioned user active. `app/services/audit.py:build_entry`
  gained explicit `actor_type`/`actor_label` overrides for this (SCIM has no
  TraceIQ principal; recording it as `system` makes "who removed this person's
  access" unanswerable).
- **MCP server v2** — `integrations/mcp-server/` (pkg `traceiq-mcp-server`
  0.2.0, FastMCP, `mcp>=1.10,<2`): 53 tools, each with Pydantic input/output
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
- **Targeted runs (2026-10)** — `POST /api/runs` body `case_ids` runs exactly
  those cases (each with its own suite's settings; must be inside `suite_id`;
  ≤500; not with `case_id`/`tags`; quarantined ones dropped, all-quarantined
  → 400). Runs carry `selection_mode` (`full|targeted|case`) and
  `selection_total`; the gate adds an always-passing `coverage` check
  ("targeted — N of M cases ran"). Impact analysis takes `suite_id`. The
  GitHub Action's `mode: targeted` falls back to the full suite on any
  unmapped file, no match, all-quarantined or an impact error.
- **Flake owners + quarantine expiry (2026-10)** — `FlakeRecord` gains
  `owner_user_id` and `quarantine_{source,reason,expires_at}`/`quarantined_{at,by_id}`
  (migration `b8c9d0e1f2a3`; existing quarantines got 14 days). Rules in
  `app/services/flake_policy.py`: every quarantine expires (default 14, max 90
  days); past expiry the case **runs again** but stays flagged "expired" and is
  never silently re-quarantined; score-based auto-release only undoes `auto`
  (or legacy NULL-source) quarantines, never a manual one. Every "is it
  quarantined?" query must use `active_quarantine_clause()` / `is_active()`.
  `POST /flakes/{id}/quarantine` needs `{reason, days?, owner_user_id?}`;
  `PUT /flakes/{id}/owner`; `GET /flakes/owner-candidates` (editors only);
  all audited. Retried tests carry `result_payload.attempts` (worker
  `summariseAttempt`; redacted in both twins) — needs a worker rebuild.
- **Heal candidates + DOM redaction (WP14c / W5, 2026-10)** — every heal
  (runtime: `execution-engine/src/core/selector-heal.ts`; proactive:
  `app/tasks/heal_tasks.py`) ranks several candidates: deterministic strategies
  over the REDACTED DOM (test-id > id > role+name > aria-label/placeholder/name
  > text > CSS path without nth-child) plus the LLM's pick, scored 0.35
  stability + 0.35 similarity + 0.15 LLM + 0.15 uniqueness; only `matches == 1`
  is applicable and only that is ever applied at run time. Twins:
  `core/heal-candidates.ts` ↔ `app/services/heal_candidates.py` (keep weights,
  strategy names and selector forms identical). **No DOM reaches an LLM
  unredacted**: `redactDom`/`redact_dom` (twins in redact.ts/redaction.py —
  strips scripts/styles/comments/meta, hidden inputs, input values, text in
  password/token-ish elements and project `mask_selectors`, handlers, data:
  URIs, URL queries, long attrs) and `redactPageSource` for mobile; ai.ts
  re-redacts whatever it is given. `SelectorHealProposal` gains `candidates`,
  `chosen_selector`, `dom_snapshot_key`, `recheck` (migration `d032b3c4d5fa`);
  the snapshot is re-redacted with the project policy by the aggregator and
  stored at `runs/{run}/heal/…html` (object store, deleted with the run; not
  stored at capture level `none`). Accept takes `{selector}` (must be one of
  the proposal's candidates, else 400) and re-matches it against the snapshot
  with the lxml matcher in heal_candidates.py — 409 unless exactly one element
  (unsupported selector forms are refused too; no snapshot → allowed,
  `recheck.status=unavailable`). `POST /heal-proposals/{id}/recheck` is the dry
  run. The list endpoint is now filtered by project access. Needs a worker
  rebuild.
- **Manual testing + release sign-off (WP12d, 2026-10)** — `executor="manual"`
  cases hold only `manual-step` steps (`value`=instruction,
  `params.expected`); the step type makes the case manual and mixing is a 400.
  Manual cases are **never dispatched**: `run_test_suite` and `_expand_cases`
  drop them (all-manual selection → run ERROR), a manual TestRun is ignored by
  the Celery task, and the engine's `core/manual-guard.ts` reports one that
  arrives anyway as not executed (no browser). A manual run is a normal
  `TestRun(executor=manual)` + one `TestCaseResult(result_kind=manual)` per
  case (steps snapshotted), so history/analytics/gate see it. It stays
  **PENDING** while open (RUNNING would be swept as stale and count against
  the concurrency cap). Outcomes passed/failed/blocked/skipped map to
  PASSED/FAILED/ERROR/PENDING (TestStatus is a native enum); notes, per-step
  results, `tester_id` (names looked up, never stored) and screenshot
  attachments (magic-byte sniffed, 5 MB, under `runs/<id>/manual/`) live in
  `result_payload`. API `app/api/manual_testing.py` (editor to record).
  `ReleaseSignoff` (project, label, run_ids/case_ids as JSON, verdict, signer):
  editor, never an API key; with `require_separate_approver` (workspace or
  instance floor) the signer may not have started or recorded any covered
  manual run. Gate check `require_manual_signoff` (off by default) = newest
  sign-off (for the commit) is an approval. Migration `ce42b3c4d5f4`.
- **Cost in money (WP12d, 2026-10)** — instance settings group `cost`:
  `COST_CURRENCY`, `COST_LLM_PRICES` (new setting type `json`, validated on
  save in `app/services/cost.py`), `COST_BROWSER_MINUTE_PRICE`. All empty by
  default: no currency → no money anywhere; a model without a price is "not
  priced" (amount null), never 0. Decimal math, rounded once half-up to the
  currency's minor unit. Browser-minutes = summed `TestCaseResult.duration_ms`
  of automated, non-local runs — an approximation and labelled so.
  `GET /api/costs/workspaces/{id}` covers only projects the caller can read;
  LLM use with no project only for workspace admins. `ProjectCostBudget`
  (project admin) + hourly beat `check_project_budgets`: 80%/100% alerts via
  the project's own recipients/webhooks, deduped by the
  `CostBudgetAlert(project, month, threshold)` unique key (row committed
  before sending; one message when both thresholds are crossed at once).
- **Identity matrix + access-control matrix (WP4, 2026-10)** — migration
  `c4a2b3c4d5e4`. `POST /api/runs` body `persona_ids[]` (+ `include_anonymous`)
  repeats the selection once per identity, sharing `TestRun.matrix_group`;
  `TestRun.anonymous` strips the project session AND credential headers
  (`access_matrix_runtime.strip_identity`). Personas validated by the shared
  `access_matrix.validate_personas` (same rules as `persona_id`).
  `AccessMatrix` (rows JSON: `case|request|page` targets × persona/`anonymous`
  columns, cells `allow|deny|skip` + optional status) at
  `/api/projects/{id}/access-matrices`, `/api/access-matrices/{id}[/run|/runs]`,
  `/api/access-matrix-runs/{id}`. Execution: case rows → ordinary persona
  TestRuns; request/page rows → `job_type=access_check` side jobs
  (`execution-engine/src/access-check.ts`) that only OBSERVE; verdicts are
  computed in `app/services/access_matrix.py` and folded in by the
  self-rescheduling `collect_access_matrix_run` task (45 min deadline →
  `inconclusive`). Rules: request/page rows need project security testing
  enabled + host on `security_settings.allowed_domains`; any non-GET/HEAD/OPTIONS
  (including the ID-swap owner request) needs `authorized: true`; the worker
  re-checks both and only writes `access_check:result:{cell_id}`. A DENY passes
  only on a deny-set status (401/403/404 + the cell's status) or an absent
  element on a page that loaded — timeouts/network/5xx/3xx are `inconclusive`.
  A granted DENY upserts a `SecurityFinding` (`scan_type=access_matrix`,
  `access_control`, high, fingerprint matrix|row|persona; resolved when fixed,
  reopened on regression, false_positive sticks); a failed ALLOW is only a
  summary regression. Evidence redacted by the `redactAccessObservation` /
  `redact_access_observation` twins; page screenshots at
  `access-matrix/{run}/{cell}.png` (purge + project delete cover the prefix;
  run retention sweeps finished matrix runs + cells + that prefix with the
  same per-project windows — `cleanup_tasks.purge_old_matrix_runs`, called
  from `purge_old_runs`; pending/running runs and the matrix definition are
  never swept). **Tenant isolation:** create two
  personas logged in to different tenants, add request rows with `swap`
  (`owner_persona_id` = tenant A, owner URL lists A's resources, extractor
  pulls an id, row URL contains `{id}`), expect allow for A and deny for B and
  anonymous. Security steps `expect-header`, `expect-cookie-flags`,
  `expect-blocked-after` (needs `params.authorized` + `security_allowlist`,
  injected at dispatch only when security testing is enabled),
  `expect-session-dead` (logs out itself so it can replay the old cookies) live
  in `core/security-steps.ts` — worker image rebuild needed.
  **UI (WP4b)**: `/access-matrix` (`pages/AccessMatrix.tsx` is a thin shell;
  components + pure helpers in `components/access-matrix/`, logic in
  `grid.ts`/`model.ts` with vitest). Selection lives in the URL
  (`?matrix=&tab=runs&run=&cell=`) so evidence is deep-linkable. The
  prerequisites panel *previews* the backend safety gates (`grid.ts:
  prerequisites` mirrors `host_allowed`/`safety_problems`); the backend stays
  the authority and its refusal `detail` is shown verbatim. SecurityFinding has
  no matrix column, so `GET /api/access-matrices/{id}/findings` recomputes the
  link: fingerprint(matrix,row,persona) + the latest run whose DENY cell was
  granted.
- **Release readiness (2026-10)** — `GET /api/projects/{id}/release-readiness
  ?git_commit=&git_branch=` (viewer; project-scoped API keys honoured — now
  also on `GET .../quality-gate`) returns `verdict` ready | not_ready |
  insufficient_data + reasons, the gate, runs, critical cases, active
  quarantines (owner/expiry), linked requirements, recent commits. Logic in
  `app/services/release_readiness.py`. Three opt-in policy fields (no
  migration — the policy is JSON; absent = off): `max_flaky_cases` (active
  quarantine OR score ≥ 0.4), `require_critical_passed`, `max_evidence_age_days`.
  Rules: priority `"critical"` matched trimmed/case-insensitive; results
  matched by `TestCaseResult.test_case_id` only (never name) and, with a
  commit, over EVERY finished run on it; newest result per browser/device
  must pass (green rerun replaces red; chromium can't hide firefox).
  `require_critical_passed` with no critical case fails closed. Evidence age
  covers critical cases, or all cases when none is critical. Checks carry
  `missing_data`; a gate failing ONLY on missing data reads "insufficient
  data", never pass. No commit given → the newest finished run (on the
  branch) picks the commit. Policy PUT is now audited. UI `/release-readiness`;
  MCP `get_release_readiness`; the Action's PR comment shows
  check `detail` (dist rebuilt).
- **Per-step evidence + failure investigation v2 (2026-10, WP3)** — workers
  send `step_evidence` per result (`core/evidence.ts:StepEvidenceRecorder`),
  stored in `result_payload.step_evidence` and rebuilt field by field on
  ingestion (`result_aggregator.normalise_step_evidence`: ids re-derived from
  position, screenshot keys kept only under `runs/{this run}/screenshots/`).
  Ids are the citation contract: `s3`, `s3.c0` (console), `s3.r1` (failed
  request; URL keeps query NAMES only), `s3.dom`, `s3.shot`, `error`,
  `run:<id>`; old results get `console.<n>`/`net.<n>`. Gated by capture level:
  `none` = step outcome only, `minimal` + console/requests/screenshot,
  `standard`+ a ≤4 kB DOM excerpt around the FAILING step's target (input
  values, scripts and `mask_selectors` removed in-page, text redacted). Both
  redaction twins scrub the field. `FailureReport` is `schema_version: 2`
  (`app/services/failure_investigation.py`): `observed` facts vs ranked
  `hypotheses` with `evidence_refs`, `history` (same case + same row name +
  same project, prior runs only; signatures recomputed, not read from
  `cluster_id`, which races) and `changed_since_last_pass`. v1 fields mirror
  the top hypothesis. LLM hypotheses citing no existing id are dropped
  (`dropped_citations`). `fail_on_console_errors` + `console_error_ignore_patterns`
  (suite chain; `testcase.fail_on_console_errors` overrides, migration
  `c3a2b3c4d5e3`) fail the step on `pageerror`/`console.error`, checked
  OUTSIDE the step's try so runtime heal never "heals" it. Suite-chain merge now
  also carries `har_capture`/`auto_retry`/`max_retries`/`retry_backoff_ms`,
  which it used to drop. **Also fixed:** workers read `data_policy` from the
  job's top level but the dispatcher puts it in `settings` — every job ran at
  the default `standard` level with built-in redaction only; `jobDataPolicy()`
  reads both. `trace_parser.parse_actions` exists but is not wired in.
  Worker + backend + frontend rebuild needed.
- **Test inbox + webhook capture (2026-10)** — steps `wait-for-email` and
  `wait-for-webhook`; templates `{{inbox.address[.label]}}` /
  `{{webhook.url[.label]}}`. Values are **derived, never stored**:
  `app/services/step_hooks.py` HMACs (run, case, data-row, label) under a
  SECRET_KEY subkey; `_case_payload(..., run_id=)` scans the case and ships
  them as `test_case.test_hooks` (worker interpolates via `interpolate.ts`).
  Inbox providers (`app/services/inbox_providers.py`): `mailpit` (compose
  profile `inbox`) and `imap` (IMAPS only, plus-addressing); instance settings
  group `test_inbox`, validated on save, "Test inbox connection" button. The
  **worker holds no mail credentials**: it POSTs (run, case, row, label) to
  `/api/internal/test-inbox/search` (X-Worker-Secret); the backend derives the
  address itself, requires a live run whose project owns the case, and matches
  + extracts on the raw message — only the extracted value and a
  `redact_message_text` preview (new twin rule: link query/fragment, opaque
  path tokens, 6-8 digit codes) go back. Webhooks: public
  `/api/hooks/capture/{token}[/sub]`; the token `<run>.<case>.<row+1>.<label>.<mac>`
  is self-verifying (no registration table), the run must be live (410 after),
  120/min, 256 kB, 50 per token, redacted BEFORE storage in `webhookcapture`
  (migration `c2a2b3c4d5e2`, purge + ON_DELETE entries), swept hourly after
  24 h (`purge_webhook_captures`). Public base = `TEST_HOOKS_PUBLIC_URL`, else
  `FRONTEND_BASE_URL`. Both waits take the NEWEST match (retries reuse the
  address). Not supported by local or mobile workers. Module names avoid
  `test_*` on purpose (pytest collected a `test_`-named endpoint). Worker image
  rebuild needed.
- **Inbound integrations (WP6, 2026-10)** — migration `c6a2b3c4d5e6`: tables
  `requirement`, `incident`, `incidentwebhook`. **Requirements**
  (`app/api/requirements.py`): import from the workspace's existing
  `IssueTrackerConfig` rows (Jira, plus new import-only `linear`; ticket
  creation refuses Linear) via `app/services/tracker_import.py` — Jira v3
  `/search/jql` with `/search` fallback (ADF flattened), Linear GraphQL with
  the filter as a *variable*; base URLs through net_guard, redirects not
  followed; issue keys regex-checked before going into JQL. Mock-verified only
  (`httpx.MockTransport` via `tracker_import._TRANSPORT`). The config's
  workspace must equal the project's (404 otherwise — the cross-tenant hole).
  `app/services/acceptance_criteria.py` parses Gherkin / AC headings / lists /
  checklists / `AC1:` and flags ambiguity (vague words; no observable outcome;
  perf words forgiven with a measurable bound). `POST /requirements/{id}/generate`
  makes one CREATE `CaseProposal` per criterion (never direct; ambiguous ones
  reported unless `include_ambiguous`); generation is the shared
  `case_generation.generate_steps`. Null provider / quota / daily cap → a
  *skeleton* proposal (empty selectors, `needs_authoring`, confidence 0 so it
  can never auto-apply). Proposal payload carries `requirement_id` /
  `incident_id`; `_apply_proposal` → `generation_proposals.link_accepted_case`
  creates the `RequirementLink` / sets `regression_case_id` **only if the
  origin is in the proposal's project** (payloads are caller-writable).
  Coverage joins `RequirementLink.ref == external_key` (manual links count):
  `covered|failing|untested|not_covered`. Old `GET /projects/{id}/requirements`
  is untouched; the new list is `/requirements/imported`. **Incidents**
  (`app/api/incidents.py`): `POST /projects/{id}/incidents/webhook` (no user
  auth) accepts `X-TraceIQ-Signature: sha256=`, `X-PagerDuty-Signature: v1=`, or
  — Opsgenie can't sign — `X-TraceIQ-Token`; 404 when no webhook exists so it
  can't enumerate projects. Secret: per project, Fernet-encrypted, shown once,
  create/rotate is project-admin and human-only. Same `(source, external_id)`
  updates the incident (PagerDuty lifecycle), otherwise a new one; recurrence =
  same `fingerprint` (service + title with numbers/ids/times stripped) →
  `previous_incident_id` + `recurrence_count`. Webhook regression proposals run
  in a BackgroundTask after the response. Escaped defects: `POST
  /incidents/{id}/escaped` names the missing case or `no_test_existed`;
  `escaped-report` counts "test added after" only when the regression case was
  created at/after the incident started. UI: `/requirements`, `/incidents`.
- **Security operations (WP7, 2026-10)** — migration `c7a2b3c4d5e7`.
  **SIEM**: instance-settings groups `siem` (`syslog` RFC 5424 tcp/tls/udp with
  octet-counted framing, `splunk_hec`, `elastic` bulk with `_id`=row hash so
  retries are idempotent, HMAC-signed `webhook`) and `security_alerts`.
  The audit `before_flush` hook adds a `SecurityEventOutbox` row in the SAME
  flush as each audit row (only when streaming or alerts are on), so an event
  exists iff its audit row commits; beat `drain_security_events` (10s,
  `FOR UPDATE SKIP LOCKED`) evaluates alerts and delivers in batches with
  exponential backoff; `SIEM_OUTBOX_MAX` drops the OLDEST beyond the cap (the
  audit table stays the record). Events carry `chain.{prev_hash,row_hash,
  hashed_timestamp}` — the naive isoformat the hash was computed over, not the
  `Z` timestamp. Destination goes through net_guard at save AND before each
  delivery; no redirects. `POST /admin/siem/test`, `GET /admin/siem/status`.
  **Alerts** (`app/services/security_alerts.py`) fire the burst rule when the
  count reaches EXACTLY N (stateless — no "already alerted" table); channels
  are `SECURITY_ALERT_*`, separate from run notifications. New audit rows the
  rules depend on: every instance-setting change (`entity_type
  instance_setting`, secrets as `changed` only), instance-admin grant/revoke,
  `role_grant` (workspace invite-add, project access, federation group sync),
  `mfa_enabled`/`mfa_disabled`, and `login_failed` for bad MFA codes and LDAP.
  **Access reviews**: `app/services/access_review.py` + `app/api/access_reviews.py`
  (workspace admin / instance admin; API keys refused for every write). Items
  snapshot access at campaign start; revoke needs a note; sign-off needs every
  item decided and is audited; `apply-revocations` only after sign-off and only
  with `confirm: true` (workspace scope removes membership/teams/project grants,
  instance scope deactivates + revokes refresh tokens; a reviewer can't revoke
  themselves). CSV is formula-injection-safe; HTML export ships a CSP.
  **Findings workflow** (`app/services/finding_lifecycle.py`): statuses
  `open|in_progress|fixed_pending_verification|verified_fixed|accepted_risk|
  false_positive` (old acknowledged/resolved migrated; still accepted on
  input). Rows stay per scan; workflow lives on the newest row per (project,
  scan_type, fingerprint) and older rows get `superseded_by_id` (soft, no FK).
  `ingest()` carries owner + ORIGINAL due date + unexpired risk acceptance,
  re-opens fixed findings that reappear; `verify_fixes()` is the only path to
  `verified_fixed` — a LATER completed scan of the same target AND same
  scan_type (ZAP), or a later run of the same suite that covered the origin
  (passive) / re-ran the case (leak). Marking fixed queues a retest through the
  same gates as a manual scan (`_scan_gate`); header-auth scans are not
  retested (the token is gone). Accepted risk / due-date changes need project
  admin; hourly beat re-opens lapsed acceptances. SLA per workspace
  (`workspace.finding_sla_days`, `GET/PUT /workspaces/{id}/finding-sla`).
  Register `GET /projects/{id}/security-findings`; SARIF 2.1.0 at
  `…/security-findings/sarif` and `/security-scans/{id}/sarif` (waived findings
  become `suppressions`). Passive findings now get fingerprints; re-scanning a
  run (`_replace_run_findings`) inherits state and relinks predecessors.
  **Leak detection** (`app/services/leak_detection.py`, task `scan_run_leaks` at
  finalize, `LEAK_DETECTION_ENABLED`): response side only (bodies, headers,
  network events + URLs, console errors, step DOM, execution log) — never
  request data; `[REDACTED:<cloud kind>]` markers count, because the APP sent
  it; email/phone only if the project's `redact_patterns` opt in; masked
  samples only; fingerprints strip list indexes. New `cloud_keys` redaction
  pattern (AWS/GitHub/Slack/Stripe/Google/Azure/PEM) is ON by default in both
  twins and runs FIRST (before PAN) — needs a worker rebuild. Test corpora
  build tokens by concatenation (push protection); `test_leak_detection.py` is
  in `.gitleaks.toml`. UI: Security page register + SLA card + SARIF buttons;
  Settings → Instance (Admin) SIEM/alerts cards; `/access-reviews` page.
- **Run-scoped data + suite hooks + cleanup (WP5, 2026-10)** — migration
  `c5a2b3c4d5e5` (`testrun.data_seed`). Tokens (`core/run-scope.ts`, resolved in
  `interpolate.ts` via `templateContextFrom`, which the step switch now uses):
  `{{run.id|short|worker|case|row|started_at|attempt}}` (`row` 1-based, 0 = no
  dataset; `short` = HMAC of the run id, `suite_hooks.run_short_token`) and
  `{{unique.NAME}}` (sha256 of run+short+case+row+**attempt**+name: stable in an
  attempt, a retry gets new values so it can't collide with what attempt 1
  created; never seeded). `{{fake.*}}` is seeded (mulberry32 keyed by
  seed+case+row, NOT run — a replay must reproduce) when `data_seed` is set: POST
  /runs body wins over the suite chain's `data_seed` (an inherited execution
  flag). `run_test_suite` writes the effective seed onto the run and ships
  `settings.run` = run context. Hooks: suite settings `setup_steps` /
  `teardown_steps`, validated on save/proposal (`suite_hooks.validate_suite_settings`),
  NOT merged into effective settings — each case gets `test_case.suite_hooks`, its
  own chain (outermost first, cut after the first `inherit_settings=false` suite;
  `hook_chain_sync`, cached in `session.info`). Worker `core/suite-hooks.ts`
  `HookRunner` keeps a frame STACK: before each case pop frames it doesn't share
  (teardown) and push new ones (setup) — so single-case jobs wrap every case and a
  continuous sub-suite job sets each suite up once. Setup failure → case `error`,
  not run; teardown ALWAYS runs (also for the frame whose setup failed), is best
  effort, and only produces `result_payload.hook_warnings`. Case steps are raced
  against `MAX_JOB_DURATION_MS - HOOK_TEARDOWN_BUDGET_MS` when hooks exist —
  the job-level timeout otherwise abandons the job with no teardown. Cleanup:
  `params.cleanup` (true = DELETE Location / extracted URL, or `{method,url,...}`
  resolved right after the step) registers once something was created (a 2xx
  that then failed an assertion counts); case cleanups LIFO after the case,
  setup ones after their frame's teardown; 2xx/404/410 = ok. Reports
  (`hook_report` → `result_payload.hooks|cleanup`) show the URL TEMPLATE and drop
  query values — twin `redactHookReport`/`redact_hook_report`. **Fixed on the
  way:** accepting an UPDATE_SUITE_SETTINGS proposal ran `_merge_settings`,
  which drops every key it doesn't know (`_proposed_suite_settings` now keeps
  them). Local worker supports hooks; mobile worker gets run tokens only. UI:
  suite settings "Setup & teardown" card (reuses `StepComponent`), step editor
  "Clean up afterwards", run page seed + "Re-run with this seed" + per-result
  outcomes. MCP: `run_suite(data_seed=)`, `describe_step_types` → `templates`.
  Worker + backend + frontend rebuild needed.
- **Developer workflow (WP11, 2026-10)** — `app/api/developer.py`, migration
  `cba2b3c4d5ec` (`projectapispec`, one OpenAPI doc per project).
  `GET /api/runs/{run}/results/{result}/bundle` (viewer; project-scoped keys
  honoured; audited) zips case/settings/data row/run meta/step evidence/this
  run's artifacts + a standalone `repro.spec.ts` (`app/services/repro_bundle.py`).
  It **never reads ProjectSecret**: `{{secret.X}}` stays a template; literal
  credentials (sensitive header/param/body names, password-field fills,
  redaction-pattern hits) become `{{secret.REPRO_*}}`; the spec reads
  `TRACEIQ_SECRET_<KEY>` from env. Untranslatable steps are `//` TODOs with the
  step JSON; regex operators are never compiled into the spec. Artifact keys are
  only fetched under `runs/{run_id}/`. `GET /api/runs/{id}/failures` gives the
  failing step + truncated error + a presigned screenshot (`ttl_seconds`,
  default 1h, same-run keys only) — the GitHub Action renders it as a "Failing
  cases" section in its sticky comment (dist rebuilt with ncc).
  Git sync: `GET/POST /api/suites/{id}/sync` — export docs carry a content hash
  (`app/services/case_sync.py`, mirrored in the CLI's `sync.py`: keep the two
  identical) over name/steps/tags/priority/code_paths; import turns edits into
  CaseProposals (create/update/move/delete; same `maybe_auto_apply` policy as
  agents, deletions only ever proposed), reports a server-side change since pull
  as a conflict, dedupes identical pending proposals, `dry_run` writes nothing.
  UPDATE/DELETE payloads carry `sync_base_hash`, re-checked in `_apply_proposal`
  (409 if the case changed while the proposal waited).
  `expect_schema` on `http-request` (`{schema}` or `{openapi:{operationId|method+path},
  status?}`): openapi refs are resolved to an inline schema in
  `app/worker.py:_case_payload` from `ProjectApiSpec` (`app/services/api_specs.py`;
  unresolvable → `{error}` which FAILS the step); the worker validates strictly
  with ajv in `execution-engine/src/core/schema-check.ts` (needs a worker
  rebuild). Specs are stored by `PUT /api/projects/{id}/openapi-spec` and by
  `/cases/from-openapi` (`store_spec`, default true). `GET /api/version`
  (authenticated; `api_version` major is what the CLI checks).
  CLI: `integrations/cli/` (`traceiq-cli`, stdlib-only Python — zipfile/urllib,
  pipx-installable): `login` (0600 config), `run --wait` (exit from the gate),
  `status`, `replay`, `pull`/`push`, `version`; exit codes in `main.py`.
  **Security review (2026-10-15):** `repro.spec.ts` takes case data only via
  `_js` (json.dumps, `<>&` escaped) and comments only via `_comment` (printable
  ASCII, `*/` broken) — tests assert the code is identical once string literals
  are blanked. CLI paths go through `sync.safe_path` (component rules + resolved
  commonpath) and ids must be ints; a tampered `.traceiq-sync.json` cannot
  delete or push files outside the root, symlinked case files are skipped.
  `from-openapi` audits spec storage (`openapi_spec_set`, `via`) and case
  creation; failure links ≤ 1 h; sync docs ≤ 1 MB / 1000 steps, request ≤ 25 MB;
  `GET /api/artifacts/{key}` honours project-scoped API keys and refuses `..`.
- **Estate + script inventory (WP9, 2026-10) — PASSIVE ONLY** — migration
  `c9a2b3c4d5ea`. Active probing (port exposure, WAF detection strings, origin
  bypass, TLS protocol/cipher enumeration) was designed and deliberately NOT
  built: it is parked for an owner decision in `SCOPE_NOTES.md` ("Estate:
  active probing"). Do not add it back in any form — no connect probes, no
  attack strings, no `verify=False`/`ignoreHTTPSErrors` anywhere in this path.
  **Estates** (`Estate`/`EstateDomain`/`EstateScan`/`EstateDomainResult`,
  project-owned): `app/services/estate_checks.py` is pure logic over an
  injected `Net` (DnsClient, fetch, cert, ct); `estate_net.py` is the real one —
  dnspython (new dep, imported lazily; a stale image reports "rebuild" instead
  of crashing), crt.sh + page GETs through net_guard with verification ON and
  no redirects followed, certificate read from an ordinary verified handshake
  (a failing cert is the `cert_untrusted` finding, never a reason to retry
  unverified). Score = 100 − `RUBRIC` deductions, stored itemised so the
  drill-down shows the arithmetic. Every host must be on the project's
  security allowlist (security enabled) at import AND at scan time
  (`access_matrix.host_allowed` / `security_allowlist_for`); CT-discovered
  names are added only if allowlisted (cap 200/apex, 500/estate). A check
  family only "ran" with real answers — DNS errors or unreachable HTTPS never
  resolve that family's findings. Findings: one row per fingerprint refreshed
  in place (`estate_findings.apply_findings`, WP7 vocabulary via
  `carry_forward`; a clean rescan → `verified_fixed`). Cert alerts once per
  threshold crossed (30/14/7, reset on renewal) via project Slack/Teams (net_
  guarded) + estate emails. **Monitored pages** (PCI DSS 4.0 6.4.3/11.6.1):
  `job_type=page_check` side job (`execution-engine/src/page-check.ts`, result
  key pinned to `page_check:result:{page_id}`, allowlist re-checked in the
  worker): a normal page load records document.scripts + network-only scripts
  (self-removing injectors), SHA-384 of each body as received, inline scripts
  as hashes only, security headers (never cookies), text/elements only when
  content tracking is on — redacted in the worker AND at ingestion.
  `collect_page_check` polls (15 min deadline → error). A changed script loses
  its approval; approval needs a ≥10-char justification and is audited
  (`page_script`/`approve`). Keyword/element checks are plain substrings (no
  regex on outsider content). Beat `dispatch-due-{estate-scans,page-checks}`
  every 5 min honour each row's cron (estate daily, page weekly; at most
  hourly). API `app/api/estates.py`; UI `/estate`, `/script-inventory`.
  Needs backend + worker image rebuilds.
- **Fleet & infrastructure (WP8, 2026-10)** — migration `c8a2b3c4d5e8`;
  operator guide in `docs/OPERATIONS.md`, CI snippets in `docs/CI_RECIPES.md`.
  **Heartbeats**: web/mobile workers write `workers:hb:<consumer name>` (EX 45,
  every 15s, `execution-engine/src/core/heartbeat.ts`) + ZSET `workers:index`
  straight to Redis; local workers `POST /api/jobs/heartbeat` (API key,
  namespaced `local:<ws>:<id>`). `GET /api/admin/workers` (instance admin,
  `app/services/worker_fleet.py`) joins them with stream length/lag/XPENDING
  per consumer, DLQ depth, and **version skew** (catalog step types a live
  worker's `SUPPORTED_STEP_TYPES` lacks; `load-test` is executor-only; a worker
  reporting no list is "unknown", not skewed). Gauges `traceiq_workers_alive`,
  `traceiq_queue_lag`, `traceiq_workers_version_skew`. **Worker config**:
  instance-settings group `worker`, served by `GET /api/internal/worker-config`
  and polled every 60s by `core/worker-config.ts`, which writes into
  `process.env` for the four managed vars; `null` restores the worker's
  *original* env, backend unreachable keeps last-known-good. LLM:
  `WORKER_LLM_PROVIDER` blank = worker env, `inherit` = the backend's default
  provider (registry default first); `llm-provider.ts`'s exported `provider` is
  now a delegating object (`reconfigureProvider`). **Regions**: `TestRun.region`
  (body `region`, validated `[a-z0-9-]`, not combinable with
  `local_worker_id`; **a region is a grant**: instance setting `WORKER_REGIONS`,
  one per line, `eu` = every workspace, `acme=12,15` = only those — undeclared
  regions 403, checked at run/schedule create AND again at dispatch before any
  secret is resolved, because region workers may sit in one customer's network) → `jobs:pending:<region>` / `jobs:mobile:pending:<region>`;
  jobs carry `region` so DLQ replay returns them there (replay also now sends
  mobile jobs back to the mobile stream — it used to send everything to
  `jobs:pending`). Workers with `WORKER_REGION` read their regional stream
  first, then the global one, **one stream per XREADGROUP** (a multi-stream
  read with COUNT 1 returns one entry *per stream* and strands the second in
  the PEL); `WORKER_REGION_EXCLUSIVE=true` drops the global stream (the DEFAULT for the
  compose `execution-worker-region` service and Helm `regionWorkers` — a
  dedicated pool that drained the global stream would run every tenant's jobs). No region
  = the old behaviour exactly. Schedules take `regions` (one run per
  region × device); monitors keep per-region state in
  `TestSchedule.region_states` with the pure `monitor_tasks.transition()`
  shared by both paths; aggregate = down if any monitored region is down.
  **Paging** (`app/services/paging.py`, `app/api/paging.py`): project
  `PagingChannel` (PagerDuty Events v2 / Opsgenie, key Fernet-encrypted,
  write-only) + `MaintenanceWindow` (one-off UTC or weekly in its own tz,
  overnight wraps to the start day) + `PagingEvent` log (soft refs). Monitors
  page on their DOWN/RECOVERY transition (resolve regardless of
  `alert_on_recovery`); `on_run_failure` pages only SCHEDULE/CI/WEBHOOK runs,
  never human/agent/local runs (`paging_tasks.page_for_run`, queued at
  finalize). A window suppresses but records; a resolve is only sent for a key
  whose last *sent* action was a trigger. Vendor HTTP is mocked at
  `paging._post` — not yet tried against a real PagerDuty/Opsgenie account.
  **IP allowlists**: `ApiKey.allowed_cidrs` (checked in
  `_principal_from_api_key` *before* `last_used_at`; refusal → 403 + audit
  `ip_refused`), `SCIM_ALLOWED_CIDRS` (only a *valid* token from outside is
  audited — scanner noise would flood the append-only log). Client IP is
  `app/core/client_ip.py`: XFF believed only from `TRUSTED_PROXY_CIDRS`
  (default none), walked right-to-left; an unparseable hop fails every match.
  **mTLS** (`app/core/worker_auth.py`): `REQUIRE_WORKER_MTLS` makes a pure-ASGI
  middleware demand `X-Client-Verify: SUCCESS` (+ optional subject list) on
  `/api/internal/*`, believed only from a trusted proxy; the worker secret is
  still required. Engine side `core/backend-http.ts` (`WORKER_TLS_*`) is now
  the only path to internal endpoints (test-hooks, baselines, llm-usage).
  **Egress**: `Project.network_policy` + `EGRESS_ALLOWLIST_ENFORCED` /
  `EGRESS_ALLOWED_HOSTS`, resolved at dispatch (`app/services/network_policy.py`;
  base_url host implicit; `allow_private` capped by
  `ALLOW_PRIVATE_NETWORK_TARGETS`; a corrupt policy fails closed) →
  `settings.egress_policy`; `core/egress-guard.ts` registers its route *after*
  the header-injection route, `fallback()` when allowed, abort + a
  `blocked: true` network event otherwise; metadata ranges never allowed;
  WebSockets/service workers are not intercepted. **Load tests**: `load-test`
  `params.prometheus_queries` evaluated on read by
  `GET /api/runs/{id}/results/{rid}/server-metrics` against `PROMETHEUS_URL`
  (net_guard with its own host as the private exemption). Prometheus has no
  tenancy, so steps may only name operator templates
  (`PROMETHEUS_QUERY_TEMPLATES`, `name=PromQL` per line, `$project_id`/
  `$workspace_id`/`$run_id` substituted as integers); free-form PromQL needs
  `PROMETHEUS_ALLOW_ADHOC_QUERIES`. Paging `_post` pins the vendor hosts, runs
  net_guard (`validate_outbound_url_sync`) and does not follow redirects.
  Egress guard canonicalises hosts with the WHATWG parser and classifies IPv6
  by its 128 bits (v4-mapped/compatible, NAT64, 6to4 → the embedded IPv4;
  `[::ffff:169.254.169.254]` serialises as `::ffff:a9fe:a9fe`), and a DNS
  failure now fails closed; net_guard unwraps the same embedded forms. **Deploy**: optional
  `execution-worker-region` (compose profile `region`, all five files),
  Helm `regionWorkers: []` + values-gated KEDA ScaledObjects on stream lag
  (HPA suppressed when on); `scripts/restore_drill.sh` restores a pg_dump (+
  artifact snapshot) into a throwaway DB and verifies migrations, row counts and
  the audit chain (DB URL passed to `docker run` via env, never argv; report
  lines redacted; `umask 077`).

- **Frontend quality (WP10, 2026-10)** — migration `caa2b3c4d5eb`
  (`testrun.emulation`/`sweep_group`, `sitecrawl`, `sitecrawlrun`,
  `sitecrawlissue`, `pagebudgetsample`). UI: `/frontend-quality` (crawler,
  page budgets, selector health, Storybook), run page "Sweep" tab, suite
  settings card + "Screen sizes & locales" run picker.
  **Matrix** (`app/services/frontend_matrix.py`): `POST /api/runs` body (or the
  inherited suite settings of the same names) `viewports` (presets
  mobile/tablet/desktop/wide or `{name,width,height}`), `locales` (tag or
  `{locale, query, cookie, url_template}`), `color_schemes`, `reduced_motion`
  fan out as a cartesian product INSIDE `_resolve_matrix` (each device slot
  became a `(device, variant)` pair, so the four creation sites kept their
  loops); `MAX_FANOUT_RUNS`=120 per case counts browsers×devices×variants×
  identities. Scheduled suite runs fan out too; monitors never do (one check
  per tick). `url_template` must start `{origin}`/`{url}` and the worker drops
  a rewrite that changes origin. Each variant's visual baseline lives under
  device `vp:<…>` and `/internal/visual-baselines/resolve` never falls back
  across `vp:` keys (a 375px shot vs the desktop baseline fails for the wrong
  reason). A multi-variant sweep screenshots after every step
  (`sweep-<case>-s<i>.png` attached to step evidence); `GET /runs/{id}/sweep`
  filters the group to the run's own project.
  **Crawler** (`app/services/site_crawl.py`, `app/tasks/frontend_quality_tasks.py`,
  worker `core/frontend-quality/crawler.ts`): `job_type=site_crawl` side job,
  report parked at `site_crawl:result:{run}` (worker derives the key from the
  run id), `collect_site_crawl` polls with a countdown, 45-min deadline.
  Start/extra hosts must be the project's OWN hosts (environment base URLs ∪
  suite allowed_domains ∪ security allowlist) — re-checked at dispatch; other
  hosts are only status-checked (body never read), never crawled.
  robots.txt matched by a hand-written matcher (no RegExp on site content),
  Crawl-delay is a floor on per-host spacing. Beat `process-due-site-crawls`
  (60s) skips a crawl whose previous run is still going.
  **Budgets**: step `check-page-budget` (no Lighthouse dep — ESM-only, own
  Chrome launcher; measured with Playwright: vitals, INP≈longest event-timing
  entry, Resource-Timing weights, SEO facts) pushes outcomes onto the case
  context; the worker ships them as `page_budgets`, the aggregator stores
  PageBudgetSample rows replace-per-(run,test) so redelivery doesn't double
  the trend. Unmeasured metrics never fail a budget; SEO is report-only
  unless `seo: fail`. Steps `expect-no-horizontal-overflow` /
  `expect-rtl-layout` (clipped-by-overflow-ancestor elements don't count; RTL
  set only through CSS fails — assistive tech follows `dir`).
  **Selector health** (`GET /projects/{id}/selector-health`) aggregates heal
  proposals + failed/healed step evidence; suggestions parse the
  `data-traceiq-target` DOM excerpt (test id > role+name > unique id) —
  display only. **Storybook**: `POST /projects/{id}/storybook/{stories,cases}`
  reads index.json/stories.json through net_guard from a project host; the
  visual step id is `story-<story id>`, so the component baseline is keyed by
  story; regenerating updates the case in place. Redaction twins gained
  `redactPageBudgets`/`redactCrawlReport`. Worker image rebuild needed.
  **Security review (2026-10-15):** crawl targets come from page content, so
  every link check, redirect hop and page subresource passes
  `egress-guard.ts:targetVerdict` (literal IPs in every encoding, names
  resolved, `*.localhost`); link-local/metadata never, private only on the
  crawl's own hosts or with ALLOW_PRIVATE_NETWORK_TARGETS (`config.allow_private`,
  set by `build_job`). Refused targets are `not_checked` issues. Playwright
  routes only the FIRST request of a redirect chain, so the loader fetches with
  `maxRedirects: 0`: subresource chains are walked hop by hop, a page redirect is
  reported (`redirectTo`) and the target queued as its own page. Production link
  checks use `guardedFetch` (connect-time lookup check = no DNS rebinding).
  Ceilings regardless of the job: 1000 pages, depth 10, 15 min (+ a hard race
  in worker.ts), 20k checks, robots 512 KB, 300 sweep screenshots/job; server
  side: 20 crawls/project, at most hourly, limits re-clamped in `snapshot()`,
  sweep view 200 runs, Storybook index streamed with `safe_get(max_bytes=)`.
- **Reporting: digest, share links, executive summary, portfolio (WP12a,
  2026-10)** — migration `ce12b3c4d5f1` (`sharelink`;
  `reportschedule.recipient_user_ids`/`last_result`). One builder,
  `app/services/reporting.py` (`project_snapshot`, `workspace_snapshot`,
  `portfolio_rows`): pass rate = passed/finished runs (None, not 0, with no
  runs), "failures" = failing results, trend = this window minus the previous
  one of equal length, readiness verdict for the newest commit (None if it
  can't be computed). **Digest** (`app/tasks/report_tasks.py`, beat
  `send-scheduled-reports`): email to picked project members re-checked with
  `has_project_access_sync` at send (project pref "off" skips them) + extra
  addresses validated like project notifications — legacy `recipients` rows
  are honoured as extras and re-validated at send; one message per recipient;
  chat ONLY via the project's own webhook (`pn.webhook_url` + `post_webhook`),
  never the instance SLACK/TEAMS_WEBHOOK_URL; SMTP via `instance_settings`;
  send-now/edit need editor. The task builds the snapshot with `asyncio.run`
  on a private NullPool engine (the app engine is loop-bound).
  `GET /projects/{id}/digest-preview`. **Exec summary**
  `GET /projects/{id}/executive-summary`, `GET /workspaces/{id}/executive-summary`
  and **portfolio** `GET /workspaces/{id}/portfolio` cover only projects the
  caller can access (`reports.accessible_projects`, honours project-scoped
  keys). **Share links** (`app/api/share_links.py`, `app/services/share_links.py`):
  kinds `run_report|project_summary` (project editor) and `workspace_summary`
  (member; covers only projects where the CREATOR is an editor); token shown
  once, stored as SHA-256; expiry default 7 / max 90 days; revoke =
  `DELETE /share-links/{id}`. Public `GET /api/share/{token}` (30/min, every
  view audited without the token) recomputes the view at every request from
  what the creator can still see — creator deactivated/erased (erasure revokes
  their links) or lost access → 404. Public payloads are WHITELISTS
  (`public_run_report`, `public_summary`): names, statuses, counts, durations,
  pass rates, dates; never artifact URLs, error text, cluster titles,
  readiness reasons, URLs/selectors/headers, git details or emails — add a
  field there only on purpose. UI: Analytics digest card, `/reports/summary`
  (print CSS), `/reports/portfolio`, run page "Share", public `/share/:token`.

- **Delivery + reliability reports (WP12b, 2026-10)** — migration
  `ce22b3c4d5f2`: tables `deployment`, `flakedailysnapshot`,
  `flakecasescoredaily`, `vendorhealthdaily`; `testschedule.slo_*` columns;
  indexes `ix_testrun_project_created`, `ix_testcaseresult_test_run_id`
  (CONCURRENTLY, IF NOT EXISTS — legacy DBs may have the latter).
  **DORA** (`app/api/dora.py`, maths in `app/services/dora.py`): the only
  source of "a deployment happened" is `POST /api/projects/{id}/deployments`
  (editor, or an API key that `api_key_allows_project`; body `git_commit`,
  `environment`|`environment_id`, `version`, `status` success|failed,
  `deployed_at`, `commit_at`, `source`, `external_id` — same external id =
  update, so CI retries don't double-count). `GET /projects/{id}/dora?days=&environment_id=`.
  Lead time = deployed_at − commit_at, else − first TestRun with that
  git_commit (exact match). Change failure = deployment status failed, an
  incident within 24 h (attributed to ONE deployment: the one whose commit/
  version it names, else the latest preceding; incidents have no environment,
  so project-wide), or a failed run on the same environment (NULL = NULL)
  within 24 h. MTTR = median resolved_at − started_at. Bands use the 2023
  State of DevOps thresholds; every metric returns `missing` text when it has
  no inputs. curl/GitHub Actions snippet:
  ```bash
  curl -fsS -X POST "$TRACEIQ_URL/api/projects/$PROJECT_ID/deployments" \
    -H "X-API-Key: $TRACEIQ_API_KEY" -H "Content-Type: application/json" \
    -d '{"git_commit":"'"$GITHUB_SHA"'","environment":"production","version":"v1.4.2",
         "status":"success","source":"github-actions","external_id":"'"$GITHUB_RUN_ID"'"}'
  ```
  (full workflow step in `integrations/github-action/README.md`).
  **SLOs** (`app/api/reliability_reports.py`, `app/services/slo.py`):
  `GET|PUT /schedules/{id}/slo` (PUT editor, audited), `GET /projects/{id}/slo`;
  window 7/30/90 (default 30, rolling); NULL target = 99.9 and
  `target_is_default`. Budget remaining can go negative (overspent); burn rate
  for the window and the last 24 h (status `at_risk` when 24 h burn > 1);
  latency p95 from `MonitorCheck.run_id → TestRun.duration_ms` (nearest rank),
  per region only when >1 region.
  **Flake trends** — beat `snapshot-flake-trends` (6 h,
  `reliability_tasks.snapshot_flake_trends`) replaces today's rows (idempotent;
  refreshes yesterday's result counts); 180-day retention.
  `GET /analytics/projects/{id}/flake-trend` and `/flake-patterns` (browser,
  UTC hour, environment, failing step type — the step type is extracted in SQL
  from `result_payload.step_evidence` so the JSON never leaves Postgres).
  **Dependency health** — beat `aggregate-vendor-health` (hourly) rebuilds
  today + yesterday per project from `TestRun.network_events` (responses only;
  "errors" = status ≥ 400 or none); `aggregate_vendor_health(days_back=N)`
  backfills. Vendor = `script_inventory.site_of` of hosts outside the run's own
  sites (first document per case, target_url, environment base_url). Only
  HOSTS are stored — never paths or queries. Latency is a bucketed histogram
  (mergeable; percentiles are bucket upper bounds). Correlation = share of
  failed vs passed results whose run saw an error from that vendor.
  `GET /projects/{id}/dependency-health?days=`. All reads: project viewer.
- **Journeys, flags, benchmarks (WP12c, 2026-10)** — migration `ce32b3c4d5f3`
  (`journeyimport`, `journeyconfig`, `businessjourney`, `flagcatalogentry`,
  `benchmarkgroup`, `testrun.flags`, `testrun.benchmark_group_id`). API in
  `app/api/journey_reports.py` + `app/api/benchmarks.py`; logic in
  `app/services/{journey_coverage,business_journeys,flag_coverage,benchmarks}.py`.
  **Journey coverage**: `POST /projects/{id}/journey-imports` (multipart CSV or
  JSON, generic columns path|url + views [+ journey/funnel, step, date]; 5 MB /
  50k rows, read bounded, newest 20 kept). Rows are stored normalised (no
  host/query/fragment/trailing slash) but NOT rule-mapped, so editing
  `PUT /projects/{id}/journey-rules` re-applies without re-import. Rules:
  `pattern` (`{id}`/`*` one segment, `**` any, `*` in a segment — hand-written
  matchers) or `regex` (RE2 only). Covered = canonical keys of the project's
  cases' top-level goto values (same source as `/apps/{id}/surface`
  routes_covered); `GET /journey-coverage` = traffic-weighted %, top uncovered,
  tested-but-low-traffic (<0.5% or absent), per-journey step coverage. When a
  file has page rows, journey rows don't add to the total (funnel steps repeat
  page views). **Business journeys**: `case_ids` is an ordered JSON list
  (validated in-project on write, re-filtered on read — no FK, so deleted cases
  drop out as `missing_case_ids`); health per case from the last 30 days of
  results under THIS project's runs (failing > flaky [quarantined / is_flaky /
  mixed recent] > passing > not_run); requirements = declared refs plus the
  cases' RequirementLinks; traffic from the latest import's `traffic_journey`,
  else the cases' pages. Owner must have project access. **Flags**: `POST /runs`
  body `flags {name: str|bool}` (max 50, name `[A-Za-z0-9_.-]{1,64}`) →
  `TestRun.flags`, job `settings.flags`, `{{flag.NAME}}` in
  `core/interpolate.ts` (web + mobile; older workers ignore it).
  `GET /projects/{id}/flag-coverage?days=` vs the catalogue (`/flag-catalog`):
  runs/cases/last seen per value, never-tested values, uncatalogued flags;
  values compared as strings ("true"/"false").
  **Benchmarks**: `POST /projects/{id}/benchmark-groups {name, test_suite_id,
  targets[2..10], browser}` → one suite-level run per target with
  `target_url` = base URL. `target_url` (also comparison runs, where it was
  previously stored but ignored) now overrides the environment `base_url` at
  dispatch, so only RELATIVE gotos follow the target (the report lists cases
  with absolute gotos). Targets pass net_guard and, when the project enforces
  an egress allowlist, must be on it — the base URL is otherwise allowed
  implicitly, so this check is what stops a retarget bypassing the policy;
  comparison runs now get the same check. The matrix
  (`GET /benchmark-groups/{id}`) only reads runs of the group's own project.
  Worker image rebuild needed (flag interpolation).

- **Controls, evidence packs, restore drills (WP13a, 2026-10)** — migration
  `cf12b3c4d5f5` (`compliancecontrol`, `controlevidence`, `controlexception`,
  `evidencepack`, `drillresult`). API `app/api/compliance.py`; rules in
  `app/services/compliance.py`, pack in `app/services/evidence_pack.py`, task
  `app.tasks.compliance_tasks.build_evidence_pack` (main-queue, no beat).
  **Controls**: workspace-scoped; members read, workspace admin (or Tenant
  Admin of its tenant, via `workspace:manage_users`) writes; the control's
  owner (must be a member) may add evidence and request exceptions; API keys
  never write and must match the workspace + not be project-scoped to read.
  Status is computed on read (never stored): excepted > failing (any source
  fails or newest manual evidence is a fail) > not_assessed > stale (newest
  PASSING evidence older than `freshness_days`) > passing. Sources (`SOURCES`)
  are typed checks over existing data (access review signed off within N days,
  incl. instance-wide reviews; audit chain verifies — global walk in id
  batches, cached 5 min per chain head, never names another workspace's row;
  data policy on every project; no findings past SLA; restore drill passed
  within N days; MFA for admins; SSO only; API keys expire). Exceptions:
  approver ≠ requester (409), expiry ≤ 365 days, expired ones stop counting
  without a job. "Load SOC 2 starter set" is idempotent by
  `(workspace_id, seed_key)` unique key; nothing is auto-seeded. Every change
  (incl. evidence + exceptions) is audited as entity_type
  `compliance_control` — the control's history is that query. Evidence files:
  10 MB, extension allow-list, content type from our map, downloaded as an
  attachment with `sandbox` CSP. **Evidence packs**: always built in Celery
  (bounded memory: spooled temp file, audit rows in id batches) → object store
  under `compliance/ws<id>/` (the workspace purge deletes that prefix) →
  `POST /evidence-packs/{id}/download` returns a 5-minute presigned URL
  (audited). One build per workspace at a time; queued/building > 2 h shows as
  failed. `manifest.json` = SHA-256 + size per file, HMAC-SHA256 over its
  canonical JSON with `secrets.purpose_keys("traceiq/evidence-pack-manifest/v1")`
  (HKDF from SECRETS_KEY, fallback SECRET_KEY material — never the raw JWT
  key; `_PREVIOUS` keys still verify). `POST /evidence-packs/verify` (zip, or
  zip + separate manifest) reports mismatched/missing/unexpected files and
  signature validity, only to members of the workspace the manifest names.
  Every pack query filters by the pack's workspace (live test asserts no
  other tenant's audit rows, findings or project names). **Restore drills**:
  `scripts/restore_drill.sh --report-to URL` (key via `TRACEIQ_DRILL_API_KEY`,
  passed to curl on an fd) POSTs `/api/admin/restore-drills`: instance admin
  only — a JWT, or an API key whose creator is an instance admin now and that
  is not project-scoped (a workspace key alone could forge instance-wide
  evidence for every tenant). `DrillReport` is `extra=forbid` and REJECTS
  (422, input not echoed) anything that looks like a credential; dump name
  must be a basename. Workspace views get check names + verdicts only (details
  hold instance-wide row counts). UI: `/compliance/controls`,
  `/compliance/evidence-packs`, `/audit-log`.
- **Trust center + PoC report (WP13b, 2026-10, decisions C3/C4)** — migration
  `cf22b3c4d5f6` (`trustcenter`, `trustincidentpost`, `trustdocument`,
  `sharelink.params`). API `app/api/trust_center.py`, rules
  `app/services/trust_center.py`. **Trust center**: one per workspace, OFF by
  default, workspace admin only (project-scoped keys refused); public
  `GET /api/trust/{slug}` (60/min) + page `/trust/:slug`; the slug is an
  unguessable identifier (like status pages). Everything 404s while it is off.
  Status = the workspace's own picked status pages, re-filtered per view
  (enabled, still in the workspace), monitors reduced to name/state/uptime.
  Incident posts: only `published` are public; updates are a JSON list
  rendered by `render_markdown_safe` (escape FIRST, then p/br/ul/li/strong/em/
  code only — no links). Posture: registry `POSTURE_ITEMS` (opt-in per item;
  an item with no data is hidden, never shown as "no"); findings are a count
  BAND, never titles; audit item = the last `POST .../verify-audit` result
  (stored on the row; a full-chain walk per public view is too heavy); restore
  drill has no data source yet. Adding WP13a's controls summary = one fact in
  `collect_facts` + one `PostureItem`. Documents: multipart upload, 20 MB,
  type SNIFFED from bytes (PDF/PNG/JPEG/Office/TXT; HTML/SVG refused), key
  `trust/<ws>/<uuid>` (purge deletes the prefix). Downloads only via
  ShareLink kind `trust_document` (`target_id` = doc) issued per recipient:
  link `/trust/<slug>#<token>`, `POST /api/trust/{slug}/document {token}` →
  60 s presigned URL forced to `attachment` with the sniffed type; resolves
  only for the slug's workspace, a doc of that workspace, an issuer still a
  workspace admin. `/share/view` never resolves that kind; the member-visible
  workspace link list hides it. Another workspace's doc/post id → 404.
  Request-access form (opt-in, 5/hour): emails ONLY this workspace's admins via
  `send_account_email`; requester email is not written to the audit row. Page
  views are counted on the row, downloads audited. **PoC report**:
  `GET /api/workspaces/{id}/poc-report?start&end&project_ids&minutes_per_execution&hourly_rate&currency`
  (`app/services/poc_report.py`) over the caller's viewer projects (others
  silently dropped; `project_ids` says what was covered). Estimate = automated
  (non-manual) case executions × minutes (default 5) / 60, money only with a
  rate AND a typed 3-letter code — always labelled "Estimated". Shared via
  `POST /workspaces/{id}/share-links` kind `poc_report` with `params` frozen
  on the link (editor projects only); public payload is `public_payload`'s
  allow-list (counts and names; no errors, URLs, emails, cluster titles).
- **Sizing, demo workspace, test packs (WP13c, 2026-10)** — migration
  `cf32b3c4d5f7` (`workspace.is_demo`, `workspace.demo_owner_id`,
  `testcase.description`). **Sizing** (C6): `app/services/sizing.py` is pure
  arithmetic over the documented figures (1 CPU / 1.5 GB per worker, one job
  per worker, 70% target utilisation, capture-level kinds from
  `data_policy._LEVEL_ALLOWS`); `POST /sizing/estimate` (any signed-in user)
  returns results plus every formula row (`formula` + `substituted`) and the
  assumptions with their source — the page renders those rows, so a new
  number must be added through `_Sheet.add`. Workers = max(throughput at 70%,
  burst wave: `ceil(burst / (floor(wait/duration)+1))`); KEDA `lagCount` =
  `floor(wait/duration)`. Artifact sizes are DECLARED (nothing measures them)
  and overridable via `artifact_sizes_kb`. **Demo workspace** (C5):
  `GET/POST/DELETE /demo-workspace`, one per USER (users row locked for the
  check, 409 otherwise; slowapi 5/hour). `app/services/demo_workspace.py`:
  `build_demo_plan(seed, today)` is pure + deterministic; the writer inserts
  2 projects, envs, encrypted todolite secrets, personas, requirements, a
  PAUSED monitor (`is_active=False`, checks + `schedule:<id>` runs), 30 days of
  runs/results, a failure cluster, a passive finding, flake records + daily
  snapshots, deployments — no worker, no network; hosts only example.com /
  httpbin.org / localhost:8080. Delete = `purge_workspace`. Every delivery
  path refuses demo projects via `is_demo_project_sync` (run email/chat in
  `get_notification_settings`, monitor alerts incl. per-monitor emails,
  digests, `paging.page`, outbound webhooks) and the projects are also seeded
  with notifications off. **Test packs** (C7): JSON in `backend/app/packs/`
  (`format: traceiq-test-pack`, v1: id, version, variables{name, description,
  required, secret, default}, suites→cases{name, description, tags, steps});
  `validate_pack` (run over every pack in `tests/test_test_packs.py`) rejects
  unknown step types (vs `/step-types`), undeclared/unused variables,
  `{{env.X}}` for a secret (or the reverse), secret defaults, and templates in
  assertion values (the worker compares those verbatim). `GET /test-packs`;
  `POST /projects/{id}/test-packs/{pack}/import {variables, environment_id?}`
  (editor) creates "<title> pack v<version>" (409 on clash), non-secret values
  into the chosen/default environment (created "default" if none; a default
  never overwrites an existing value), secrets into `ProjectSecret`
  (encrypted; audit logs names only), reports `missing`; never contacts the
  target. Suite export is format 2: per-case description/tags/priority and a
  top-level `variables` block (pack declarations from `settings.test_pack`,
  else names derived from templates; never secret values); import reads the
  new case fields and ignores `variables`. wait-for-webhook cannot assert
  headers, so the webhook pack does not check signatures (says so).

- **In-app assistant "Ask TraceIQ" (WP14a, 2026-10, decision W1)** — migration
  `d012b3c4d5f8` (`assistantconversation`, `assistantmessage`). API
  `app/api/assistant.py` (`/api/assistant/status`, `/conversations` CRUD,
  `POST /conversations/{id}/messages {text, workspace_id, project_id?}`, 20/min);
  loop + citations `app/services/assistant.py`; tools
  `app/services/assistant_tools.py`. Uses only `provider.complete()`: a JSON
  protocol (`{"tool","args"}` or `{"answer","citations"}`), max 4 tool calls,
  one corrective retry, then prose → uncited answer / fixed apology. Tools are a
  fixed READ-ONLY allow-list (runs, results, flaky, clusters, readiness,
  findings, cases, project overview) — no writes, no dispatch, no artifact
  URLs/headers/bodies. Scope = `accessible_projects` recomputed per message
  (API key: own workspace + project only); a project/record id the model names
  outside it gets "not found or not accessible" (no oracle), and `run_tool`
  post-filters every item by `project_id` whatever the data layer returned.
  Everything to the LLM goes through `redact_for_llm` (shared patterns minus
  phone — it eats ISO dates — plus key=value secrets, bearer, `tiq_` keys,
  URL query/opaque path tokens), results capped at 6000 chars, fenced by a
  per-question random `<data-…>` tag declared untrusted. Refs `run:`/`result:`/
  `cluster:`/`finding:`/`case:`/`project:`/`readiness:` — only refs a tool
  returned in the conversation (and still in scope) survive; invented inline
  refs become `[unverified]`, an answer citing nothing is `uncited`. Stored:
  question redacted, answer, citations, tool calls (name/args/refs, never
  payloads), `project_ids` — reading an answer later needs access to ALL of
  them (lost access or a narrower key → hidden). Private per user (404 for
  anyone else, admins included). 30 days: beat `purge_assistant_conversations`
  (daily) + read path filters older rows. Gate `assistant_allowed(session,
  workspace_id)` = instance setting `ASSISTANT_ENABLED` (ai group, default on)
  + non-Null provider → friendly stored answers for not_configured / disabled
  / quota; the W2 opt-out policy plugs in there. Metered as feature
  `assistant`. Purge plan, erasure (`PERSONAL_TABLES`) and entity delete
  (`assistantconversation.project_id` NULLIFY) cover the tables. UI: "Ask
  TraceIQ" button in the DashboardLayout header opens a drawer.
- **Policy engine, AI opt-out, agent budgets (WP14b, 2026-10, decisions
  W2/W3/W4)** — migration `d022b3c4d5f9` (`policydecisionlog`, `agentcontrol`,
  `agentsession`, `workspace.ai_enabled` default true,
  `testrun.agent_session_id`, `llmusageevent.agent_id/agent_session_id`).
  `app/services/policy/`: `evaluate(action, ctx)` / `evaluate_sync` →
  `Decision(allowed, rule, reason, data, status_code, detail, effect)`; rules
  in `rules.py` WRAP the legacy functions (which stay the source of truth —
  `tests/test_policy_parity.py` compares them table by table, so changing a
  check means changing the wrapped function, not the rule). Actions:
  `run.dispatch` (stages `create` = quota + agent rules in the run/benchmark/
  access-matrix endpoints, `admit` = concurrency cap as a DEFER, `configure` =
  capture ceiling, egress, `ai_allowed` job flag — all in `worker.py`),
  `ai.use`, `agent.write`, `proposal.create|approve|auto_apply`,
  `security.scan` (`_scan_gate` → rule → `_scan_gate_reason`), `backend.fetch`
  (net_guard, documented only, `enforced=False`). Every DENY is written to
  `PolicyDecisionLog` in its own transaction (the caller is about to raise);
  defers and `log=False` probes are not; identical denies dedupe for 60 s per
  process; `purge_policy_decisions` (daily beat) keeps
  `POLICY_DENY_RETENTION_DAYS` (default 90). Legacy HTTP details are kept
  verbatim; new rules return dict details (`error`, `rule`, `message`, …).
  **AI opt-out** (W2): `get_provider()` returns `_PolicyGuarded(inner)` (Null
  passes through) whose `complete` evaluates `ai.use` from the ambient
  `llm_call_context` — so EVERY backend LLM call needs `workspace_id` in that
  context or it is not workspace-gated. Assistant/feature code asks
  `policy.ai_allowed(session, ws_id, principal=...)` (async) /
  `ai_allowed_sync(...)`. Jobs carry `settings.ai_allowed`; the worker's
  `core/ai-gate.ts` (`withAiGate` around each job, checked in
  `provider.complete` by `runId`) skips heal/analysis LLM calls; absent flag =
  allowed (old backends). The legacy CONTINUOUS engine (`server.ts`) does not
  honour it. **Agents** (W3): `get_current_principal` binds X-Agent-Id/
  X-Agent-Session-Id into the LLM context for the request and applies the
  kill switch (`agent.write`) to API-key writes (non-GET) using the key's
  workspace; JWT callers with agent headers are checked at runs, proposals
  (single, bulk, generated), case create/update/import and suite create
  (`agent_controls.enforce_agent_write`). Budgets are per SESSION:
  `AgentControl` for the agent id → the workspace `"*"` row → instance
  `AGENT_SESSION_MAX_{RUNS,LLM_TOKENS,PROPOSALS,MINUTES}`; None inherits, 0 =
  no limit; refused once the counter has reached the cap (429, detail
  `{error:"agent_budget_exceeded", limit, used, max, agent_id,
  agent_session_id, message}`); wall clock counts from the session's first
  budgeted action. Counters are an upsert on `agentsession` (separate
  transaction); tokens come from `record_llm_usage` and the worker ingest
  (`/internal/llm-usage`, attributed via the run). No session header = not
  session-budgeted (budgets stop cooperative agents; the hard stop is the kill
  switch / revoking the key). APIs: `/policy/rules`,
  `/workspaces/{id}/ai-policy` (GET member, PUT admin humans-only, audited),
  `/workspaces/{id}/policy-decisions` (ws admin), `/admin/policy-decisions`
  (instance admin), `/workspaces/{id}/agents[/sessions|/timeline]` and
  `/workspaces/{id}/agent-controls` (ws admin; PUT/DELETE humans-only,
  audited `agent_blocked|agent_unblocked|agent_controls_updated|removed`).
  Pages `/policies`, `/agents`.
- **Visual review: element baselines, stored diffs, grouping, comments
  (WP14e, 2026-10, decision W7)** — migration `d052b3c4d5fc`
  (`visualbaseline.element_key` NOT NULL default '' + `selector`; tables
  `visualcomparison`, `visualreviewcomment`). **Element baselines**:
  `expect-visual-match` with a `selector` screenshots only that element
  (`execution-engine/src/core/visual-step.ts`, locator.screenshot, same
  capture-time masks/tolerance/pixel masks). The slot is (case, step, browser,
  device, `element_key`); `element_key` = `el-` + 12 hex of sha256 of the
  normalised selector (whitespace collapsed / dropped around `>` `,` outside
  quotes; twins `visual-element-key.ts` and `services/visual_review.py` with
  shared vectors), computed from the selector AS AUTHORED (pre-template). ''
  = full page. Candidate file `visual-<step>-<key>.png` (full page unchanged:
  `visual-<step>.png`). `/internal/visual-baselines/resolve` filters on
  `element_key` (default '') so an old worker — which ignores `selector` and
  sends no key — never compares a full page with an element baseline.
  Promote takes `element_key` / `comparison_id`; with a comparison its
  REPORTED slot wins over the request, the source object must end with that
  slot's file name, and dest keys carry the element key. **Diff stats**:
  `visual-diff.ts` returns `regions` (cell-clustered boxes of pixelmatch-red
  pixels, max 20, each with a 64-bit dHash of candidate and baseline crop);
  the step pushes an entry onto `ctx.visual_results` (worker ships it like
  `page_budgets`; continuous jobs slice per case; candidates now go to the
  job artifacts dir, not the video dir). The aggregator stores one
  `VisualComparison` per entry (`persist_comparisons`: rebuilt field by field,
  file name must match the claimed key, candidate/diff keys only from this
  run's uploaded screenshots, selector redacted; replaced per (run, test),
  rows with comments kept). **Grouping** (`group_comparisons`, within one
  run): union-find over changed items; two are related when the dominant
  region of one matches a region of the other — sizes within 25%+8px and
  both crop hashes within Hamming 10 (flat crops / no hashes also need the
  same position ±24px). Group key `g<lowest comparison id>`. **API**
  (`app/api/visual_review.py`, viewer reads, editor writes, project-scoped
  keys enforced): `GET /projects/{id}/visual-review/runs`, `GET
  /projects/{id}/visual-review?run_id=` (items with presigned urls, current
  slot baseline, this run's accept/keep decision from the audit log, groups,
  comment counts), `POST .../groups/{key}/accept|keep {run_id}` — still ONE
  `visual_baseline.accept|keep` audit row per baseline (with `group_key`),
  `GET/POST .../comments` (item: `comparison_id`; group: `run_id`+`group_key`,
  anchor must be in that run/project), `POST .../comments/{id}/resolve`.
  Comment bodies are plain text (control chars stripped, ≤4000), rendered
  escaped; the audit row records where/who/length, not the text. Mobile
  `mobile-expect-visual-match` is unchanged (full screen, reports no
  comparisons). Cross-run grouping is not built.

- **Run lifecycle (WP15f, 2026-10-20)** — migrations `d062b3c4d5fd`
  (statuses, `testcaseresult.error_kind`, `testrun.cancelled_at`),
  `d072b3c4d5fe` (`webhookdelivery`), `d082b3c4d5ff` (`gatepolicyversion`),
  `d092b3c4d600` (webhook secrets encrypted).
  **Finalize (L0, bug fix):** a finished distributed run's side effects
  (notifications, outbound webhooks, monitor check, paging, passive + leak
  scans, triage, proactive heal) used to fire ONLY from
  `POST /runs/{id}/finalize`, which only the legacy engine's controller
  (`server.ts`, `run:<id>:complete` pub/sub) calls — and no shipped deployment
  runs it, so they never fired. `app/services/run_completion.py`: the
  aggregator claims `finalized_at` (conditional UPDATE) in the transaction that
  sets the terminal status and queues `SIDE_EFFECT_TASKS` after commit; stale
  timeout, dispatch error, force-complete and cancel do the same; the endpoint
  shares the claim. **Statuses (L2):** `TestStatus` gains `CANCELLED` /
  `TIMED_OUT` — terminal WITHOUT a verdict. `app/services/run_status.py`
  separates `TERMINAL` (is it over? waits, purges, "already final") from
  `VERDICT` (rates, gates, readiness, flake scores) — use the right one; most
  old `(PASSED, FAILED, ERROR)` lists mean VERDICT and were left alone.
  TIMED_OUT = the platform's limit (job wall clock `MAX_JOB_DURATION_MS`, the
  stale sweep, the hook deadline); a step timing out on the page is still
  FAILED. `error_kind` (`test|setup|infrastructure|timeout|cancelled|
  not_executed`) is sent by the worker and derived for old workers
  (`normalise_result` also turns an old worker's platform timeout into
  TIMED_OUT). The run outcome comes from the stored results (FAILING >
  TIMED_OUT > CANCELLED > PASSED), not the Redis counter alone. Monitors count
  TIMED_OUT as down and skip CANCELLED; paging pages timeouts, never cancels;
  notifications skip CANCELLED; outbound events `run.cancelled` /
  `run.timed_out` (a `run.completed` filter still receives both); flake
  history ignores infrastructure errors. Worker: a job past
  `MAX_JOB_DURATION_MS` used to stay unacked with a zombie still running (and
  ran again on redelivery) — it now reports `timed_out`, the zombie stops at
  its next step boundary (`core/run-control.ts`) and the loop waits for it
  (`TIMED_OUT_JOB_GRACE_MS`, default 60s) because its finally stops the SHARED
  browser manager. `cleanup_stuck_tests` is retired (it filtered on a
  `TestRun.updated_at` that never existed). **Cancel (L1):**
  `POST /api/runs/{id}/cancel` (editor; project-scoped keys; agent kill
  switch; 409 when finished; audited in the same transaction). Workers read
  `run:cancelled:<id>` (72 h TTL) before every step: queued jobs report
  `cancelled` without a browser, a running case stops before its next step and
  still runs suite teardown/cleanups; `run_test_suite` skips a run cancelled
  while queued/deferred; local-worker poll drops its jobs; the mobile worker
  checks at claim and before every step. A cancelled run is never reopened by late results. MCP
  `cancel_run`, CLI `traceiq cancel`. CLI/MCP/Action treat the two statuses as
  terminal and never as a pass. **Webhook deliveries (L3):** one
  `WebhookDelivery` row per attempt (no response body, no URL — errors are
  reduced to a class/reason), retries at 1/5/30 min (4 attempts; not for 410
  or a net_guard refusal), `X-TraceIQ-Delivery` stable across retries and
  redelivery, `GET .../webhooks/{id}/deliveries`, `POST .../deliveries/{d}/
  redeliver` (audited), 30-day sweep. Fixed: any workspace member could
  create/list/edit/delete webhooks (now workspace admin, project-scoped keys
  refused, audited); project-scoped webhooks received every project's runs;
  send-time net_guard + no redirects. Signing secrets are stored as a `v1:`
  envelope (migration `d092b3c4d600` encrypts old rows; read them only via
  `webhook_delivery.signing_secret`; `scripts/rotate_secrets.py` covers them). **Gate versions (L4):** `app/services/gate_policy_versions.py`
  — every evaluation pins the effective policy as an immutable numbered
  version (new one when the SHA-256 differs: PUT, first use, or a changed
  built-in default). `QualityGateResult` carries `policy_version`,
  `policy_sha256`, `ignored_run_ids` (no-verdict runs in scope); replay with
  `GET .../quality-gate?policy_version=&run_ids=`; history at
  `.../quality-gate/policy/versions[/{n}]`. Worker + backend + frontend
  rebuild needed.
- **Supply chain + worker tracing (WP15b, 2026-10-20)** — **H2:**
  `release-images.yml` (still dispatch-only) cosign-signs every pushed image
  by digest (keyless, GitHub OIDC; `id-token: write` on the build job only) in
  every repo it went to, and attaches a CycloneDX SBOM (anchore/sbom-action)
  as a `cosign attest --type cyclonedx`; every action pinned to a commit SHA
  (trivy-action's old `0.28.0` tag no longer exists). Verify commands in
  SELF_HOSTING.md. FastAPI 0.153 / Starlette 1.9 / OTel 1.45 — FastAPI now
  depends on `opentelemetry-api` and ships native telemetry that records
  `url.query`: it is OFF in `main.py` (FastAPIInstrumentor is ours);
  `strict_content_type` is on (body without Content-Type ≠ JSON). **H3:**
  `core/tracing.ts` — jobs carry `traceparent` (stamped by
  `telemetry.stamp_trace` in `_enqueue_job` + the side-job senders; a replayed
  job keeps its original); Celery instrumented in the API process too, so a
  run is one trace API → Celery → worker. One `job` span per job, `case`/
  `step` children reconstructed from step evidence after the job (capped
  100/300). Attributes are an ALLOW-LIST (`safeAttributes`: ids, types,
  statuses, counts, durations — never values/URLs/selectors/errors). SDK
  loaded by a variable-specifier `import()` only when the endpoint is set,
  typed locally (no @types needed); missing packages → off with one warning.
  Worker image rebuild needed (new deps). **H4:** `server.ts` is
  deprecated (removed one release after 2026-10): boot banner
  (`core/legacy-engine.ts`), compose profile `legacy` only, and it ignores
  ai_allowed / egress / cancel. `USE_DISTRIBUTED_EXECUTION` was never a
  Settings field, so `dispatch_legacy_execution` was already unreachable;
  it is now a constant. CONTINUOUS stays one job per case (not one
  shared-browser job — default mode, 10-min job clock; parked in SCOPE_NOTES).

- **Trace correlation (WP16a, 2026-10, decision V1)** — migration
  `e112b3c4d611` (`project.trace_settings` JSON, NULL = off). Opt-in per
  project (`GET/PUT /api/projects/{id}/trace-settings`, admin, audited;
  `{enabled, cross_origin}`). Dispatch (`trace_correlation.for_dispatch`)
  ships `settings.trace_correlation = {hosts, cross_origin}` — the project's
  OWN hosts (`site_crawl.known_hosts_from`: env base URLs ∪ suites'
  `allowed_domains` ∪ security allowlist; a benchmark `target_url` is NOT
  added) — and nothing at capture level `none` or with no hosts. Worker
  `core/trace-context.ts`: one trace id per case attempt (continuous jobs:
  per case), one span id per step, `traceparent 00-<trace>-<span>-01` (no
  `tracestate`); nothing outside a step, an existing traceparent is never
  overwritten. Browser side rides the header-injection route in
  `network-interceptor.ts` (egress guard still runs first); it injects only on
  navigations or same-origin requests unless `cross_origin` (a custom header
  on a cross-origin fetch can force a CORS preflight the app may refuse).
  API steps (http-request/graphql/feed-check — Playwright's request context
  is not routed) inject for own hosts directly. Step evidence gains
  `trace: {trace_id, span_id, requests}` (hex-validated in
  `normalise_step_evidence`; redaction twins leave it alone — corpus tests in
  both). Reading back: `app/services/trace_backends.py`, instance settings
  `TRACE_BACKEND` (jaeger|tempo), `TRACE_BACKEND_URL` (net_guard, own host as
  private exemption, no redirects, 5 s, 5 MB), `TRACE_BACKEND_TOKEN` (Bearer,
  or verbatim `Basic …`), `TRACE_BACKEND_TENANT` (Tempo X-Scope-OrgID).
  `GET /api/runs/{run}/results/{result}/traces` (viewer; scoped keys) maps
  spans to a step ONLY by parent-span ancestry from the step's span id —
  otherwise `no_telemetry` + reason (no ids / no backend / no own-host
  request / trace missing / no descendants / query failed); never by time.
  Attributes are an allow-list redacted with the project policy (no URLs, no
  statements). Caps: 1000 spans/trace, 50/step, 5 traces/result; 60 s
  in-process cache per result, read only after the access check.
  Local-worker results are never looked up (their evidence is API-key
  writable and a trace backend has no tenancy). Failure analysis fetches the
  same payload (`analysis_tasks.backend_error_spans`) and adds `sN.trace`
  for steps with ERROR spans → an app_bug hypothesis; uncited ids still drop.
  Jaeger/Tempo are mock-verified only. Not in local/mobile/legacy workers.
- **Accessibility register + HAR replay (WP15d, 2026-10-20)** — migration
  `e042b3c4d604`. **H6**: `check-accessibility` pushes every outcome (all
  impacts, before deciding to fail) onto the case context's
  `accessibility_results` (`core/accessibility.ts`); the worker ships it like
  `page_budgets` (continuous jobs slice per case) and the aggregator stores
  `AccessibilityCheck` (one per scanned step — the EVIDENCE; a clean scan has
  no violation rows) + `AccessibilityViolation` (rule, impact, redacted target,
  node count, Deque help URL) replace-per-(run, test_name). Never page text:
  the twins `redactAccessibilityResults`/`redact_accessibility_results`
  REBUILD the list from a whitelist (axe `html`/`failureSummary` dropped) and
  `redactA11yTarget` keeps attribute VALUES only for `A11Y_SAFE_ATTRS`
  (id/class/type/role/name/data-testid…, ≤64 chars) — `[href="…"]` → `[href]`.
  Fingerprint = rule|page_key|target. **New** = not in the previous comparable
  run (latest earlier run with a check for the same case id — else test name —
  browser and device); no previous run → everything is new (fail closed; waive
  known issues). Waivers (`AccessibilityWaiver`, rule + `*`-glob over the
  stored target, hand-written linear matcher) follow flake-quarantine rules:
  1-90 days, expired ones exclude nothing, renewal only by explicit PUT `days`;
  all writes audited (`accessibility_waiver`). Gate: policy
  `block_new_accessibility_violations` → check `accessibility_new_violations`
  (serious+ new, unwaived); no check rows in the evaluated runs →
  `missing_data`. API `app/api/accessibility.py`; UI `/accessibility`, rule on
  the Quality page. **H7**: `mock-response` `params.sequence` (nth call → nth
  entry; `after_sequence` `repeat_last` default | `fallthrough`). Step
  `har-replay` (`core/network-mocks.ts`, Playwright `routeFromHAR`,
  `params.har_id`, optional URL glob in `selector`, `not_found`
  `abort` default | `fallback`). Both are PAGE routes, so anything handed on
  (`fallback`) still passes the context-level egress guard. HARs: `POST
  /api/projects/{id}/har-files` (editor, 20 MB, sniffed as HAR JSON, REDACTED
  with the project policy via the new `redact_har` twin BEFORE storage — the
  only copy is redacted) → `har/{project_id}/{uuid}.har` (purge + project
  delete sweep the prefix). The worker gets no URL: `GET
  /api/internal/har-files/{id}?run_id=&test_case_id=` (worker secret) serves
  it only for a live run whose project owns both the case and the HAR;
  `_case_payload` checks the same at dispatch and sets `params.har`/`har_error`
  (caller-written values are overwritten). Not for local/mobile workers. UI:
  Environments → "Recorded traffic (HAR)"; step editor "Network mocks".
  Worker + backend + frontend rebuild needed.
- **API spec history + test ownership (WP16b, 2026-10, V2/V5)** — migration
  `e122b3c4d612`. **Spec history:** every store of the project OpenAPI doc
  (PUT `.../openapi-spec`, `cases/from-openapi`) appends a numbered
  `projectapispecversion` (canonical-JSON SHA-256; identical upload = no new
  version; newest 50 kept + the gate's pinned baseline; DELETE of the current
  spec keeps history; old rows backfilled as v1). `ProjectApiSpec` is still
  what dispatch resolves. `GET .../openapi-spec/versions[/{n}]`,
  `.../diff?from=&to=` (default newest vs previous), `.../diff/sarif`.
  Diff = pure `app/services/openapi_diff.py`, direction-aware (request
  narrowing / response widening breaks). Local `$ref`s only, resolved before
  comparing (a renamed component with the same shape is no change), allOf
  folded, oneOf/anyOf matched by shape, 3.0 `nullable` == 3.1 `type` arrays,
  path params matched by position, readOnly/writeOnly honoured, bounded
  (nodes/depth/2000 changes → `truncated`). Deliberate calls: response enum
  value ADDED = non-breaking (with a closed-enum caveat); response
  nullable→non-null is safe (non-null→nullable breaks); removed 4xx/5xx is
  non-breaking unless a test pins that status, and an operationId rename
  breaks tests using the old id — both promoted in `link_cases`, which names
  the project's own cases whose `expect_schema` references each operation.
  Gate: `require_no_breaking_api_changes` + `api_baseline`
  (`previous`|`pinned`) + `api_baseline_version`; < 2 versions / missing pin
  = `missing_data`. MCP `get_spec_diff`. **Ownership:** `testcase.owner_user_id`,
  `backup_owner_user_id`, `last_reviewed_at/_by_id` — NOT on TestCaseBase (create
  /PUT can't set them), not in revisions, git-sync hash or proposals (metadata,
  not test content; a reassignment must not turn into sync conflicts). `PUT
  /cases/{id}/ownership` (editor; both must be active with project access;
  audited `ownership_set`), `POST /cases/{id}/reviewed` (editor, humans only,
  audited `reviewed`), `GET /projects/{id}/test-review?status=&owner=`. An
  owner who loses access is never cleared — shown `state: no_access`.
  Interval = suite setting `review_interval_days` (inherited execution flag,
  validated in `suite_hooks.validate_suite_settings`; default 90, 0 = no
  review). Owner email (`notify_case_owners` project notification setting,
  off by default) on a case's first test-kind failure after a pass:
  `notification_tasks.notify_case_owners_for_run` in `SIDE_EFFECT_TASKS`;
  owner, else backup; dedupe row `caseownernotice` unique on (case, last
  passing result) committed before sending. Logic in
  `app/services/ownership_review.py` / `app/api/case_ownership.py` (not
  `test_*` — pytest would collect them). Backend + frontend rebuild.

- **Data-capture policy + redaction** (see `info/REGULATED_READINESS.md`) —
  `Project.data_policy` (migration `b7c8d9e0f1a2`, NULL → built-in default)
  declares `capture_level` (`none|minimal|standard|full`), `store_bodies`,
  `redact_headers`/`redact_body_fields`/`redact_patterns`, `mask_selectors`,
  `retention_days`. Resolved by `app/services/data_policy.py:resolve_for_project`
  and **clamped at dispatch** to the `MAX_CAPTURE_LEVEL` instance setting, so a
  worker can't be talked past the ceiling by a crafted job. Unknown levels
  resolve to `standard`, never `full`.
  Video/trace/HAR require `full` — none of them can be meaningfully redacted
  (a trace is a full DOM-snapshot recording), so they are opt-in only.
  **This changed default behaviour**: projects that predate the column no longer
  record video/traces/HAR until opted back up.
  Redaction is two mirrored implementations —
  `execution-engine/src/core/redact.ts` (capture time: the only layer that keeps
  secrets out of the object store) and `app/services/redaction.py` (ingestion: the worker
  image bakes code at build time and can be older than the backend). Their
  corpora (`redact.test.ts`, `tests/test_redaction.py`) are deliberate twins —
  change one, change both. Key denylist + checksum-validated patterns (Luhn for
  PAN, Verhoeff for Aadhaar, JWT); `email`/`phone` exist but are OFF by default
  because blanket-redacting them breaks stored assertions.
  All artifact uploads funnel through `core/artifact-store.ts` — there is no
  other object-store client in the engine; keep it that way or the policy gate is
  bypassable. Object key shapes are asserted in tests because
  `GET /api/runs/{id}/artifact` parses `runs/{run_id}/…` to enforce access.

- **Encryption + transport (workstream D)** — `app/core/secrets.py` writes a
  `v1:` envelope over a **MultiFernet ring**: first key encrypts, any key
  decrypts, so rotation has an overlap window. `SECRETS_KEY` is independent of
  `SECRET_KEY` (previously the same value signed JWTs *and* derived the secret
  key, so neither could be rotated); `SECRETS_KEY_PREVIOUS` holds retired keys,
  and `scripts/rotate_secrets.py` re-encrypts. `legacy_fernet_key()` reproduces
  the pre-envelope derivation and is **pinned by test** — changing it makes
  every existing deployment's secrets unreadable.
  Stored browser sessions (`AuthSession.storage_state`, `Persona.session_state`)
  are wrapped in a `{"__enc__": …}` envelope — still valid JSON, so no migration
  and rows written by a lagging Celery worker stay readable.
  `db_url_for(url, sync=)` is the **only** way to build a DB URL (1 async + 19
  sync engines). Operators write `sslmode=`; it renames to `ssl=` for asyncpg
  **preserving the value** — `ssl=true` raises `ClientConfigurationError`, and
  coercing `verify-full` to a boolean would silently downgrade cert
  verification. `MINIO_USE_SSL` / `MINIO_SSE_ALGORITHM`; SSE is re-stated on
  `copy_object` because a server-side copy does **not** inherit the source
  object's encryption (that path promotes visual baselines).
  `REQUIRE_TRANSPORT_SECURITY` promotes the startup transport findings from
  warnings to a boot refusal — opt-in, because unconditional fatality would
  stop every existing deployment upgrading and push operators to
  `ENVIRONMENT=development`, which disables the secret checks too.

- **Audit trail (workstream E)** — `app/services/audit.py`, migration
  `c8d9e0f1a2b3`. Two complementary mechanisms: a **DB trigger** rejects UPDATE
  always and DELETE except for the retention task (which arms it with a
  transaction-local `SET LOCAL traceiq.audit_retention='on'`), and a **hash
  chain** (`prev_hash`/`row_hash`) makes any edit that bypasses the trigger
  detectable — verified: disabling the trigger, rewriting a row and re-enabling
  it is still caught. Rows predating this verify as *unverifiable*, not intact.
  Rows are sealed by a **`before_flush` listener**, so the 17 legacy inline
  `AuditLog(...)` sites are chained without being rewritten and a new one
  can't break the chain. `record()` is still preferred — it adds actor_type /
  actor_label / IP / user_agent.
  The trigger DDL also lives in `audit.py` as an `after_create` event: since
  the 2026-09 squash fresh installs get it from the root migration like every
  other deployment, and the event copy only serves the `create_all()` path the
  unit-test fixtures use. Keep the two texts identical.
  `auditlog` has **no FKs** to workspace/users — an FK from append-only history
  into a mutable table forces a choice between destroying history and blocking
  deletion.
  Read/export at `/api/workspaces/{id}/audit{,/export,/verify}`, admin-gated.
  `AUDIT_RETENTION_DAYS` is separate from run retention (PCI DSS Req 10 wants a
  year regardless of artifact retention) and defaults to keeping forever.

- **Separation of duties on proposals (workstream F4)** —
  `app/services/proposal_policy.py`, migration `e0f1a2b3c4d5`.
  `CaseProposal.created_by_id` did not exist, so nothing could compare it to
  `decided_by_id` and an editor could accept their own proposal. It now records
  the human behind the call **even for API-key callers** — otherwise "propose
  with my key, accept in the UI" walks around the queue.
  `workspace.require_separate_approver` is opt-in; the `REQUIRE_SEPARATE_APPROVER`
  instance setting is a **floor** a workspace cannot lower; `AUTO_APPLY_DISABLED`
  kills auto-apply instance-wide. Both are instance settings rather than env vars
  so an operator can *show* the control is on. Defaults are off — a solo user
  proposing via their own agent and accepting in the UI is a normal workflow.
  Unattributed (pre-migration) proposals are not conflicts, and **reject is not
  gated** (withdrawing your own proposal is harmless; gating it would strand
  proposals). `GET/PUT /workspaces/{id}/proposal-policy` returns
  `separation_enforced` next to the stored flag; omitted PUT fields are left
  alone so editing the threshold can't silently disable the control.
  **Migration trap (historical):** naming the FK explicitly broke `downgrade`
  on fresh installs, because `bootstrap_db.py` then built from metadata and
  SQLAlchemy named it `caseproposal_created_by_id_fkey`. Fresh installs run the
  migrations now, but still pass `None` — `alembic check` in
  `scripts/verify_migrations.py` compares the two and a named constraint that
  differs from the model's shows up as drift.

- **Deletion and retention (workstream G)** — `app/services/purge.py`,
  `app/services/retention.py`, `app/services/erasure.py`;
  operator-facing summary in `docs/DATA_RESIDENCY.md`.
  `DELETE /api/workspaces/{id}` now cascades (39 tables + object-store prefixes) behind
  `confirm=<workspace name>`, with `dry_run=true`. **The real guarantee is
  `tests/test_purge_plan.py`**: it walks the FK graph in `SQLModel.metadata` and
  fails if a table that can reach `workspace` is neither in `PURGE_PLAN` nor in
  `PURGE_EXEMPT` with a reason — so a table added later cannot silently leak.
  Object keys are collected BEFORE the rows are deleted (they are derived from
  run/build ids). `auditlog` is exempt on purpose.
  Retention is per project now: `data_policy.retention_days` combined with
  `RUN_RETENTION_DAYS`, **shorter window wins** (instance = ceiling), and
  0/absent/negative all mean "keep forever" — never delete-now.
  New sweeps: `purge_derived_records` (`DERIVED_RETENTION_DAYS`; always keeps the
  newest revision per case) and `purge_orphaned_artifacts`
  (`ARTIFACT_ORPHAN_SWEEP_ENABLED`, **report-only by default** — a sweep keyed on
  "the DB doesn't mention this" deletes live artifacts if its query is wrong).
  `storage.delete_prefix` is paginated; the old code silently stopped at
  `list_objects_v2`'s 1000-key cap.
  Erasure (`DELETE /api/auth/me`) returns three parts — erased / retained
  de-identified / retained with reason. Authorship columns keep pointing at the
  scrubbed row (they are the customer's records; deleting them would destroy a
  workspace's test history because an employee left), and the audit trail is
  retained under legal obligation. The erasure audit row deliberately omits the
  email.

- **Operability (workstream H)** — operator guide in `docs/OPERATIONS.md`.
  **H1 beat**: `app/services/beat_health.py` + `app/tasks/heartbeat_tasks.py`.
  Beat *dispatches* the heartbeat and a **worker** executes it — that is the
  point: a beat that is up but can't reach the broker is the same silent stall.
  Written with a TTL so the failure mode is "unknown", not "healthy from a stale
  key". `GET /health/beat` + `traceiq_beat_healthy` /
  `traceiq_beat_heartbeat_age_seconds` (`-1` = never reported).
  Deliberately NOT in `/health/ready` — readiness gates LB rotation, so failing it
  because a scheduler is down converts degradation into an outage.
  `celery-redbeat` is a dep; `CELERY_BEAT_SCHEDULER=redbeat.RedBeatScheduler` is
  opt-in (defaulting it would migrate every existing deployment's schedule state
  file→Redis mid-upgrade). `REDBEAT_LOCK_TIMEOUT` must exceed the longest tick gap
  or two beats fire the same schedule.
  **H2 DLQ**: `app/api/dead_letter.py` — list/replay/discard, instance-admin only
  because the payload carries **resolved project secrets** (hence the list
  summarises, never echoes). Replay MUST clear `jobs:retries` or the job is
  re-killed on first claim; discard is a separate endpoint on purpose.
  **H3**: `bootstrap_db.py` takes a Postgres session advisory lock
  (key 8534217601) and **blocks** rather than skipping — a replica that skipped
  would serve against an unverified schema. The empty Alembic baseline was
  replaced on 2026-09-07 by a real squashed root — see "Migrations" under
  *Known issues* below.
  **H4**: `infrastructure/monitoring/` (Prometheus + alerts + Grafana + Jaeger
  overlay); `metrics_token` is gitignored. **H4 remainder (2026-09-07)**:
  `app/core/telemetry.py` — `LOG_FORMAT=json` (uvicorn/Celery folded in;
  `sys.stdout` wrapped so legacy `print()` becomes JSON — new code uses
  `logging`), `X-Request-ID` middleware (pure ASGI), OpenTelemetry on
  `OTEL_EXPORTER_OTLP_ENDPOINT` (Celery instrumented in `worker_process_init`,
  post-fork, or spans never leave the child), Sentry on `SENTRY_DSN` (PII off,
  bodies off, our secret names in the scrubber, tracing left to OTel). All
  no-ops when unset. Node worker not instrumented. **H5 (2026-09-07)**:
  `deploy/helm/traceiq/` — same env contract as compose via a shared helper,
  `$(VAR)`-assembled URLs so passwords stay in the Secret, memory-backed
  `/dev/shm`, beat on `Recreate`, optional HPA/Ingress/ServiceMonitor and
  eval-grade in-cluster Postgres/Redis/object store; `required` on every secret plus
  `fail` on `webhookSecret == secretKey`; lint/template/kubeconform in CI
  (`helm-chart` job); installed on kind 2026-09-07. Three traps found there,
  all fixed: kubelet refuses `runAsNonRoot` with a non-numeric image USER
  (images now `USER 10001:10001`); nginx's `resolver` ignores the DNS search
  list and the frontend image hardcoded Docker's `127.0.0.11` (now read from
  resolv.conf by `docker-entrypoint.d/16-traceiq-resolver.envsh` — which must
  be executable or the base entrypoint silently ignores it — and the chart
  passes the backend FQDN); an empty `MINIO_PUBLIC_URL` is an invalid boto3
  endpoint (chart omits it; `storage.py` falls back to the internal one).

- **Proving it (workstream I)** — CI now has an `integration-tests` job with
  Postgres + Redis service containers plus a `docker run` of the pinned RustFS
  image (a step rather than a service container, so CI runs the exact
  version + digest the compose stacks and Helm chart pin; readiness is polled
  from the runner on `/health/ready`).
  It runs `tests/integration` and an `alembic downgrade -3 && upgrade head`
  round trip. `tests/integration/*` self-skip unless `TRACEIQ_LIVE_DB` is set.
  `tests/integration/test_tenant_isolation_db.py` is the important one: isolation
  here is **application-layer only** (no RLS, one shared bucket), so every
  boundary is a Python `if`. Five of the eight old `verify_*.py` scripts could not
  even import (they referenced the long-gone `Organization` model) and are
  deleted — they were never convertible.
  Coverage gates: a low global floor (22%; real ~24%) plus a **70% floor on the
  security-critical modules** (currently 86%). A global floor set at aspiration
  rather than reality is theatre; the second gate is the one that means something.
  `pip-audit`/`npm audit` are advisory — a blocking gate on an unfixable
  transitive CVE stops every unrelated PR and the job gets deleted.
  Also fixed here: `get_run`/`create_run` wrapped everything in
  `except Exception` and re-raised as 500, so access denial reached clients as
  `500 Internal Server Error: 403: Access denied`.

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

Also FIXED 2026-08-07 (Phases 1-2 of info/REGULATED_READINESS.md, verified
against a real Postgres and the compiled dist): `fill` now interpolates, so
`{{secret.X}}` works for UI logins instead of forcing plaintext passwords into
`TestCase.steps` (the same omission was in select-option/expect-text/expect-url
and the selector paths of click/check/hover/press-key/expect-*); AuditLog
changes are redacted at write and revision snapshots on read (storage stays
faithful so restore still works); `GET /api/jobs/poll` is editor-gated and
honours `ApiKey.project_id` instead of handing decrypted secrets to any
workspace key; `/metrics` needs `METRICS_TOKEN` or a principal.

Also FIXED 2026-10-12 (see `info/FEATURE_ROADMAP_2026-10.md` §1):
**Celery routing.** Workers in every deployment consume only `main-queue` and
`aggregator-queue`; `task_default_queue` was unset, so any task missing from
`task_routes` went to the implicit `celery` queue and never ran — run
notifications, outbound webhooks, account emails, failure analysis, proactive
heal, persona refresh, tautology scan. Default is now `main-queue`;
`tests/test_celery_routing.py` fails if a task lands on an unconsumed queue.
A new queue needs a worker in EVERY deployment file (compose ×N, AIO
supervisord, Helm) plus the test's `CONSUMED` set. Personas are wired
(`POST /api/runs` body `persona_id`; refresh is a `job_type=persona_refresh`
worker job); `POST /visual-baselines` only accepts bare keys under
`baselines/{case_id}/` (they are presigned back and used as purge prefixes).

Also FIXED 2026-10-13 (branch `fix/per-entity-deletes`): **per-entity
deletes** (project, suite, case, run — single/bulk/all — and the retention
sweep) 500'd on FKs added after they were written. They now share
`app/services/entity_delete.py:cascade_delete`, which walks the FK graph and
applies a per-edge `ON_DELETE` policy (`DELETE` or `NULLIFY`; e.g. heal/case
proposals, issue tickets and monitor checks outlive a deleted run with the link
cleared; project-scoped API keys/webhooks/personas are deleted, never nulled,
because NULL means workspace-wide). `tests/test_entity_delete_plan.py` fails
for any FK into a deletable table without a policy — a new FK into
project/suite/case/run needs an `ON_DELETE` entry. Artifacts are deleted after
commit. Suite/case deletes still 409 on a referencing schedule.
**Project boundary:** the cascade walks FKs by id, and access is checked for
the deleted entity's project only, so a `DELETE` edge that reaches a row with a
*different* non-null `project_id` raises `CrossProjectReference` (409 via an
app exception handler) instead of deleting another project's data. Roots with
no project get an EMPTY allowed set (only project-less rows), never "no
check". The 409 body is generic on purpose — the other project's ids stay in
the server log, since the caller may have no access to them. Retention
retries a refused batch run by run and skips the offender.

Still open:
- Worker image bakes code at build time — new step types need an image rebuild.
  **This is also why the backend redacts on ingestion**: an un-upgraded worker
  ships raw headers and bodies, so `app/services/redaction.py` runs again at all
  three ingestion entry points rather than trusting the worker.
  Workers now HARD-FAIL an unknown step type (the error lists the worker's
  supported types) rather than skipping it silently, so a backend-newer-than-
  worker version skew turns those cases red instead of faking a pass.
- `celery_beat` is required for run finalization (drains `jobs:results` every 2s)
- Credential hygiene (C5): inventoried, locally rotated and history-rewritten
  on 2026-09-07 — see `docs/CREDENTIAL_HYGIENE.md`. The rewritten repo is
  prepared in `~/traceiq-history-backup/` but **the force-push has not been
  done** (human step; every commit id changes, all clones must re-clone, SHAs
  quoted in older notes go stale). Until then treat everything ever committed
  as disclosed. Production `*.thehindu.co.in` credentials and three
  third-party `appKey` values in the old dump are the operator's to rotate.
  CI now runs gitleaks over full history (`.gitleaks.toml` allowlists the
  redaction test corpora by path — fingerprints would break on the rewrite).
- Security baseline (I5 prep): `docs/SECURITY_ASSESSMENT.md` (ZAP/Trivy/
  pip-audit/npm-audit before/after, fixes, accepted risks, tester scope) and
  `docs/SOC2_CONTROLS.md` (TSC → implementation → evidence). nginx now sends a
  CSP (`script-src 'self'`; img/media/connect open because artifacts come from
  the deployment-specific object-store URL), Permissions-Policy, COOP, CORP —
  from `frontend/security-headers.conf`, included at every `add_header` site
  because nginx's add_header in a location replaces inherited headers. The
  remaining pip-audit items need a FastAPI/Starlette major bump: own branch.
- **Migrations (fixed 2026-09-07, verified against a real Postgres).** The
  live chain in `app/alembic/versions/` starts with a real squashed root,
  `e0f1a2b3c4d5_squashed_initial_schema.py` — the whole model schema plus the
  audit trigger, generated with autogenerate and hand-adjusted. Its revision id
  is deliberately the OLD head's: the 49 pre-squash files moved to
  `app/alembic/versions_legacy/` (off `version_locations`), so a database that
  finished them is already at the new root with no stamp. `bootstrap_db.py`
  no longer calls `create_all()`; empty DB = `alembic upgrade head`, and a DB
  stamped *inside* legacy history is bridged by running the legacy chain to its
  head first (plain alembic would say "Can't locate revision"). The trap
  family "bootstrap builds from metadata so migration-only DDL is missing" is
  therefore gone — but non-model DDL still has to be *in the root migration*
  (autogenerate cannot see it). `teststatus` is used by two tables, so enum
  types are created once with `create_type=False`. `f2a3b4c5d6e7` backfills
  four indexes that only one of the two populations (migrated vs. built by
  create_all) had — found by `compare_metadata` against the live DB; its
  downgrade is a no-op on purpose. `scripts/verify_migrations.py`
  (`./run-tests-live.sh --migrations`, and CI) proves upgrade == models
  (`alembic check`), downgrade base leaves an empty database, and the bridge
  works. `tests/test_migration_chain.py` pins the shape without a DB. Two
  small fixes found on the way: `alembic.ini` had an inline comment on
  `version_path_separator` that broke any use of `version_locations`, and
  `script.py.mako` never imported `sqlmodel` although autogenerate emits
  `sqlmodel.sql.sqltypes.AutoString()`.
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
- Images are published by `.github/workflows/release-images.yml`. **Both
  workflows are manual-only since 2026-10-14** (`workflow_dispatch`; the old
  push/PR/tag triggers are kept as comments). Run on a `v*.*.*` tag for
  `:latest`, on `main` for `:edge`. Docker Hub is a second
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
  (`traceiq-aio`): postgres+redis+object store (RustFS)+backend+celery+worker+nginx under
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
