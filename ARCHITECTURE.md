# TraceIQ — Architecture & Operating Guide

> **Audience:** anyone (human or AI agent) who needs to understand what TraceIQ does, how its pieces fit together, where to make a change, and how to operate it. Read this top-to-bottom once; afterwards scan section headings.
>
> **Source of truth.** Code beats this doc whenever they disagree — when you spot a drift, fix the doc.

---

## 1. What TraceIQ is, in one paragraph

**TraceIQ is a multi-tenant SaaS that lets teams record user journeys through any web app, organize them into hierarchical test suites, execute them in distributed Playwright workers, and consume the results as an AI-friendly regression-safety net.** It is built specifically for an industry where AI agents author most code: the value proposition is *"every change — human or AI — gets a verdict on whether existing functionality still works."* The platform spans a FastAPI control plane, a Node.js distributed worker pool, Celery for orchestration, Postgres for state, Redis for the job queue, and MinIO for artifacts. It exposes itself to AI coding agents through API keys, an MCP server, and a GitHub Action.

---

## 2. System map

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                                   USERS                                          │
│                                                                                  │
│  ┌────────────┐    ┌──────────────────┐    ┌────────────────────────────────┐   │
│  │ Web UI     │    │ AI coding agents │    │ CI bots (GitHub PR / GitLab /  │   │
│  │ (React 19) │    │ (Claude Code,    │    │  Jenkins) via the registered   │   │
│  │            │    │  Cursor, …)      │    │  outbound webhooks or Action.  │   │
│  └─────┬──────┘    └────────┬─────────┘    └──────────────┬─────────────────┘   │
│   JWT  │              MCP / X-API-Key                     │                     │
│        ▼                    ▼                             ▼                     │
└────────┼────────────────────┼─────────────────────────────┼─────────────────────┘
         │                    │                             │
         ▼                    ▼                             ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                              CONTROL PLANE                                     │
│                                                                                │
│   FastAPI backend (8000)                                                       │
│     • REST API     /api/auth   /api/runs   /api/cases   /api/workspaces…      │
│     • AuthN/AuthZ  JWT + refresh + API keys → AuthPrincipal → RBAC checks      │
│     • Lifespan     init_db, MinIO bucket bootstrap                             │
│                                                                                │
│   PostgreSQL (5432) — multi-tenant ORM state (SQLModel)                        │
│                                                                                │
│   Celery workers (main-queue, aggregator-queue) + Celery beat                  │
│     • run_test_suite       enqueue jobs to Redis Streams                       │
│     • notification_tasks   email / Slack / Teams                               │
│     • outbound_webhook…    fan-out to registered workspace webhooks            │
│     • result_aggregator    roll up TestCaseResult → TestRun                    │
│     • heal_tasks           proactive selector-heal proposals                   │
│     • persona_tasks        session refresh for personas                        │
│                                                                                │
│   Redis (6379)                                                                 │
│     • Celery broker                                                            │
│     • Job queue: stream `jobs:pending`, consumer group `execution-workers`     │
│     • Run progress: `runs:{id}:progress` hash                                  │
└─────────────────────────────┬──────────────────────────────────────────────────┘
                              │  XADD jobs:pending …  XREADGROUP …
                              ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                               DATA PLANE                                       │
