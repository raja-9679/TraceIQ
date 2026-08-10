# Handoff — resuming the regulated-readiness work

Written 2026-08-07, end of session. Branch `feature/enterprise-auth-ai`.

This file is deliberately self-contained: it lives in git, so it travels to any
machine. Assistant session memory does **not** — it sits in
`~/.claude/projects/…` on one laptop only. If something matters for resuming,
it belongs here rather than in a chat history.

---

## Where we are

`info/REGULATED_READINESS.md` is the plan: nine workstreams (A–I) to make
TraceIQ sellable into insurance, payments, and enterprise SaaS procurement.

**Done and pushed:** workstreams C, A, B (phases 1–2) and D, E (phase 3).
**Done, not yet pushed:** F1.

| Workstream | State |
|---|---|
| C — credential leaks | done |
| A — redaction (A1–A8) | done |
| B — capture policy | done |
| D — encryption at rest, TLS, key rotation | done |
| E — audit trail (E1–E5) | done |
| F1 — federated provisioning (the OIDC tenant bug) | done |
| **F2 — SCIM 2.0 with real deprovisioning** | **next** |
| F3–F5 — SAML, separation of duties, roles | not started |
| G — deletion / residency | not started |
| H — operability (beat HA, DLQ replay, real migration baseline) | not started |
| I — proving it (CI coverage, pen test, SOC 2) | partial |

Tests: **433** — 326 backend unit, 21 backend live-Postgres, 86 engine. CI runs
the 326 (it has no Postgres service, so the live ones are opt-in — see below).

The nine commits for phases 1–3 are `5ad603a` … `1d90480`, plus `4e038b9`.
Everything below `4e038b9` in the push is earlier security-hardening work from
2026-08-06.

Architecture decisions and traps are recorded in `CLAUDE.md` under
"Data-capture policy + redaction", "Encryption + transport", and "Audit trail".
Read those before touching any of it — several are counter-intuitive and
expensive to rediscover.

---

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
out of the unit suite and out of CI (which has no Postgres service — I1).

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

## Next up: workstream F (identity)

Read `info/REGULATED_READINESS.md` § "Workstream F" for the full detail.

**F1 is done** — `app/services/federation.py` plus
`provision_federated_user()` / `sync_federated_access()` in
`user_provisioning.py`. Operators pick a mode (`standalone` / `workspace` /
`deny`) and optional IdP group → role/team maps in Settings → Instance (Admin) →
Federated provisioning. Two things to know before touching it: the default is
still `standalone` (the legacy tenant-per-user behaviour) so existing installs
upgrade unchanged, and group maps are re-applied on **every** login — the whole
point, since create-only mapping never revokes anything. `docs/ENTERPRISE_AUTH.md`
has the operator-facing version. Verified end to end against a mock OIDC
provider; a throwaway mock-IdP recipe is not kept in the repo, but it is ~30
lines of FastAPI serving discovery/token/userinfo, and the userinfo `groups`
claim is the only interesting part.

**F2. SCIM 2.0 with real deprovisioning.** There is no deprovision path of any
kind today, so someone removed from Okta or Entra keeps their TraceIQ account
and refresh token indefinitely. This is the item most likely to fail an IT
security review on its own. Good news: `is_active` is already enforced in both
the JWT and API-key principal paths, so a SCIM `active:false` bites immediately.

**F3. SAML 2.0.** Zero code today. A large share of insurance and banking IdPs
are SAML-first, so this gates those deals regardless of OIDC support.

**F4. Separation of duties.** `app/api/agent_ownership.py` lets an editor accept
their own proposal — creating and accepting both require the same role and
nothing checks `decided_by_id != created_by_id`. `maybe_auto_apply` also applies
changes with no human at all above the workspace threshold.

**F5. Roles.** `Role.tenant_id` exists but nothing ever creates a tenant-scoped
role — no API, no UI, dead column. Also reconcile or delete
`backend/scripts/setup_rbac.py`, which seeds an `org:`-scoped permission
vocabulary incompatible with the `workspace:`/`test:` scopes the live code
checks; running it produces roles that grant nothing.

---

## Loose ends and things to know

**The stack is running old images.** Your local compose stack uses published
images that predate all of this. A real end-to-end run still exercises the
pre-redaction worker until you rebuild:

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
history and — until commit `4983c51` — a tracked `dump.sql` mean the honest
answer to "have credentials been exposed in version control" is still yes.
Untracking the file does not remove it from history. This needs a
`git filter-repo` rewrite, and the affected credentials need rotating first.
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
