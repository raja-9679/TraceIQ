# TraceIQ — Production Deployment Runbook

> **Audience:** the operator running this deploy (SRE / devops / on-call).
> **Goal:** safely roll new TraceIQ code into a production environment that
> already has live data — workspaces, projects, test suites, runs, artifacts
> in MinIO — without losing state or breaking active users.
>
> Two scenarios are covered:
> 1. **First-time upgrade** through Phase A–E (4 schema migrations, 8 commits).
>    Use this when prod is currently on `main` (commit `bf271c2`) and you
>    want to ship everything on `feature/ai-agent-integration` in one go.
> 2. **Steady-state feature updates** — what to do for every subsequent
>    feature commit. Lighter checklist, same rollback shape.
>
> If anything in this doc and the live system disagree, trust the live system
> and fix the doc. Code beats prose.

---

## Quick navigation

- [Part 1 — First-time Phase A-E upgrade](#part-1--first-time-phase-ae-upgrade)
- [Part 2 — Steady-state feature updates](#part-2--steady-state-feature-updates)
- [Part 3 — Reference](#part-3--reference)
- [Part 4 — Post-deploy follow-ups](#part-4--post-deploy-follow-ups)

---

# Part 1 — First-time Phase A-E upgrade

## 1.1 What this deploy ships

Eight commits on `feature/ai-agent-integration`. Net effect: TraceIQ becomes
agent-callable with mandatory human review.

| Category | What lands |
|---|---|
| **Schema** | 4 new migrations on top of `b2c4e6f8a0d1`. New tables: `apikey`, `refreshtoken`, `workspacewebhook`, `visualbaseline`, `persona`, `selectorhealproposal`, `flakerecord`, `caseproposal`. New columns on `testrun`, `testcase`, `testcaseresult`, `testsuite`, `workspace` |
| **Auth** | API-key support (`X-API-Key`), refresh tokens with rotation-on-use, `X-Agent-Id` + `X-Agent-Session-Id` headers captured into provenance |
| **AI integration** | Pluggable LLM provider (OpenAI / Anthropic / null); proactive selector heal task; tautology detector; structured failure-report on every finalize |
| **Test authoring** | `CaseProposal` queue (API keys can't auto-merge); `code_paths`; impact analysis on PR diffs; bulk propose + bulk-set code paths; test-from-intent and test-from-OpenAPI generation |
| **Execution** | PARALLEL execution mode routed; visual regression via pixelmatch in the worker; persona-based auth hydration |
| **Bug fixes** | `DELETE /api/cases/{id}` + `DELETE /api/suites/{id}` now cascade correctly (used to 500 on FK); schedule-fired runs now correctly tagged `triggered_by='schedule'`; pre-existing migration `a7b3c9d2e1f4` table-name typo fixed |
| **Docs + integrations** | `ARCHITECTURE.md`, `SCOPE_NOTES.md`, `AGENT_GUIDE.md`, MCP server scaffold (17 + 9 = 26 tools), GitHub Action scaffold, browser-recorder MV3 extension, TodoLite demo app |

## 1.2 Pre-flight checklist

Before *any* command in §1.3, complete these. Skipping any of them is the
most likely cause of a failed rollback.

| # | Check | How |
|---|---|---|
| 1 | **Full Postgres backup**, restorable to a fresh DB | `pg_dump -U <user> -d quality_intelligence --format=custom --file=/var/backups/traceiq/pre-phase-a-e-$(date +%Y%m%d-%H%M).pgdump` |
| 2 | **Backup is restorable** (smoke-test on a throwaway instance, even if just `pg_restore --list backup.pgdump | head`) | Don't skip this — corrupted backups have ended careers |
| 3 | **MinIO bucket backup** if you have critical historic artifacts | `mc mirror <minio>/test-artifacts /var/backups/traceiq/minio-$(date +%Y%m%d)` — or rely on a snapshot at the storage layer |
| 4 | **Current alembic head** noted in the deploy log | `docker exec <backend> alembic current` — expect `b2c4e6f8a0d1` |
| 5 | **Drain the job queue** (or accept that in-flight runs will retry) | `docker exec <redis> redis-cli XLEN jobs:pending` — wait until 0, or stop the celery worker to halt new dispatches |
| 6 | **Confirm `infrastructure/.env`** has correct Postgres password matching the DB volume | `docker exec <backend> sh -c 'echo $DATABASE_URL'` and confirm it can connect: `docker exec <backend> python -c "import asyncio, asyncpg, os; print(asyncio.run(asyncpg.connect(os.environ['DATABASE_URL'].replace('+asyncpg','')).then(lambda c: c.close())))"` |
| 7 | **Maintenance window communicated** if you have an SLA — expect ~5–15 minutes of API impact for a non-blue-green deploy |
| 8 | **Rollback plan rehearsed** — see §1.7. Operator should be able to `alembic downgrade` and re-deploy the previous commit |

## 1.3 Required environment variables

All optional with sensible defaults. Recommended starting values for a first deploy:

| Var | Purpose | First-deploy recommendation |
|---|---|---|
| `LLM_PROVIDER` | `openai` \| `anthropic` (selector heal + failure analysis + case generation) | leave unset (auto-detected from whichever API key is present) |
| `ANTHROPIC_API_KEY` | enables Claude for AI features | only if you intend to use Anthropic; otherwise leave unset |
| `PARALLEL_MAX_CONCURRENCY` | caps PARALLEL-mode fan-out per run | leave unset (defaults to total cases in the run) |
| `PROACTIVE_HEAL_ENABLED` | enables the post-run selector-heal proposal task | **`false`** for first deploy. Turn on later after observing behavior |
| `TAUTOLOGY_DETECTOR_ENABLED` | enables flake-suspect detector | **`false`** for first deploy |
| `REFRESH_TOKEN_EXPIRE_DAYS` | refresh-token TTL | leave unset (30 days) |
| `AI_ANALYSIS_ENABLED` | enables LLM-based failure analysis in execution-engine | only after `LLM_PROVIDER` is set |
| `AI_MAX_HEALS_PER_RUN` | per-run cost cap on selector healing | leave unset (10) |
| `TRACEIQ_AGENT_GUIDE_PATH` | operator-override for AGENT_GUIDE.md path | leave unset (uses bundled copy) |

## 1.4 Dependency rebuilds

| Image | New deps | Action |
|---|---|---|
| **Backend** (shared by backend + celery_worker + celery_aggregator + celery_beat) | `anthropic==0.39.0`, `PyYAML==6.0.1` | rebuild required |
| **Execution-engine** + **execution-worker** | `pixelmatch`, `pngjs` | rebuild required |
| **Frontend** | none | no rebuild needed |
| **Postgres / Redis / MinIO** | none | not touched |

## 1.5 Deploy sequence

Execute in this order. Each step is a hard checkpoint — if anything is
unexpected, stop and go to §1.7.

```bash
# ───────────────────────────────────────────────────────────────────────
# Step 1: Get the new code on the host
# ───────────────────────────────────────────────────────────────────────
cd /path/to/TraceIQ
git fetch origin

# Either deploy directly from the feature branch:
git checkout feature/ai-agent-integration
git pull
# … OR after merge to main, from main:
# git checkout main && git pull

# Note the new HEAD sha for the deploy log:
git rev-parse HEAD
```

```bash
# ───────────────────────────────────────────────────────────────────────
# Step 2: Build the new images BEFORE stopping anything
#         (so a build failure doesn't leave you with no running service)
# ───────────────────────────────────────────────────────────────────────
cd infrastructure

docker compose -f docker-compose.prod.yml build \
  backend celery_worker celery_aggregator celery_beat \
  execution-worker execution-engine

# Verify the new images exist:
docker images | grep -E 'backend|execution' | head -10
```

```bash
# ───────────────────────────────────────────────────────────────────────
# Step 3: Stop celery beat FIRST
#         Prevents any scheduled run from firing mid-migration.
# ───────────────────────────────────────────────────────────────────────
docker compose -f docker-compose.prod.yml stop celery_beat
```

```bash
# ───────────────────────────────────────────────────────────────────────
# Step 4: Apply migrations
#         All 4 in one `alembic upgrade head` call.
#         Expected runtime: seconds to a couple of minutes.
# ───────────────────────────────────────────────────────────────────────
docker compose -f docker-compose.prod.yml run --rm backend alembic upgrade head

# Confirm the new head:
docker compose -f docker-compose.prod.yml run --rm backend alembic current
# Expected output: f8b3c4d5e6f7 (head)
```

> ⚠️ **Index creation note.** Phase A creates 3 non-CONCURRENT indexes on
> `testrun` (`ix_testrun_git_commit`, `ix_testrun_triggered_by`,
> `ix_testrun_api_key_id`). On a large `testrun` table this briefly
> ACCESS-EXCLUSIVE-locks the table. If your `testrun` is large enough
> that this is unacceptable, see §3.5 for the manual CONCURRENTLY recipe.

```bash
# ───────────────────────────────────────────────────────────────────────
# Step 5: Restart the Python services (rolling if you have >1 replica)
# ───────────────────────────────────────────────────────────────────────
docker compose -f docker-compose.prod.yml up -d \
  backend celery_worker celery_aggregator celery_beat

# Wait for backend healthy
sleep 5
curl -sf https://your-traceiq.example.com/health || echo "BACKEND NOT HEALTHY — STOP"
```

```bash
# ───────────────────────────────────────────────────────────────────────
# Step 6: Restart execution workers + legacy engine
# ───────────────────────────────────────────────────────────────────────
docker compose -f docker-compose.prod.yml up -d execution-worker execution-engine
```

```bash
# ───────────────────────────────────────────────────────────────────────
# Step 7: Smoke test (see §1.6 for the full verification)
# ───────────────────────────────────────────────────────────────────────
curl -sf https://your-traceiq.example.com/health
# Log in via the UI; trigger a known-good run; confirm it lands.
```

## 1.6 Post-deploy verification

Run all of these. Each one should return what's described.

### 1.6.1 Schema state

```sql
-- Connect to the prod DB. Run as the application user.
SELECT version_num FROM alembic_version;
-- Expected: f8b3c4d5e6f7

-- All 8 new tables present?
SELECT table_name FROM information_schema.tables
WHERE table_schema='public' AND table_name IN (
  'apikey','refreshtoken','workspacewebhook','visualbaseline',
  'persona','selectorhealproposal','flakerecord','caseproposal'
)
ORDER BY table_name;
-- Expected: 8 rows

-- New columns on testrun?
SELECT column_name FROM information_schema.columns
WHERE table_name='testrun' AND column_name IN (
  'git_commit','git_branch','git_pr_url','git_repo',
  'triggered_by','agent_id','api_key_id',
  'baseline_run_id','target_url','persona_id'
)
ORDER BY column_name;
-- Expected: 10 rows
```

### 1.6.2 Existing data sanity

```sql
-- All existing testrun rows got the safe default 'human' for triggered_by?
SELECT COUNT(*) FROM testrun WHERE triggered_by IS NULL;
-- Expected: 0

-- No agent-authored cases before deploy (correct):
SELECT COUNT(*) FROM testcase WHERE is_ai_authored=true;
-- Expected: 0

-- New tables empty (correct — they fill as features get used):
SELECT
  (SELECT COUNT(*) FROM apikey) AS api_keys,
  (SELECT COUNT(*) FROM refreshtoken) AS refresh_tokens,
  (SELECT COUNT(*) FROM caseproposal) AS proposals,
  (SELECT COUNT(*) FROM workspacewebhook) AS webhooks;
-- Expected: 0 / 0 / 0 / 0  (will grow as features get used)
```

### 1.6.3 API surface

```bash
# Old endpoints still respond
curl -sS https://your-traceiq.example.com/api/auth/login -X POST \
  -d "username=&password=" -H "Content-Type: application/x-www-form-urlencoded" \
  -o /dev/null -w "%{http_code}\n"
# Expected: 401 (not 500)

# New endpoints are mounted
curl -sf https://your-traceiq.example.com/api/step-types | jq '.total'
# Expected: 25  (or whatever the catalog count is)

curl -sf https://your-traceiq.example.com/api/agent-guide | jq '.size_chars'
# Expected: > 15000
```

### 1.6.4 Full functional smoke

1. Log in via the UI as an existing user — works as before.
2. Open a test suite, open a recent run — UI loads normally.
3. Trigger one run on a low-risk suite — runs to completion, lands a result.
4. (If you have schedules) Verify next scheduled fire creates a run tagged
   `triggered_by='schedule'` (was `'human'` before the deploy):
   ```sql
   SELECT id, triggered_by, agent_id, created_at FROM testrun
   ORDER BY id DESC LIMIT 5;
   ```

## 1.7 Rollback

If anything in §1.5 or §1.6 fails, rollback in reverse order:

```bash
# 1. Stop the new code
docker compose -f docker-compose.prod.yml stop \
  backend celery_worker celery_aggregator celery_beat \
  execution-worker execution-engine

# 2. Downgrade migrations all the way back
docker compose -f docker-compose.prod.yml run --rm backend \
  alembic downgrade b2c4e6f8a0d1
# Expected: walks Phase E → D → B/C → A back to the previous head

# 3. Check out the previous code
git checkout main   # or whatever the previous HEAD was

# 4. Rebuild old images
cd infrastructure
docker compose -f docker-compose.prod.yml build \
  backend celery_worker celery_aggregator celery_beat \
  execution-worker execution-engine

# 5. Start it back up
docker compose -f docker-compose.prod.yml up -d
```

**Rollback caveats — read these.**

- **Data created between deploy and rollback IS LOST.** API keys minted,
  refresh tokens issued, proposals submitted, personas created — those
  tables are dropped on downgrade. There's no automated path to preserve
  them. If you rolled back quickly this is usually a non-issue.
- **Workers may already have started running with new code.** Wait until
  `XLEN jobs:pending` is 0 and no jobs are in `XINFO GROUPS jobs:pending`
  pending state before considering the rollback complete.
- **If the migration partially applied** (e.g., one of the four migrations
  failed in the middle), `alembic current` will tell you where you are.
  Downgrade from there. Don't try to "skip" — let alembic walk the chain.
- **If a downgrade fails** (which happens if the upgrade left orphan data
  the downgrade can't handle), you may need to restore from the Postgres
  backup from §1.2. That's the worst-case path, hence the backup is
  non-negotiable.

## 1.8 Behavioral changes that affect existing users

Tell your users / docs:

| Change | What's different |
|---|---|
| `DELETE /api/cases/{id}` and `DELETE /api/suites/{id}` previously returned `500` on any case/suite that had associated runs or referenced FKs (visual baselines, heal proposals, etc.) | **Now succeeds with a clean cascade.** Also `delete_suite` returns `409 Conflict` if any `TestSchedule` references the suite or any sub-suite. If your operators were leaning on the `500` as accidental safety, deletes will now go through. |
| Schedule-fired runs were silently tagged `triggered_by='human'` | **Going forward, tagged `'schedule'`.** Historical rows stay mis-tagged unless you backfill (see §3.6). |
| Workspaces gain `ai_generation_limit_daily` (default 100) | Only matters if anyone uses `POST /api/cases/generate`. Bump to `0` per-workspace if you want unlimited generation. |
| Login response now includes a `refresh_token` field | Backward-compatible — existing frontend ignores the new field. Wire it up separately to eliminate 30-minute logouts. |

---

# Part 2 — Steady-state feature updates

After the first-time deploy above, every subsequent feature commit follows
one of four patterns. Pick the one that matches what's in the change.

## 2.1 Decision tree

```
Does the commit add an alembic migration in backend/app/alembic/versions/ ?
├── YES → §2.3 Standard deploy WITH migration
└── NO  → Does the commit change backend Python or requirements.txt ?
         ├── YES → §2.2 Standard rolling deploy (no migration)
         └── NO  → Does it change execution-engine TypeScript or its package.json ?
                  ├── YES → §2.4 Worker-only restart
                  └── NO  → §2.5 Frontend-only update (rebuild Vite, no backend touch)
```

Hot-fix path (urgent, single-line fix) → §2.6.

## 2.2 Standard rolling deploy (no migration)

```bash
cd /path/to/TraceIQ
git pull
cd infrastructure

# Rebuild only what changed
docker compose -f docker-compose.prod.yml build \
  backend celery_worker celery_aggregator celery_beat

# Rolling restart — one service at a time if you have replicas
docker compose -f docker-compose.prod.yml up -d --no-deps \
  backend celery_worker celery_aggregator celery_beat

# Health check
curl -sf https://your-traceiq.example.com/health
```

**Pre-flight is light**: confirm `git log --oneline -1` shows the expected
commit, confirm backup retention is recent enough (last 24h), confirm the
job queue isn't backed up.

**Rollback**: `git checkout <previous-sha>`, rebuild, restart.

## 2.3 Standard deploy WITH a migration

Same as Part 1, abbreviated:

```bash
# 1. Pre-flight: pg_dump + note alembic current
docker exec <postgres> pg_dump -U <user> -d quality_intelligence \
  --format=custom --file=/var/backups/traceiq/pre-$(git log -1 --format=%h)-$(date +%Y%m%d-%H%M).pgdump
docker compose run --rm backend alembic current

# 2. Build new images
cd infrastructure
docker compose -f docker-compose.prod.yml build \
  backend celery_worker celery_aggregator celery_beat

# 3. Stop celery beat
docker compose -f docker-compose.prod.yml stop celery_beat

# 4. Apply migration
docker compose -f docker-compose.prod.yml run --rm backend alembic upgrade head
docker compose -f docker-compose.prod.yml run --rm backend alembic current  # confirm

# 5. Restart Python services
docker compose -f docker-compose.prod.yml up -d \
  backend celery_worker celery_aggregator celery_beat

# 6. (If the commit changed execution-engine too)
docker compose -f docker-compose.prod.yml build execution-worker execution-engine
docker compose -f docker-compose.prod.yml up -d execution-worker execution-engine
```

**Rollback**: see §1.7. Same shape: `alembic downgrade <previous-revision>`,
git checkout previous, rebuild, restart.

## 2.4 Worker-only restart (execution-engine / execution-worker changes)

Use when the commit only touches `execution-engine/src/**` or its
`package.json`.

```bash
cd /path/to/TraceIQ
git pull
cd infrastructure

# Rebuild only the worker images
docker compose -f docker-compose.prod.yml build execution-worker execution-engine

# Workers are stateless from the backend's view — restart freely
docker compose -f docker-compose.prod.yml up -d execution-worker execution-engine
```

In-flight jobs that were claimed before restart: the worker's Playwright
context dies when the container stops. The job's TestRun stays in
`RUNNING` state until either (a) the stale-run cleanup task force-completes
it, or (b) the worker reclaims and retries it. Brief restarts are usually
fine; minutes-long ones mean some runs need manual `force-complete`.

## 2.5 Frontend-only update

Use when the commit only touches `frontend/src/**` or its `package.json`.

```bash
cd /path/to/TraceIQ
git pull
cd infrastructure

# Build the frontend bundle (Vite produces static assets)
docker compose -f docker-compose.prod.yml build frontend

# Hot-swap (nginx-served static SPA — recreate the container)
docker compose -f docker-compose.prod.yml up -d frontend
```

No DB, no worker, no backend touch. Users may see a brief 502 from the
proxy during the container swap (<5s); a hard refresh on their browser
picks up the new asset hashes.

**Rollback**: `git checkout <previous-sha>`, rebuild, restart frontend.
Browser caches will catch up on next reload.

## 2.6 Hot-fix workflow (urgent, low-blast-radius)

For a one-line fix to a running prod (e.g., a query bug, a wrong constant).

```bash
# 1. Branch from the deployed sha
DEPLOYED=$(docker inspect <backend> --format '{{ .Config.Labels.git_sha }}')  # or look at your CI
cd /path/to/TraceIQ
git fetch origin
git checkout -b hotfix/<short-desc> $DEPLOYED

# 2. Make the minimal fix. Commit. Push.
git commit -am "hotfix: <description>"
git push origin hotfix/<short-desc>

# 3. Deploy as §2.2 (no migration) or §2.3 (with migration)
git pull
cd infrastructure
docker compose -f docker-compose.prod.yml build backend
docker compose -f docker-compose.prod.yml up -d backend

# 4. After verification, merge hotfix → main + feature branches separately
```

Hot-fixes still need backups (lighter — usually the daily snapshot is enough)
and a sanity test before rolling out.

---

# Part 3 — Reference

## 3.1 Service architecture map

| Service | Image source | Replicas | State |
|---|---|---|---|
| `backend` | `./backend` Dockerfile | 1+ | stateless |
| `celery_worker` | `./backend` (shared image) | 1+ | stateless; consumes Celery main-queue |
| `celery_aggregator` | `./backend` (shared image) | 1 | stateless; consumes aggregator-queue |
| `celery_beat` | `./backend` (shared image) | **exactly 1** | reads cron, dispatches scheduled tasks |
| `execution-engine` | `./execution-engine` Dockerfile | 1 | legacy continuous-mode HTTP API |
| `execution-worker` | `./execution-engine` (shared image) | 4 default | stateful only in Playwright per-job context |
| `frontend` | `./frontend` Dockerfile | 1+ | nginx + static SPA |
| `postgres` | `postgres:15-alpine` | 1 | system of record |
| `redis` | `redis:7-alpine` | 1 | broker + job queue + progress hashes |
| `minio` | `minio/minio` | 1+ | artifact store |

## 3.2 Migration history (newest first)

| Revision | Phase | What |
|---|---|---|
| `f8b3c4d5e6f7` | Phase E | `created_by_agent_id` + `agent_session_id` on `testsuite`, `testcase`, `caseproposal` |
| `e7a1b2c3d4e5` | Phase D | `caseproposal` table + `caseproposalaction` enum; agent-ownership columns on `testcase`; `workspace.ai_generation_limit_daily` |
| `d6f9a3b4c5d6` | Phase B/C | `persona`, `selectorhealproposal`, `flakerecord`; comparison columns on `testrun`; retry/flake columns on `testcaseresult` |
| `c5d8f1a2b3c4` | Phase A | `apikey`, `refreshtoken`, `workspacewebhook`, `visualbaseline`; git-context columns on `testrun`; `runtrigger` enum |
| `b2c4e6f8a0d1` | (legacy) | backfill role_id from string fields |
| `a7b3c9d2e1f4` | (legacy) | performance indexes — note: the original was buggy referencing `"user"` instead of `users`; Phase D commit `100ba85` fixed it |
| `1f266105057e` | (legacy) | baseline schema with schedules |

## 3.3 Required env vars (summary)

Already covered in §1.3. Two reminders:
- `SECRET_KEY` and `WEBHOOK_SECRET` are MUST-HAVES (existing requirement; don't reuse demo values in prod).
- `BACKEND_CORS_ORIGINS` must be set in prod to your actual frontend origin (default of `["*"]` is for dev).

## 3.4 Health-check script (drop into operator's repo)

```bash
#!/usr/bin/env bash
# /usr/local/bin/traceiq-healthcheck
set -e
HOST=${1:-https://your-traceiq.example.com}

echo "[1/5] /health …"
curl -sf "$HOST/health" >/dev/null && echo "  OK"

echo "[2/5] alembic head matches …"
ACTUAL=$(docker exec <backend> alembic current | tail -1 | awk '{print $1}')
EXPECTED="f8b3c4d5e6f7"  # update this on each deploy that includes a migration
[ "$ACTUAL" = "$EXPECTED" ] && echo "  OK ($ACTUAL)" || { echo "  FAIL: at $ACTUAL"; exit 1; }

echo "[3/5] step-types catalog reachable …"
COUNT=$(curl -sf "$HOST/api/step-types" | jq -r '.total')
[ "$COUNT" -gt 20 ] && echo "  OK ($COUNT step types)" || { echo "  FAIL: got $COUNT"; exit 1; }

echo "[4/5] Redis jobs queue depth …"
DEPTH=$(docker exec <redis> redis-cli XLEN jobs:pending)
[ "$DEPTH" -lt 100 ] && echo "  OK ($DEPTH pending)" || echo "  WARN: backlog $DEPTH"

echo "[5/5] Recent test runs …"
RECENT=$(docker exec <postgres> psql -U <user> -d quality_intelligence -tA -c \
  "SELECT COUNT(*) FROM testrun WHERE created_at > NOW() - INTERVAL '1 hour'")
echo "  $RECENT runs in last hour"
```

## 3.5 Concurrent index creation (for large `testrun` tables)

If the Phase A migration's index creation on `testrun` would lock for too
long, do this off-hours instead:

```bash
# 1. Skip the index-creating part of the migration by faking that revision applied:
#    a. Run alembic upgrade head EXCEPT for c5d8f1a2b3c4 (manually skip).
#       Easier: temporarily comment out the create_index lines in
#       c5d8f1a2b3c4_ai_agent_integration.py, run alembic upgrade head,
#       then revert the file.
# 2. Create the indexes manually with CONCURRENTLY:
docker exec <postgres> psql -U <user> -d quality_intelligence -c \
  "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_testrun_git_commit ON testrun(git_commit);"
docker exec <postgres> psql -U <user> -d quality_intelligence -c \
  "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_testrun_triggered_by ON testrun(triggered_by);"
docker exec <postgres> psql -U <user> -d quality_intelligence -c \
  "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_testrun_api_key_id ON testrun(api_key_id);"
```

Postgres `CREATE INDEX CONCURRENTLY` doesn't lock writes; it does take
longer (sometimes multiples of the table-locked version).

## 3.6 Backfill historical schedule-tagged runs (optional)

Schedule-fired runs created before the deploy stay tagged `triggered_by='human'`.
To backfill them — **only if you know which runs came from schedules**:

```sql
-- Conservative backfill: only mark runs that ran cases CURRENTLY referenced
-- by a schedule, by the schedule's creator, after the schedule was created.
UPDATE testrun r
SET triggered_by = 'schedule',
    agent_id = 'schedule:' || s.id
FROM testschedule s
WHERE r.user_id = s.created_by_id
  AND r.test_case_id = s.test_case_id
  AND r.created_at >= s.created_at
  AND r.triggered_by = 'human'
  AND r.agent_id IS NULL;
```

This is heuristic — a human running the same case as a schedule would also
get rewritten. Inspect a few rows first:

```sql
SELECT r.id, r.created_at, r.user_id, s.id AS schedule_id, s.cron_expression
FROM testrun r
JOIN testschedule s ON s.test_case_id = r.test_case_id
WHERE r.created_at >= s.created_at AND r.triggered_by = 'human'
LIMIT 10;
```

## 3.7 Common failure modes + fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError: slowapi` (or `anthropic`) at backend startup | Image was built before deps were added to `requirements.txt` | Rebuild backend image: `docker compose build backend …` |
| `socket.gaierror: Name or service not known` at backend startup | Postgres password contains `@` and breaks `DATABASE_URL` parsing | URL-encode the `@` as `%40` in `.env`, OR change the password (note: Postgres ignores `POSTGRES_PASSWORD` after first init — use `ALTER USER` if the role's password is what needs changing) |
| `InvalidPasswordError: password authentication failed for user` | `.env` password doesn't match what's actually stored on the Postgres role | Connect with `psql` using known credentials, run `ALTER USER ...` to set a new password, update `.env`, restart backend |
| `DELETE /api/cases/...` returns 500 | Either an unrelated FK violation OR Phase E hasn't been deployed | `docker exec <backend> alembic current` — if not `f8b3c4d5e6f7`, redeploy |
| `relation "user" does not exist` during migration | The pre-existing `a7b3c9d2e1f4` migration had a typo (referenced `"user"` table; actual is `users`). Phase D `100ba85` fixed it. | Pull `feature/ai-agent-integration` or later; the fix is included |
| `caseproposalaction enum already exists` during Phase D migration | A previous failed migration attempt left the type stranded | The Phase D migration is idempotent on type creation (`DO $$ IF NOT EXISTS ...`). If you still hit this, manually `DROP TYPE caseproposalaction` and retry |

## 3.8 Backup / restore commands

```bash
# Backup
docker exec <postgres> pg_dump -U <user> -d quality_intelligence \
  --format=custom --file=/tmp/backup.pgdump
docker cp <postgres>:/tmp/backup.pgdump /var/backups/traceiq/

# Restore to a fresh DB
docker exec <postgres> createdb -U <user> quality_intelligence_restore
docker exec -i <postgres> pg_restore -U <user> -d quality_intelligence_restore < /var/backups/traceiq/backup.pgdump

# Verify the restore — count critical tables
docker exec <postgres> psql -U <user> -d quality_intelligence_restore -c \
  "SELECT 'testrun' AS t, COUNT(*) FROM testrun
   UNION ALL SELECT 'testcase', COUNT(*) FROM testcase
   UNION ALL SELECT 'testsuite', COUNT(*) FROM testsuite;"
```

---

# Part 4 — Post-deploy follow-ups

These are NOT blockers — schedule them once the deploy is stable.

## 4.1 Within 1 week

| Item | Why |
|---|---|
| **Index hygiene on `testrun`** if the table is large (>10M rows) | Phase A indexes were created with table locks; re-create CONCURRENTLY off-hours per §3.5 if peak hours saw slow queries |
| **Wire frontend auto-refresh** to consume the new refresh token | Eliminates the 30-min logout; ~½ day of frontend work |
| **Set `BACKEND_CORS_ORIGINS`** to your actual origin if you haven't already | `["*"]` is a known issue called out in `info/CODEBASE.md` |
| **Bump or zero out `Workspace.ai_generation_limit_daily`** if you intend to allow unlimited AI gen | `UPDATE workspace SET ai_generation_limit_daily = 0;` for unlimited |
| **Monitor `caseproposal` queue length** | If proposals are piling up unaccepted, set up a Slack/email reminder for reviewers |

## 4.2 Within 1 month

| Item | Why |
|---|---|
| **Build the frontend UI for the new entities** — API keys, webhooks, personas, proposals, baselines, deployment-comparison runs | All have working backend endpoints; no React pages yet |
| **Decide on `PROACTIVE_HEAL_ENABLED` and `TAUTOLOGY_DETECTOR_ENABLED`** | Both default `false`. Turn on after observing baseline behavior. Proactive heal requires per-step DOM capture in the worker — confirm that's in place |
| **Add CONCURRENTLY-mode default to future migrations** | Update the Phase X+1 migration template to use `op.create_index_concurrently()` for any new indexes on hot tables (`testrun`, `testcaseresult`) |
| **Backfill historical schedule-tagged runs** per §3.6 if you care about analytics |
| **Publish `traceiq-mcp` to PyPI** and document the install for external agents | Currently it's installable as `pip install -e .` from the repo only |
| **Build the GitHub Action's `dist/` bundle** and publish it under your org | The repo ships sources; GitHub Action conventions need the compiled JS in `dist/index.js` |

## 4.3 Optional, longer-horizon

- Phase F items (parked in `SCOPE_NOTES.md`): Mode-2 discovery tools for URL-only agents; server-side codebase analysis; auto-approval policy for self-created proposals; CI parity check between the step-type catalog and the runner.
- Migration from path-prefix to coverage-based impact analysis (instrument workers with istanbul + coverage.py, store per-file coverage on `TestCaseResult`).
- OpenTelemetry tracing — currently no first-class observability layer.

---

## Document maintenance

This doc is checked into the repo at `/PRODUCTION_DEPLOY.md`. When you add
a new migration or change the deploy sequence:

1. Update §3.2 (migration history) with the new revision.
2. Update §3.4 (health-check `EXPECTED` value).
3. If the change requires a non-standard deploy step, add it under Part 2
   or as a new sub-section of Part 1.
4. Commit with the rest of the feature.

The point is: the operator's next deploy reads from a doc that's current
with the code. Stale runbooks have caused more outages than buggy code.