│                                                                                │
│   execution-worker (×N, Node.js + Playwright)                                  │
│     • Claims jobs from `jobs:pending` via XREADGROUP                           │
│     • Runs steps (goto, click, fill, expect, http-request, …)                  │
│     • Captures: video, trace.zip, network HAR, screenshots, execution log     │
│     • Uploads artifacts to MinIO                                               │
│     • POSTs results to /api/runs/{id}/webhook + /api/runs/{id}/finalize        │
│                                                                                │
│   execution-engine (legacy, port 3000)                                         │
│     • Continuous shared-browser mode dispatched via HTTP                       │
│     • Also hosts the persona-refresh + AI failure analyzer modules             │
│                                                                                │
│   MinIO (9000)                                                                 │
│     • bucket: test-artifacts                                                   │
│     • keys: runs/{run_id}/{traces|videos|screenshots}/...                      │
└────────────────────────────────────────────────────────────────────────────────┘
```

All services are containerized — see `infrastructure/docker-compose.yml` and `docker-compose.prod.yml`.

---

## 3. The five most important mental models

1. **A `TestRun` is the unit of work.** It is created in `PENDING`, transitions through `RUNNING`, and ends in `PASSED | FAILED | ERROR`. Everything that happens in TraceIQ — UI updates, notifications, AI analysis, webhook fan-out — is anchored to a run.
2. **Jobs are Redis Stream messages, not Celery tasks.** Celery only kicks off `run_test_suite` and other backend bookkeeping. The actual browser work travels through `jobs:pending` so multiple Node workers can fan out without contention.
3. **Tests are JSON, not code.** A `TestCase.steps` field stores an array of `TestStep` records. The execution-worker's `test-executor.ts` is a step-type switch statement; adding a step type means adding a case there + (optionally) a Pydantic enum value.
4. **There are two layers of identity per request.** A *user* is who owns the credential; a *principal* is "user + how they authenticated this time." `get_current_principal` returns an `AuthPrincipal` whose `is_api_caller` flag tells endpoints whether to default behaviors toward service accounts (e.g., set `triggered_by=api_agent`).
5. **AI-agent integration is layered on top, not bolted in.** The agent-callable surface (API keys, refresh tokens, git context on runs, outbound webhooks, MCP server, GitHub Action, LLM provider abstraction) is additive — humans using the UI continue working exactly as before.

---

## 4. Services & responsibilities

| Service | Stack | Port | Responsibility |
|---|---|---|---|
| `backend` | FastAPI + SQLModel + asyncpg | 8000 | REST API; auth; RBAC; orchestrator |
| `celery_worker` | Celery + Python | — | `run_test_suite`, notifications, webhooks, heal, aggregation |
| `celery_aggregator` | Celery + Python | — | dedicated worker for the aggregator queue |
| `celery_beat` | Celery beat | — | scheduled tasks (cron suites, stale-run cleanup) |
| `execution-worker` (×N) | Node.js + Playwright | — | claims Redis jobs; runs steps; captures artifacts; POSTs results |
| `execution-engine` (legacy) | Node.js + Express | 3000 | continuous shared-browser mode; persona refresh; failure analyzer |
| `postgres` | PostgreSQL 15 | 5432 | system of record |
| `redis` | Redis 7 | 6379 | Celery broker + job streams + progress hashes |
| `minio` | MinIO | 9000/9001 | artifact object storage (S3-compatible) |
| `pgadmin` | pgAdmin 4 | 8014 | DB admin UI |

---

## 5. Data model (the parts that matter)

### 5.1 Tenant tree

```
Tenant ─── Workspace ─── Project ─── TestSuite ─── TestCase (steps: JSON[])
              │              │             │
              ├─ Team        │             └─ self-referential (parent_id) for sub-modules
              ├─ User…       │
              ├─ Persona     ├─ UserProjectAccess (per-user RBAC)
              ├─ ApiKey      └─ TeamProjectAccess (per-team RBAC)
              ├─ Workspace
              │   Webhook
              └─ Workspace
                  Invitation
```

`TestSuite.parent_id` makes suites hierarchical. Settings (headers, params, allowed domains, auth) cascade from parent to child unless `inherit_settings=false`.

### 5.2 Run lifecycle

```
TestRun  ──► (Status: PENDING → RUNNING → PASSED|FAILED|ERROR)
  ├── results: TestCaseResult[]                  (cascade-delete)
  ├── triggered_by: human|schedule|api_agent|ci|webhook
  ├── git_commit / git_branch / git_pr_url / git_repo
  ├── agent_id, api_key_id                       (if launched by a service account)
  ├── baseline_run_id, target_url, persona_id    (Phase C comparison + persona)
  └── ai_analysis: dict                          (populated by /finalize)

TestCaseResult
  ├── status, duration_ms, trace_url, video_url, screenshots[]
  ├── network_events, request/response_headers
  ├── retry_count, confidence, is_flaky          (Phase B smart retry)
  └── ai_analysis: str                           (per-case)
