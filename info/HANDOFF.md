# Handoff — resuming the regulated-readiness work

Written 2026-08-07, rewritten 2026-08-10, updated 2026-09-01. Branch
`feature/enterprise-auth-ai`.

This file is deliberately self-contained: it lives in git, so it travels to any
machine. Assistant session memory does **not** — it sits in
`~/.claude/projects/…` on one laptop only. If something matters for resuming,
it belongs here rather than in a chat history.

---

## Where we are

`info/REGULATED_READINESS.md` is the plan: nine workstreams (A-I) to make
TraceIQ sellable into insurance, payments, and enterprise SaaS procurement.

**Everything below is DONE and PUSHED.** Branch head `7af0aac`, tree clean,
remote in sync — there is no unpushed work.

Last session (2026-09-01) did two things: deployed the accumulated work to the
local stack, and closed the one gap that deploy exposed — the per-project data
policy had no API and no UI, so it was enforceable but not configurable. Both
now exist (`7af0aac`). See "The local deployment" below, which is new.

| Workstream | State |
|---|---|
| C - credential leaks | done (except C5, below) |
| A - redaction (A1-A8) | done |
| B - capture policy | done |
| D - encryption at rest, TLS, key rotation | done |
| E - audit trail (E1-E5) | done |
| F1 - federated provisioning (the OIDC tenant bug) | done |
| F2 - SCIM 2.0 + real deprovisioning | done |
| F4 - separation of duties | done |
| F5 - roles cleanup | done |
| **F3 - SAML 2.0** | **deferred - see below** |
| G - deletion, retention, erasure, residency | done |
| H1-H4 - beat HA, DLQ replay, migration lock, monitoring | done |
| **H5 - Helm chart** | **not started** |
| I1-I4 - CI database, isolation tests, coverage gates | done |
| **I5 - pen test, SOC 2 Type II** | **external / calendar** |

Tests: **624** - 424 backend unit, 114 backend integration (real Postgres), 86
engine. CI ran 18 before any of this work, and had no database at all until I1.

### What is actually left, and why

1. **C5 credential hygiene.** Committed `.env` history and the old tracked
   `dump.sql` mean "have credentials been exposed in version control" is still
   honestly *yes*. Needs rotation, then a `git filter-repo` rewrite. **Rotation
   does not depend on the rewrite and should not wait for it.** Every regulated
   buyer's questionnaire asks this. This is the highest-value remaining item and
   it is not a coding task.
2. **F3 SAML 2.0.** Zero code. Gates SAML-first insurance and banking IdPs
   regardless of OIDC support. Deferred here because it needs `xmlsec` system
   libraries in the backend image (like ldap3 before it, but heavier) and an IdP
   to test against - a mock SAML IdP is doable, the mock OIDC one used for F1 is
   ~30 lines of FastAPI. Parked in `SCOPE_NOTES.md`.
3. **H3's squashed initial migration.** The advisory lock landed; the empty
   Alembic baseline did not. There is still no verified rollback to an arbitrary
   revision - `docs/OPERATIONS.md` prescribes snapshot-then-upgrade meanwhile.
4. **H4's remainder:** no OpenTelemetry, no structured logging (stdlib `logging`
   still mixed with raw `print()`), no error tracking.
5. **H5 Helm/K8s.** Compose only.
6. **I5 pen test then SOC 2 Type II.** External, calendar-bound. Everything it
   needs from the codebase now exists.

Closed on 2026-09-01: `Project.data_policy` had no API and no UI, so the
capture policy was enforceable but only settable by direct SQL. Now
`GET`/`PUT /api/projects/{id}/data-policy` (viewer read, admin write, partial,
audited) plus a "Data capture & redaction" panel on the Quality Dashboard,
below the Gate policy and CI cards. The read model returns the *effective*
policy next to the stored one with a `clamped` flag, because MAX_CAPTURE_LEVEL
can hold a project below its request and a screen that hid that would be lying.

New operator-facing docs worth knowing about: `docs/OPERATIONS.md` (H) and
`docs/DATA_RESIDENCY.md` (G). `docs/ENTERPRISE_AUTH.md` grew federated
provisioning and SCIM sections.