```

### 5.3 AI-agent integration tables (Phase A)

| Table | Purpose |
|---|---|
| `apikey` | Workspace-scoped service-account credentials. `prefix` (`tiq_…`) shown in UI; raw key only at creation time; only SHA-256 hash stored. |
| `refreshtoken` | Rotating refresh tokens for long human sessions. Family-based revoke on replay. |
| `workspacewebhook` | Outbound webhook targets. HMAC-SHA256 signed payloads. |
| `visualbaseline` | Pinned screenshots for `expect-visual-match` step (Phase B). |

### 5.4 Resilience tables (Phase B)

| Table | Purpose |
|---|---|
| `persona` | Reusable Playwright `storageState` + auth headers + auto-login recipe. |
| `selectorhealproposal` | Proactive selector-heal proposals generated by the post-run beat task. |
| `flakerecord` | Per-case flake score + quarantine flag. Quarantined cases are skipped at dispatch. |

### 5.5 Coverage primitives (Phase C)

These don't all have dedicated tables — they're field additions or generation endpoints:
- `TestStep.intent` (additive Pydantic field inside the JSON `steps` column). The durable contract; selectors are disposable.
- `TestRun.baseline_run_id` / `target_url` — deployment-comparison runs.
- `POST /api/cases/generate` — test-from-intent (LLM generates steps from a natural-language description).
- `POST /api/cases/from-openapi` — schema-driven generation (one `TestCase` per documented operation).

---

## 6. End-to-end run lifecycle

This is the single most important flow in the codebase.

```
┌─ 1. Trigger ────────────────────────────────────────────────────────────────┐
│  HUMAN: clicks "Run" in the UI                                              │
│      → POST /api/runs?suite_id=X&browser=chromium                           │
│        body (optional): { git_commit, git_branch, git_pr_url, ... }         │
│  AI AGENT: same endpoint, X-API-Key auth, body MAY include git context.     │
│  CI: same, via the GitHub Action which derives git_commit from $GITHUB_SHA. │
│  SCHEDULE: Celery beat → process_test_schedules creates a TestRun then      │
│           dispatches the same run_test_suite Celery task.                   │
└─────────────────────────────────────────────────────────────────────────────┘

  Backend (backend/app/api/endpoints/test_runs.py:create_run)
  ───────────────────────────────────────────────────────────
  ✓ access_service.has_project_access(min_role="editor")
  ✓ test_service.get_effective_settings (cascade)
  ✓ For each (browser, device) and each leaf TestCase under the suite:
      INSERT TestRun(PENDING, *git_context, triggered_by, …)
  ✓ Commit → returns the created run rows.
  ✓ For each run: run_test_suite.delay(run.id)   (Celery)

┌─ 2. Celery picks up the run (backend/app/worker.py) ────────────────────────┐
│  run.status = RUNNING                                                       │
│  load TestSuite + recursively collect TestCases                             │
│  filter out quarantined FlakeRecords                                        │
│  resolve effective settings                                                 │
│  branch on TestSuite.execution_mode:                                        │
│     SEPARATE   → dispatch_separate_jobs   (1 job per case or sub-suite)     │
│     CONTINUOUS → dispatch_cases_to_queue  (1 job per case, shared semantics)│
│     PARALLEL   → dispatch_parallel_jobs   (1 job per case, parallelism hint)│
│  each XADD → jobs:pending with { run_id, test_case, browser, settings, … } │
│  HSET runs:{id}:progress { total, completed, passed, failed, status=… }     │
└─────────────────────────────────────────────────────────────────────────────┘

┌─ 3. Node worker claims a job (execution-engine/src/worker.ts) ──────────────┐
│  XREADGROUP "execution-workers" → payload                                   │
│  launches Playwright browser                                                │
│  if run.persona_id: hydrate storageState from persona before goto           │
│  iterates steps via test-executor.ts switch:                                │
│     goto / click / fill / expect-* / http-request / feed-check / hover /    │
│     screenshot / press-key / scroll-to / wait-* / switch-frame /            │
│     extract-value / carousel-find / expect-visual-match (Phase B) / …       │
│  semantic-selector fallback: on selector miss, AIEngine.healSelector(...)   │
│  captures trace.zip, video.webm, screenshots, network HAR                   │
│  uploads to MinIO bucket `test-artifacts`                                   │
└─────────────────────────────────────────────────────────────────────────────┘

┌─ 4. Worker reports back ────────────────────────────────────────────────────┐
│  POST /api/runs/{id}/webhook    (per-case results; signed X-TraceIQ-Secret) │
│      → test_service.process_test_run_result writes TestCaseResult rows      │
│      → run progress hash updated                                            │
│  POST /api/runs/{id}/finalize   (summary + optional aiAnalysis dict)        │
│      → run.ai_analysis stored                                               │
│      → notification_tasks.send_run_notifications.delay(...)                 │
│      → outbound_webhook_tasks.dispatch_run_webhooks.delay(...)              │
│      → heal_tasks.propose_selector_heals_for_run.delay(...) (Phase B)       │
└─────────────────────────────────────────────────────────────────────────────┘

┌─ 5. Consumers ──────────────────────────────────────────────────────────────┐
│  UI: polls GET /api/runs/{id} (every 15s on Dashboard, every 2s on detail). │
│  AI agents (MCP): wait_for_run → returns final TestRun + structured results.│
│  CI (GitHub Action): polls, posts PR comment, sets check status.            │
│  Registered outbound webhooks: receive run.completed / run.failed POSTs     │
│    with HMAC-SHA256 signature in X-TraceIQ-Signature.                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Where the lifecycle hooks live

| Hook point | File | Function |
|---|---|---|
| Create run | `backend/app/api/endpoints/test_runs.py` | `create_run` |
| Dispatch fan-out | `backend/app/worker.py` | `run_test_suite`, `dispatch_*_jobs` |
| Worker claim | `execution-engine/src/worker.ts` | main loop |
| Step execution | `execution-engine/src/core/test-executor.ts` | `executeStep` switch |
| Result ingest | `backend/app/services/test_service.py` | `process_test_run_result` |
| Finalize | `backend/app/api/endpoints/test_runs.py` | `finalize_test_run` |
| Notifications | `backend/app/tasks/notification_tasks.py` | `send_run_notifications` |
| Outbound webhooks | `backend/app/tasks/outbound_webhook_tasks.py` | `dispatch_run_webhooks` |
| Selector heal proposals | `backend/app/tasks/heal_tasks.py` | `propose_selector_heals_for_run` |

---

## 7. Authentication & authorization

### 7.1 Three credential types

| Credential | How obtained | Where used | TTL |
|---|---|---|---|
| **Access JWT** | `POST /api/auth/login` (email/password) | `Authorization: Bearer …` | 30 minutes |
| **Refresh token** | issued alongside access token; rotated via `POST /api/auth/refresh` | body of `/refresh` and `/logout` | 30 days, single-use |
| **API key** | `POST /api/workspaces/{id}/api-keys` (returned once, `tiq_…`) | `X-API-Key: …` | optional expiry + revocable |

### 7.2 Principal resolution

`backend/app/core/auth.get_current_principal` is the canonical dependency. It:

1. Looks for `X-API-Key` header → resolves to `(ApiKey, backing User)` → `AuthPrincipal(is_api_caller=True)`.
2. Falls back to `Authorization: Bearer <jwt>` → resolves to User → `AuthPrincipal(is_api_caller=False)`.
3. Returns 401 if neither resolves.

Endpoints typically depend on `get_current_user` (a back-compat shim that returns `principal.user`), unless they care whether the caller was an AI agent. `create_run` and `create_comparison_run` depend on `get_current_principal` directly so they can:
- Default `triggered_by` to `api_agent` when an API key is used.
- Pick up `X-Agent-Id` (e.g. `claude-code`) and attach it to created runs.
- Populate `api_key_id` so the run is auditable back to a specific credential.

### 7.3 RBAC layers (still authoritative)

`backend/app/services/access_service.has_project_access(user_id, project_id, session, min_role=...)`:

1. Workspace admin → full access.
2. `UserProjectAccess` direct grant.
3. `TeamProjectAccess` via team membership.
4. Otherwise deny.

Roles ladder: `viewer` (read) < `editor` (create/edit/run) < `admin` (manage members/delete).

### 7.4 Internal service auth