Architecture decisions and traps are in `CLAUDE.md`. Read those before touching
any of it - several are counter-intuitive and expensive to rediscover.

## Setting up a fresh laptop

Everything needed is in the repo. Two things are not obvious:

### 1. Backend tests need Python 3.11, which your host probably isn't

`psycopg2-binary` ships no wheel for 3.12+, and building it needs `pg_config`.
Rather than fight that, `backend/run-tests.sh` borrows the backend image (which
already has every runtime dep at the right version) and mounts the tree over it.

```bash
cd backend
./run-tests.sh                              # whole unit suite — what CI runs
./run-tests.sh tests/test_redaction.py -q   # one file
```

It builds its helper image on first use, which pulls
`ghcr.io/raja-9679/traceiq-backend:latest`. If that pull is refused, either
`docker login ghcr.io` or point it at a locally-built backend image:

```bash
TRACEIQ_TEST_IMAGE=my-local-backend:tag ./run-tests.sh
```

Plain `pytest` also works if your venv happens to be 3.11.

### 2. The execution engine has tests now, and they are not jest

`node:test` via `ts-node`, no new dependencies. `*.test.ts` is excluded from
`tsconfig.json` so test files never ship in the worker image — check that stays
true if you touch the build.

```bash
cd execution-engine
npm ci
npm test
npm run build     # must stay clean; dist/ must contain no *.test.js
```

### Verifying against a real database

There is now a script for this: **`backend/run-tests-live.sh`**. It creates a
scratch database inside the running Postgres container, bootstraps the schema,
runs pytest with `TRACEIQ_LIVE_DB=1`, and drops the database again. Modules
under `tests/integration/` skip themselves without that variable, so they stay
out of the unit suite while the CI `integration-tests` job (which has Postgres,
Redis and MinIO services) sets it and runs them.

```bash
cd backend
./run-tests-live.sh                                              # all of tests/integration
./run-tests-live.sh tests/integration/test_federated_provisioning_db.py -q
KEEP_DB=1 ./run-tests-live.sh ...     # leave the scratch DB behind for psql
```

One trap if you add fixtures there: `pytest.ini` sets
`asyncio_default_fixture_loop_scope = session`, and the installed pytest-asyncio
(0.25) ignores `asyncio_default_test_loop_scope`, so tests are function-scoped.
An async fixture must declare `@pytest_asyncio.fixture(loop_scope="function")`
or its engine ends up on a different event loop than the test.

The underlying manual pattern, for the things a pytest module can't express
(the `sslmode` translation, the append-only trigger, migration round-trips):

```bash
PGC=traceiq-postgres-1
PW=$(docker exec $PGC printenv POSTGRES_PASSWORD)
NET=$(docker inspect $PGC -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')

docker exec $PGC psql -U traceiq -d postgres -c "CREATE DATABASE traceiq_scratch;"

docker run --rm --entrypoint python --network "$NET" \
  -v "$PWD":/src -w /src -e PYTHONPATH=/src \
  -e DATABASE_URL="postgresql+asyncpg://traceiq:$PW@$PGC:5432/traceiq_scratch" \
  -e CELERY_BROKER_URL=redis://localhost:6379/0 \
  -e CELERY_RESULT_BACKEND=redis://localhost:6379/0 \
  -e MINIO_ENDPOINT=localhost:9000 -e MINIO_ACCESS_KEY=k -e MINIO_SECRET_KEY=s \
  -e SECRET_KEY=test-only-secret-value-not-real \
  traceiq-backend-test:local scripts/bootstrap_db.py

# ... run checks ...
docker exec $PGC psql -U traceiq -d postgres -c "DROP DATABASE traceiq_scratch;"
```

`--entrypoint python` is required: the image's entrypoint waits for a database
and gives up. Never point this at the `traceiq` database itself.

---

## Traps worth remembering

**Anything a migration NAMES that model metadata also creates will diverge.**
`bootstrap_db.py` builds fresh schemas from metadata, so an explicitly named
foreign key in a migration exists only on *migrated* databases - and F4's
`downgrade` failed on every fresh install with "constraint does not exist".
Pass `None` and let SQLAlchemy generate the same default name on both paths.
This is the same family as the audit-trigger trap in `c8d9e0f1a2b3`, and it will
happen again.