Worker → backend webhooks (`/runs/{id}/webhook`, `/runs/{id}/finalize`) authenticate via the `X-TraceIQ-Secret` header, validated against `settings.WEBHOOK_SECRET || settings.SECRET_KEY`. This is *not* a JWT; it's a long-lived shared secret. Rotate by updating `WEBHOOK_SECRET` in env and redeploying both backend and workers atomically.

---

## 8. AI integration map — where LLMs are called and why

| Location | Trigger | What's sent | What's used for |
|---|---|---|---|
| `execution-engine/src/ai.ts` | Step selector misses during a run | Broken selector + DOM snapshot (≤20 000 chars) | **Reactive heal** — return a replacement selector to retry the step. Cached per-run; capped at `AI_MAX_HEALS_PER_RUN`. |
| `execution-engine/src/controller/ai-analyzer.ts` | Finalize callback when `AI_ANALYSIS_ENABLED=true` | Failed test names + error messages | Structured `FailureAnalysis` JSON (summary, root causes, suggested fixes, per-test confidence). Stored on `TestRun.ai_analysis`. |
| `backend/app/ai/engine.py` | Legacy in-process runner path | Error log + DOM | Plain-English explanation of why a test failed. |
| `backend/app/tasks/heal_tasks.py` | Beat task after each finalized run (`PROACTIVE_HEAL_ENABLED=true`) | Stored selector + intent + DOM snapshot from execution log | **Proactive heal** — emits `SelectorHealProposal` rows for human review or auto-apply. |
| `backend/app/api/case_generation.py` | `POST /api/cases/generate` | NL description + target URL | LLM emits a draft `TestCase.steps` array. |

All five hit the same `app.ai.providers.provider` (Python) / `execution-engine/src/llm-provider.ts` (TS) singleton. Switch providers via `LLM_PROVIDER=openai|anthropic` + `LLM_MODEL=...`. Without a key, the provider degrades to `NullProvider` and every caller falls back to non-AI behavior — *nothing crashes when AI is unavailable*.

---

## 9. Distributed execution internals

### 9.1 Redis Streams contract

| Stream / key | Producer | Consumer | Shape |
|---|---|---|---|
| `jobs:pending` (stream) | backend Celery worker via `XADD` | `execution-worker` via `XREADGROUP` consumer group `execution-workers` | `{ job_id, run_id, payload (JSON) }` |
| `jobs:results` (stream) | execution-worker | result aggregator | per-step results |
| `runs:{run_id}:progress` (hash) | backend + worker | UI/agents | `{ total, completed, passed, failed, status, execution_mode }` |
| `runs:{run_id}:job_ids` (set) | backend | cancel/cleanup | job ids belonging to this run |

### 9.2 Three execution modes

| Mode | Behavior | Use when |
|---|---|---|
| `SEPARATE` | One job per test case (or one job per sub-suite for hierarchical fan-out). Each job gets a fresh browser context. | Strong isolation, especially for tests that mutate state. |
| `CONTINUOUS` | One job per test case but tests within the same sub-suite share a browser; or dispatched to the legacy execution-engine when `USE_DISTRIBUTED_EXECUTION=false`. | Speed + shared session affinity (e.g. login once, run 20 checks). |
| `PARALLEL` | One job per test case, tagged with a parallelism hint (`PARALLEL_MAX_CONCURRENCY`). Workers fan out aggressively. | AI-agent rapid feedback — many runs, low latency. |

The mode is set on `TestSuite.execution_mode`. AI agents firing many checks should switch their suites to `PARALLEL`.

### 9.3 Artifact contract