**pytest-asyncio 0.25 ignores `asyncio_default_test_loop_scope`.** `pytest.ini`
sets fixture loop scope to `session` and tests are function-scoped, so an async
fixture must declare `@pytest_asyncio.fixture(loop_scope="function")` or its
engine lands on a different event loop ("attached to a different loop"). Also:
`session.rollback()` expires every instance, so capture ids as plain ints before
one - `tests/integration/test_scim_db.py` has the `Ws` NamedTuple pattern for it.

**`frontend/dist` is root-owned, so `npm run build` fails on the host** with
`EACCES ... /frontend/dist/assets`. A past container build wrote it as root.
The image build is unaffected (it compiles inside the container), so this only
bites a host build. `sudo rm -rf frontend/dist` clears it.

**`cleanup_stuck_tests` has been broken for a long time and is only noise.**
It references `TestRun.updated_at` three times and `TestRun` has no such column,
so it throws every 5 minutes into a broad `except` and logs
`[Cleanup] Error cleaning up stuck tests: updated_at`. Stuck-run detection is
NOT affected — the working reaper is `check_stale_runs` in
`app/tasks/result_aggregator.py`, which uses `last_result_at` and is scheduled
alongside it. So there are two reapers and the legacy one is dead code. Either
point it at `last_result_at` or delete it as superseded; it was left alone
because deleting a scheduled task is a behaviour decision, not a cleanup.

## The local deployment

**The running stack is NOT driven from `infrastructure/`.** It lives in a
separate, hand-managed directory:

    /home/raja/traceiq-test/
        docker-compose.community.yml    <- a COPY of infrastructure/'s
        .env                            <- real secrets, mode 600, gitignored
        env.community.example

Because that compose file is a copy, it drifts. On 2026-09-01 it was six weeks
stale: the images had every line of the redaction/encryption/audit code, but the
compose predated the settings that code reads, so `MAX_CAPTURE_LEVEL`,
`SECRETS_KEY`, `MINIO_USE_SSL`, `REQUIRE_TRANSPORT_SECURITY` and `METRICS_TOKEN`
were all unset in the containers. **Check for drift before concluding a feature
is broken:**

```bash
diff /home/raja/traceiq-test/docker-compose.community.yml \
     infrastructure/docker-compose.community.yml
```

If you would rather remove the copy step entirely, run the community stack
directly out of `infrastructure/` with `--env-file` pointing at a gitignored
`.env` there. That was not done because it moves a working deployment.

### Rebuilding and rolling it

The community compose has no `build:` contexts (it pulls), so images are built
by hand and tagged `:dev` — `TRACEIQ_VERSION=dev` in that `.env` is what selects
them.

```bash
cd /home/raja/Work/repos/TraceIQ
docker build -t ghcr.io/raja-9679/traceiq-backend:dev          -f backend/Dockerfile backend
docker build -t ghcr.io/raja-9679/traceiq-frontend:dev         -f frontend/Dockerfile frontend
docker build -t ghcr.io/raja-9679/traceiq-execution-worker:dev -f execution-engine/Dockerfile.worker execution-engine

cd /home/raja/traceiq-test
docker compose -f docker-compose.community.yml --env-file .env up -d
```

**The worker MUST be built from `Dockerfile.worker`, not `Dockerfile`.** This
cost time on 2026-09-01. Two Dockerfiles sit side by side in
`execution-engine/`:

| File | CMD | What it is |
|---|---|---|
| `Dockerfile` | `npm start` -> `dist/server.js` | the LEGACY continuous engine |
| `Dockerfile.worker` | `node dist/worker.js` | the distributed worker |

`.github/workflows/release-images.yml` is authoritative and uses
`Dockerfile.worker`. Building the wrong one yields a container that looks
healthy, consumes nothing from `jobs:pending`, and spams
`Redis connection error: NOAUTH Authentication required` — because `server.ts`
imports `runner.ts`, whose Redis client is built from `REDIS_HOST`/`REDIS_PORT`
with **no password**, and the compose only supplies `REDIS_URL`. Verify after
building:

```bash
docker image inspect ghcr.io/raja-9679/traceiq-execution-worker:dev \
  --format '{{join .Config.Cmd " "}}'      # must be: node dist/worker.js
```

### Ports and quick health checks

Backend `18000`, frontend `8080` (both from that `.env`).

```bash
curl -s localhost:18000/health/ready                      # {"ready": true, ...}
curl -s -o /dev/null -w '%{http_code}\n' localhost:8080/api/step-types   # 200 via nginx proxy
```

`/api/health` returns 404 and that is correct — the backend route is `/health`,
not under `/api`.

`/metrics` needs the bearer token from `METRICS_TOKEN` in that `.env` (the same
value is in `infrastructure/monitoring/metrics_token` for the optional
Prometheus overlay). Anonymous is 401 by design.

### Things deliberately NOT enabled locally

`SECRETS_KEY` is **empty** on purpose. Setting it is safe to read — existing
ciphertext still decrypts via the legacy path — but once
`scripts/rotate_secrets.py` re-encrypts under it, removing it later makes those
secrets unreadable. That is a one-way door and should be a deliberate act, not a
deploy side effect. The `.env` block documents the three-step adoption.

`REQUIRE_TRANSPORT_SECURITY=false` because nothing in the local stack terminates
TLS; `true` would correctly refuse to boot. The five startup `[config]`
advisories about plaintext Postgres/Redis/MinIO and missing SSE are that check
working as designed, not errors.

## Loose ends and things to know

**The local stack is current** as of 2026-09-01 — see "The local deployment"
below for where it lives and how to rebuild it. It was ~6 weeks behind on
configuration before that (the code was current; the compose file was not).

```bash
cd infrastructure
docker compose -f docker-compose.yml build backend execution-worker
docker compose -f docker-compose.yml up -d backend execution-worker
```

**`infrastructure/.env` on this laptop is from July** and lacks the newer keys.
It is gitignored, so a new laptop needs its own — `./traceiq-setup.sh` generates
one, and `env.community.example` documents every new setting (capture level,
`SECRETS_KEY`, MinIO TLS/SSE, `REQUIRE_TRANSPORT_SECURITY`, `METRICS_TOKEN`).
Never commit it.

**Credential hygiene is still outstanding (workstream C5).** Committed `.env`
history and a tracked `dump.sql` mean the honest answer to "have credentials
been exposed in version control" is still yes. `dump.sql` was actually
untracked on 2026-08-11 — commit `4983c51` claimed to do it but only added the
`.gitignore` line, which does nothing for a file already in the index, so the
7.6 MB dump stayed in `HEAD` and on `main`. Untracking does not remove it from
history either. This needs a `git filter-repo` rewrite, and the affected
credentials need rotating first.
Rotation does not depend on the rewrite and should not wait for it. Every
regulated buyer's security questionnaire asks this, so it will surface.

**Migrations at head:** `c8d9e0f1a2b3` (audit chain), preceded by
`b7c8d9e0f1a2` (`Project.data_policy`). Both verified against a real Postgres
for bootstrap-from-empty, upgrade, downgrade and re-upgrade.

**Behaviour change worth remembering:** the default capture level is now
`standard`, so pre-existing projects stopped recording video, traces and HAR
until someone opts them up to `full`. That was deliberate — backfilling every
row to `full` to preserve the old behaviour was considered and rejected.

### The trap most likely to bite you

**`scripts/bootstrap_db.py` never runs migrations.** It calls
`SQLModel.metadata.create_all()` and stamps head, because the Alembic baseline
is an empty stub. So **any DDL that exists only in a migration — triggers,
functions, grants, RLS policies — is absent on fresh installs.** The audit
trigger had exactly this bug; it is fixed by also attaching the DDL to the
table's `after_create` event in `app/services/audit.py`. Check this for any new
non-model DDL.

Two smaller ones found alongside it: SQLAlchemy's `DDL()` applies
`%`-interpolation, so literal `%` in plpgsql must be doubled; and asyncpg
rejects multiple statements in one execute, so each DDL statement needs its own
event.

### Workstream H is worth pulling forward if procurement gets real

Not because of compliance, but because two items block a serious deployment:
`celery_beat` is a single point of failure with no leader election that stalls
the whole execution pipeline silently if it dies, and the Alembic baseline being
an empty stub means there is **no trustworthy rollback for a failed upgrade** —
which a change-controlled environment will reject outright.