Every test case produces, at minimum:
- A Playwright trace zip (`runs/{run_id}/traces/{job_id}.zip`).
- A WebM video (`runs/{run_id}/videos/{test_case_id}.webm`).
- A list of screenshot PNGs (one per `screenshot` step + the trace's own).
- A network HAR-shaped JSON array on `TestRun.network_events` and `TestCaseResult.response_*`.
- An execution log JSON (per-step timings + errors) on `TestRun.execution_log`.

Artifact URLs in the DB are *MinIO object paths*. Resolve to presigned URLs via `GET /api/artifacts/{path}`.

---

## 10. Multi-tenancy

`Tenant → Workspace → Project → TestSuite` is the ownership chain. Every securable row carries a foreign key up this chain (directly or transitively via `project_id`).

The default rules a new agent should expect:
- `Tenant` is the top-level isolation boundary — no cross-tenant data access is possible through the supported API surface.
- A `User` may belong to many workspaces via `UserWorkspace` (with a role). A user can also be a `Tenant Admin` via `UserSystemRole`, granting workspace-creation rights inside the tenant.
- `Project` is the smallest unit at which RBAC roles attach (besides one-off `UserTestCaseAccess` overrides).
- `ApiKey`, `WorkspaceWebhook`, and `Persona` are workspace-scoped. `VisualBaseline` and `SelectorHealProposal` are test-case scoped (and inherit project membership via the case).

---

## 11. Extension points (how to add things)

### 11.1 Add a new step type

1. **Backend (optional but recommended):** add an enum value in `TestStep` documentation. (No DB change — steps are JSON.)
2. **Execution-worker:** add a `case 'your-step-type':` arm in `execution-engine/src/core/test-executor.ts`. Implement using Playwright `page.*` APIs.
3. **Recorder:** add a capture for the new interaction in `integrations/browser-recorder/content.js`.
4. **AI generation:** add the step to the system prompt in `backend/app/api/case_generation.py` so generated cases can use it.

### 11.2 Add a notification channel (Discord, etc.)

1. Add a new function in `backend/app/tasks/notification_tasks.py` following the email/Slack/Teams pattern.
2. Add env-var settings in `backend/app/core/config.py`.
3. Add a master + per-channel flag the same way (`<CHANNEL>_NOTIFICATIONS_ENABLED`).

### 11.3 Add a new LLM provider (e.g., Gemini, local Ollama)

1. Implement the `LLMProvider` protocol in `backend/app/ai/providers.py` with a `complete(...)` method.
2. Mirror in `execution-engine/src/llm-provider.ts`.
3. Add the dispatch case in both `pickProvider()` functions.
4. Set `LLM_PROVIDER=<name>` in env.

### 11.4 Add a new outbound integration (Linear, Jira, etc.)

Two paths:
- **Webhook-based:** the user registers a `WorkspaceWebhook` pointing at the integration's URL. No code needed.
- **Native:** add a dispatch task in `backend/app/tasks/` and invoke from `/finalize` alongside the existing fan-out.

### 11.5 Add an AI-agent tool (extending MCP server)

1. Add a Pydantic-ish input model in `integrations/mcp-server/src/traceiq_mcp/server.py` (extend the `list_tools` array).
2. Add a `if name == "your_tool":` arm in `call_tool`.
3. Wrap the underlying REST call in `integrations/mcp-server/src/traceiq_mcp/client.py`.

---

## 12. Operational notes

### 12.1 Deploying

```bash
# Production
cd infrastructure
docker compose --env-file env.prod -f docker-compose.prod.yml up -d --build

# Local dev (hot-reload, volume mounts)
docker compose --env-file env.local -f docker-compose.yml up -d --build
```

Scale workers:
```bash
docker compose -f docker-compose.yml up -d --scale execution-worker=8
```

### 12.2 Migrations

```bash
cd backend
alembic upgrade head
alembic revision --autogenerate -m "your change"
```

Current migration chain (newest first):
```
d6f9a3b4c5d6  phase_b_c                                  (personas, heal proposals, flake records, comparison cols)
c5d8f1a2b3c4  ai_agent_integration                       (api keys, refresh tokens, webhooks, visual baselines, git context, run trigger)
b2c4e6f8a0d1  backfill_role_id_from_string_fields        (RBAC migration)
a7b3c9d2e1f4  add_performance_indexes                    (hot-column indexes)
1f266105057e  baseline_with_schedules                    (baseline schema)
```

### 12.3 Required env vars (the ones that actually matter)

| Var | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | Postgres DSN (`postgresql+asyncpg://…`) | — |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | Redis URLs | — |
| `MINIO_ENDPOINT` / `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | Artifact storage | — |
| `MINIO_PUBLIC_URL` | URL clients use to fetch artifacts | `http://localhost:9000` |
| `SECRET_KEY` | JWT signing + internal webhook fallback | — |
| `WEBHOOK_SECRET` | Internal service-to-service webhook secret | falls back to `SECRET_KEY` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT TTL | `30` |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Refresh-token TTL | `30` |
| `LLM_PROVIDER` | `openai` \| `anthropic` | inferred from which key is set |
| `LLM_MODEL` | model name override | provider default |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | provider keys | empty (→ null provider) |
| `AI_ANALYSIS_ENABLED` | enable controller AI failure analysis | `false` |
| `AI_MAX_HEALS_PER_RUN` | reactive heal cost cap | `10` |
| `PROACTIVE_HEAL_ENABLED` | enable post-run heal proposal task | `false` |
| `PARALLEL_MAX_CONCURRENCY` | cap on parallel fan-out per run | total cases in run |
| `USE_DISTRIBUTED_EXECUTION` | toggle distributed vs legacy engine | `true` |
| `BACKEND_CORS_ORIGINS` | comma-list of allowed origins | `["*"]` (verify in prod!) |

### 12.4 Common operational tasks

| Task | Where |
|---|---|
| Force-complete a stuck run | `POST /api/runs/{id}/force-complete` (editor role required) |
| Stale-run cleanup | `backend/app/tasks/cleanup_tasks.py` (Celery beat) |
| Retry a failed webhook | `scripts/retry-failed-webhooks.sh` |
| Verify webhook queue depth | `scripts/verify-webhook-queue.sh` |
| Bootstrap a new tenant admin | `backend/scripts/assign_tenant_admin.py` |
| Reset local DB | `backend/scripts/reset_db.py` |

### 12.5 Observability

Each service logs to stdout. There is **no first-class OpenTelemetry wiring yet** — Phase D in the long-term plan. For now:
- `docker compose logs -f backend` for the API.
- `docker compose logs -f celery_worker` for orchestration.
- `docker compose logs -f execution-worker` for browser runs (the noisy one).
- Redis stream depth: `XLEN jobs:pending`.
- DB hot queries: indexes from migration `a7b3c9d2e1f4` cover the highest-frequency cases.

---

## 13. AI-agent integration layer (the recent additions)

This section is here because the agent-callable surface is the strategic differentiator and a future agent reading this doc will care most about it.

### 13.1 What an AI coding agent can do today

| Action | Mechanism |
|---|---|
| Authenticate as a service account | Mint an `ApiKey` (UI or `POST /api/workspaces/{id}/api-keys`); send via `X-API-Key`. |
| Identify itself per request | `X-Agent-Id: claude-code` (or any string). Stored on every created run. |
| Trigger a run scoped to a code change | `POST /api/runs?suite_id=X` with JSON body `{ git_commit, git_branch, git_pr_url, git_repo, triggered_by }`. |
| Wait for results | `GET /api/runs/{id}` (poll) — or `wait_for_run` via the MCP server which encapsulates the polling. |
| Consume structured failure analysis | `GET /api/runs/{id}` → `ai_analysis` field. |
| Be notified out-of-band | `POST /api/workspaces/{id}/webhooks` to register an HMAC-signed outbound webhook. |
| Compare two deploys | `POST /api/runs/comparison` with `{ baseline_run_id, target_url }`. |
| Skip flaky tests programmatically | `POST /api/flakes/{id}/quarantine`. |
| Author tests | `POST /api/cases/generate` (NL) or `POST /api/cases/from-openapi` (schema). |

### 13.2 Distribution channels

| Channel | Path | Status |
|---|---|---|
| Direct REST | the entire `/api/*` surface | shipped |
| MCP server (stdio) | `integrations/mcp-server/` | shipped; install via `pip install -e .` |
| GitHub Action | `integrations/github-action/` | scaffold shipped; needs `npm run build` + `dist/` commit before publishing |
| Chrome recorder extension | `integrations/browser-recorder/` | scaffold shipped; load unpacked from `chrome://extensions` |

### 13.3 Recommended agent workflow

```
Coding agent opens PR for change C
        │
        ▼
GitHub Action picks up commit SHA → POST /api/runs (suite + git context)
        │                                  ↑ default triggered_by=ci because action sets it
        ▼
Action polls /api/runs/{id} every 10s (configurable)
        │
        ▼
Run finishes; ai_analysis dict populated by execution-engine controller
        │
        ├── If pass: action posts "✅ TraceIQ verified" PR comment; check passes; merge unblocked
        │
        └── If fail: action posts a structured PR comment (trace links, failed test names,
                     AI's suggested fix); check fails; merge blocked.
                     The agent reads the structured report, fixes its code, pushes a new commit,
                     and the loop repeats.
```

### 13.4 Open follow-ups for the agent layer

See `SCOPE_NOTES.md`. The most impactful unfinished items:

- Frontend UI for managing API keys / webhooks / personas / heal proposals / flake records (all the new entities have endpoints; none have UIs yet).
- Streamable HTTP transport for the MCP server (currently stdio only — fine for local IDE agents, blocks hosted ones).
- A structured `failure_report.py` schema so `ai_analysis` has a typed contract that the GitHub Action + MCP server can consume directly.
- Full Playwright recorder fidelity (drag-drop, iframe support) in the browser extension.

---

## 14. How to make changes safely

### 14.1 Adding a backend endpoint

1. Create the route in a `backend/app/api/<feature>.py` module.
2. Depend on `get_current_principal` (preferred) or `get_current_user` (back-compat shim).
3. Use `access_service.has_project_access(...)` (or `has_test_case_access`) before any mutation.
4. Register the router in `backend/app/main.py`.
5. If the endpoint accepts an LLM operation, hit `from app.ai.providers import provider` — never instantiate clients directly.

### 14.2 Changing the data model

1. Edit `backend/app/models.py`.
2. Write an Alembic migration in `backend/app/alembic/versions/`. Increment the revision chain. Idempotent DDL preferred.
3. Test locally with `alembic upgrade head` against a fresh DB.
4. Update `ARCHITECTURE.md` if the field is on a public-API model.

### 14.3 Changing the worker / step semantics

1. Edit `execution-engine/src/core/test-executor.ts` (step switch).
2. Update `info/scripting_guide.md` if the change is user-visible.
3. The Node side has no migration; old test JSON must keep working — make changes additive.

### 14.4 Changing AI behavior

1. Implementation goes through `app/ai/providers.py` (Python) or `execution-engine/src/llm-provider.ts` (TS) — never raw SDK calls in business code.
2. If you add a new prompt, log the model + max-tokens + cache strategy alongside.
3. Stay within existing cost caps: `AI_MAX_HEALS_PER_RUN`, prompt size truncation (typically 8K–20K chars of DOM).

---

## 15. Glossary

| Term | Meaning |
|---|---|
| **Test step** | A single Playwright action / assertion encoded as a JSON object inside `TestCase.steps`. |
| **Intent** | A natural-language description of what a step targets. The durable contract; selectors are disposable. |
| **Persona** | A reusable Playwright `storageState` + auth headers + auto-login recipe. |
| **Run** | A single execution of a suite (or case), recorded as a `TestRun` row. |
| **Comparison run** | A run with `baseline_run_id` set — same suite, possibly different target URL. |
| **Quarantine** | Marking a `FlakeRecord` as `is_quarantined=true` so the test is skipped at dispatch. |
| **Heal proposal** | A `SelectorHealProposal` row suggesting a selector update for a step. |
| **Principal** | The acting entity for a request — backed by a User but tagged with `is_api_caller` if it came in via an API key. |
| **Trigger** | `human | schedule | api_agent | ci | webhook` — recorded on every run. |

---

## 16. Related docs

- `CLAUDE.md` — quick orientation for Claude Code sessions; commands + structure.
- `info/CODEBASE.md` — older, more detailed walkthrough of the original codebase (pre-Phase-A).
- `SCOPE_NOTES.md` — what was shipped vs. deferred for each phase, with verification checklists.
- `info/scripting_guide.md` — user-facing reference for writing test steps.
- `MIGRATION_GUIDE.md` (repo root) and `backend/scripts/MIGRATION_PARALLEL_EXECUTION.md` — historical migration notes.
- `integrations/*/README.md` — per-integration setup (MCP server, GitHub Action, browser recorder).

When in doubt, **read the code**. This doc reflects the state at commit time; the code is what runs.
