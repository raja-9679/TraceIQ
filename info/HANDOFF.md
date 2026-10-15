# Handoff — resuming the regulated-readiness work

Written 2026-08-07, rewritten 2026-08-10, updated 2026-09-07, status corrected
2026-09-08. Branch `feature/enterprise-auth-ai`.

This file is deliberately self-contained: it lives in git, so it travels to any
machine. Assistant session memory does **not** — it sits in
`~/.claude/projects/…` on one laptop only. If something matters for resuming,
it belongs here rather than in a chat history.

---

## Resume here (2026-10-15 16:10)

The October roadmap (`info/FEATURE_ROADMAP_2026-10.md`: ten roles × run/test
lenses + 24 candidates) was largely BUILT on 2026-10-14/15. The tracker with
every work package, branch, decision and log line is
**`info/ROADMAP_EXECUTION_2026-10.md`** — read it next. The shareable status
page is https://claude.ai/artifact/LCuvPCFBWW8H1i74CeiS6j (private).
How the owner wants work done, local-stack steps and traps for a new
machine: **`info/WORKING_NOTES.md`**.

- **Done, merged, pushed, deployed locally:** WP0 targeted runs + flake
  owners/expiry/attempts + TOTP; WP1 release readiness; WP2 test inbox +
  webhook capture; WP3 per-step evidence + failure investigation v2; WP4
  identity/access matrix + security steps; WP5 run-scoped data + suite hooks;
  WP6 requirements/incidents; WP7 SIEM, alerts, access reviews, SARIF,
  findings workflow, leak detection; WP8 fleet, regions, paging, IP
  allowlists, mTLS, egress allowlist, KEDA, restore drill; WP9 passive estate
  scanning + script inventory; WP10 sweeps, crawler, page budgets, selector
  health, Storybook; WP11 repro bundle, CLI (`integrations/cli`), git sync,
  PR comments, expect_schema. More than twenty security-review findings were
  fixed along the way (commits titled `fix(security): …`).
- **Branches:** `feature/enterprise-auth-ai` → pushed as
  `feature/enterprise-auth-ai-2026-10`; `feature/redesign-a` (design branch,
  base merged in, UI ported). Migration head `caa2b3c4d5eb`. GitHub Actions are
  workflow_dispatch-only. GitHub push protection flagged the fake Stripe test
  keys in the redaction corpora; the owner allowed them on 2026-10-15.
- **On hold by the owner — do not start without being told:** WP12 reporting,
  WP13 compliance/solution, WP14 AI & platform.
- **Parked for an owner decision:** estate active probing (ports, WAF strings,
  origin bypass, TLS cipher enumeration) — `SCOPE_NOTES.md`. A safety check
  stopped the agent writing it; do not re-add it without an explicit decision.
- **Pending on the owner:** acceptance testing of the new pages on the local
  stack; default billing plans; the C5 mirror push / main fast-forward
  (below); production credential rotation; external pen test + SOC 2.
- **Known gaps:** crawler DNS-rebinding on browser page loads and robots.txt
  (worker egress rules are the control); trace parser not wired into
  analysis; INP is approximate; CLI tests not in CI; external services
  (Jira, Linear, Splunk, Elastic, PagerDuty, Opsgenie, IMAP, crt.sh,
  Storybook) are mock-verified only.
- **How the work was done** (repeatable): one worktree + agent per WP from
  `info/AGENT_BRIEF_ROADMAP.md`, unique scratch DBs per agent
  (`TRACEIQ_SCRATCH_DB`), migrations re-chained linearly at merge, every merge
  followed by the full suite set. Merges of the redact twins' test files lose
  a closing `});` easily — always run `tsc`/pytest after resolving.

## Where we are

`info/REGULATED_READINESS.md` is the plan: nine workstreams (A-I) to make
TraceIQ sellable into insurance, payments, and enterprise SaaS procurement.

**Everything below is DONE and COMMITTED. None of it is on GitHub yet** — and
that is not an oversight, it is where C5 left the repository. Branch head is
`163c248` plus this correction; tree clean.

> **Read this before you touch git.** The C5 history rewrite has already been
> applied to *this working clone*: local `main` is `4e24f56`, byte-identical to
> `~/traceiq-history-backup/TraceIQ-rewritten.git`. GitHub still carries the
> old, secret-bearing history (`origin/main` = `8b32d4a`), so the two have no
> shared recent ancestry — `git status` reports the branch as ~204 ahead and
> ~205 behind, which is the rewrite, not lost work. **Do not `git pull`, merge,
> or rebase onto `origin/*`**: that would splice the removed secrets back in.
> The only correct next git operation is the force-push in
> `docs/CREDENTIAL_HYGIENE.md`. Every SHA quoted in docs or notes written
> before 2026-09-07 belongs to the old history and will not resolve here.

> **Deferred, agreed 2026-10-12 — getting this work into `main`.** All work is
> pushed to GitHub as **`feature/enterprise-auth-ai-2026-10`** (rewritten
> history; contains every one of origin's 20 branches plus everything since).
> **Do not merge it into GitHub's `main` with a PR or `git merge`:** a trial
> merge gave 98 conflicted files (the histories share nothing after
> 2025-12-20), and the result would keep the secret-bearing old commits
> reachable from `main`. The planned order, to be done later:
> 1. Owner allows force pushes on `main` (GitHub → Settings → Branches).
> 2. Run the C5 mirror push from `docs/CREDENTIAL_HYGIENE.md` — `main`
>    becomes the rewritten `main`, which is an ancestor of the branch above.
> 3. Re-push `feature/enterprise-auth-ai-2026-10` (`--mirror` deletes refs the
>    bare repo doesn't have), then fast-forward `main` to it — no conflicts.
> 4. Re-enable force-push protection; everyone re-clones; ask GitHub Support
>    to purge cached views of the old commits.

Sessions since the last full rewrite of this file:

- **2026-09-01** — deployed the accumulated work to the local stack and closed
  the gap that deploy exposed: the per-project data policy had no API and no
  UI, so it was enforceable but not configurable. Both now exist. See "The
  local deployment" below.
- **2026-09-07** — F3 (SAML), H3 (squashed migration), H4 remainder (JSON logs,
  request ids, OTel, Sentry), H5 (Helm, installed on kind), and C5 (rotation,
  history rewrite, evidence) plus the I5 pre-assessment docs. A-H are now all
  done; only I5 remains and it is external.
- **2026-10-12** — feature analysis (10 roles × run/test lenses, plus 24 AI /
  security / platform candidates checked against the code) saved as
  `info/FEATURE_ROADMAP_2026-10.md`. Its "fix first" list was fixed and
  verified live: personas now reach the worker (and refresh via a
  `persona_refresh` worker job), proactive heal gets per-step DOM (capture
  `full` only), AI generation no longer fakes a human review, failure analysis
  gets console + network evidence, `is_flaky`/`confidence` are written, visual
  baselines are versioned with audited keep/accept/rollback, heal accept
  refuses assertion steps. **Biggest find: seven Celery tasks had never run on
  any deployment** (no default queue; workers only consume `main-queue` /
  `aggregator-queue`) — notifications, outbound webhooks, account emails,
  failure analysis, heal, persona refresh, tautology scan. Fixed with
  `task_default_queue`; `tests/test_celery_routing.py` guards it. Also closed a
  cross-tenant hole in `POST /visual-baselines` (arbitrary bare keys were
  presigned back, and purge used them as delete prefixes). Three per-entity
  delete 500s found and left open — see the roadmap doc §1. Uncommitted at
  time of writing unless a later commit says otherwise.
- **2026-10-14** — merged into this branch (pushed as
  `feature/enterprise-auth-ai-2026-10`): the three per-entity delete 500s
  (`fix/per-entity-deletes`), `fix/project-switch-order`,
  `fix/editor-expect-title`, plus new fixes — API-step body showed/saved
  `[object Object]` (`params.body` may be an object; editor now converts at
  its edges), workspace delete always 400'd (UI never sent `confirm=<name>`;
  new dry-run-first dialog), billing 500'd on installs with no `plan` rows
  (the squash dropped the legacy plan seed; endpoint now reports "No plan
  (unlimited)" — re-seeding was NOT done because the old free plan caps runs
  at 500/month, a product decision). And the roadmap's TOTP half:
  `totp` step (`execution-engine/src/core/totp.ts`, `{{secret.X}}` seed only,
  waits out a window with <5 s left) — **needs a worker image rebuild**. The
  email-inbox half is parked for onboarding work. Frontend tests (Vitest)
  exist only on `feature/redesign-a`; the frontend test files merged here run
  there. Pushes carry `[skip ci]`; workflows only trigger on `main`/tags/PRs.

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
| F3 - SAML 2.0 | done (2026-09-07) |
| G - deletion, retention, erasure, residency | done |
| H1-H4 - beat HA, DLQ replay, migration lock, monitoring, OTel/JSON logs/Sentry | done |
| H5 - Helm chart | done (2026-09-07, verified on kind) |
| I1-I4 - CI database, isolation tests, coverage gates | done |
| **I5 - pen test, SOC 2 Type II** | **external / calendar** |

Tests: **624** - 424 backend unit, 114 backend integration (real Postgres), 86
engine. CI ran 18 before any of this work, and had no database at all until I1.

### What is actually left, and why

1. **C5 credential hygiene — the force-push, and two rotations that are not
   this laptop's to do.** Full inventory, rotation of everything under local
   control, and a verified `git filter-repo` rewrite are in
   `docs/CREDENTIAL_HYGIENE.md`. Done: the rewrite itself, and this working
   clone is already re-pointed to it — so step 2 of that page's command block
   is already satisfied and must not be re-run (it would reset this branch onto
   the old history). **Not done:**
   - the **force-push** (`git push --mirror origin` from
     `~/traceiq-history-backup/TraceIQ-rewritten.git`, with `main`
     temporarily unprotected). Note that bare repo's branch tip is `c919776`,
     a couple of commits behind this clone, so follow the mirror push with an
     ordinary `git push origin feature/enterprise-auth-ai` from here.
   - asking GitHub Support to purge cached views of the old commits. Until
     that happens the old SHAs stay fetchable: **treat everything ever
     committed as disclosed.**
   - rotating the production `*.thehindu.co.in` credentials and the three
     third-party `appKey` values of the application under test.

   After the push, every *other* clone must re-clone; pull will not work.
2. ~~F3 SAML 2.0~~ — **done 2026-09-07**. `app/services/saml_auth.py`,
   `/api/auth/saml/*`, settings group `saml`, `docs/ENTERPRISE_AUTH.md`. The
   "needs xmlsec system libraries" premise was wrong: `xmlsec` has manylinux
   wheels, so python3-saml installs into the slim image with no apt packages.
   Tested with a self-signed mock IdP (`tests/test_saml_auth.py`, 40 tests);
   NOT yet exercised against a real Entra/Okta tenant — the first customer
   pilot should budget an afternoon for attribute-name surprises.
3. ~~H3's squashed initial migration~~ — **done 2026-09-07**, see "Migrations"
   below.
4. ~~H4's remainder~~ — **done 2026-09-07**: `app/core/telemetry.py`
   (`LOG_FORMAT=json`, `X-Request-ID`, `OTEL_EXPORTER_OTLP_ENDPOINT`,
   `SENTRY_DSN`), verified against Jaeger. Node worker not instrumented.
5. ~~H5 Helm/K8s~~ — **done 2026-09-07**: `deploy/helm/traceiq/`, lint +
   template + kubeconform in CI, and **installed on a kind cluster** the same
   day (all pods ready, proxy, login, beat). The three first-install traps —
   numeric uid for `runAsNonRoot`, nginx resolver + FQDN, empty
   `MINIO_PUBLIC_URL` — are fixed and recorded in the chart README.
6. **I5 pen test then SOC 2 Type II.** External, calendar-bound. The internal
   pre-assessment is done (`docs/SECURITY_ASSESSMENT.md`: ZAP/Trivy/pip-audit/
   npm-audit baseline, fixes, accepted risks, tester scope) and the control
   matrix an auditor samples is `docs/SOC2_CONTROLS.md`. Everything it
   needs from the codebase now exists.

Closed on 2026-09-01: `Project.data_policy` had no API and no UI, so the
capture policy was enforceable but only settable by direct SQL. Now
`GET`/`PUT /api/projects/{id}/data-policy` (viewer read, admin write, partial,
audited) plus a "Data capture & redaction" panel on the Quality Dashboard,
below the Gate policy and CI cards. The read model returns the *effective*
policy next to the stored one with a `clamped` flag, because MAX_CAPTURE_LEVEL
can hold a project below its request and a screen that hid that would be lying.

Closed on 2026-09-08: SSO setup required insider knowledge. `app/services/
idp_catalog.py` adds 12 named IdP presets (Entra/Okta/Google/Keycloak/Auth0
OIDC, Entra/Okta/ADFS SAML, AD/OpenLDAP, plus generic escape hatches) behind
`GET /api/admin/idp-catalog` and `POST /api/admin/idp-catalog/{id}/derive`,
with a picker card above the SSO section in Settings → Instance (Admin). A
preset is a **form-filling layer, not a new integration**: it only emits keys
already in `instance_settings.REGISTRY`, and `derive()` is pure — the client
PUTs to the existing settings endpoint, so there stays one write path with one
set of validations (including the save-time SAML metadata fetch). Presets carry
the provider quirks that cost an afternoon each: Entra emits group OBJECT IDs,
Google emits no groups claim at all, Keycloak needs a group-membership mapper.
The two load-bearing tests are guards — every derived key must exist in the
registry, and a preset must not write another protocol's keys.

Also 2026-09-08: `users.is_instance_admin` granted to raja.d@thehindu.co.in by
direct SQL. Worth knowing *why* by hand: `POST /api/admin/instance-admins/{id}`
is guarded by the very dependency it grants, so a fresh instance's only clean
route to its first instance admin is the create-only `ADMIN_EMAIL`/
`ADMIN_PASSWORD` env bootstrap — which cannot promote an account that already
exists. That grant therefore has no audit row; later grants should go through
the API. Tenant 1's test account (`user@email.com`) still qualifies via the
first-tenant fallback; making that fallback conditional on "no explicit admin
exists" would close it.

**Per-tenant (multi-tenant SaaS) SSO — designed in conversation, NOT written up.**
Three findings worth not rediscovering if it is picked up:
1. `saml_auth.load_config(get=...)` and `federation.resolve_policy(get=...)`
   already take an injectable key→value getter (`effective = get or _effective`),
   so per-connection config is a *parameterisation*, not a fork — store
   connections in the same key namespace, not typed columns. OIDC has no config
   object at all (`_oidc(key)` inline at eight sites) and needs one first.
2. `load_config` derives `sp_entity_id` as `acs.rsplit("/acs", 1)[0] + "/metadata"`,
   which with a per-connection slug in the path yields an IDENTICAL SP entity id
   for every tenant — breaking app separation in Okta/Entra. Synthesise both the
   ACS URL and the entity id per connection rather than storing them.
3. `users.email` is globally UNIQUE, so a tenant's own IdP asserting another
   company's address would resolve to that existing global account. GitHub's
   model (an assertion grants membership in *that* org, never global identity)
   is a safer fix than a domain allowlist — but a domain-verification gate is
   what Entra, Google and Atlassian all require before federating, and it is an
   authorization boundary, not a routing convenience.

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
Historically `bootstrap_db.py` built fresh schemas from metadata, so an
explicitly named foreign key in a migration existed only on *migrated*
databases - F4's `downgrade` failed on every fresh install with "constraint does
not exist". Fresh installs run the migrations now (see "Migrations" below), so
the two populations can no longer diverge silently — but keep passing `None`:
`alembic check` in `scripts/verify_migrations.py` compares migrated schema to
models, and a name that differs shows up as drift and fails CI.

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

### Tracing is on locally (2026-09-07)

`~/traceiq-test/.env` has `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318` and
a Jaeger all-in-one runs in the same compose project (`traceiq-jaeger-1`,
started from `infrastructure/monitoring/docker-compose.monitoring.yml` with
`-p traceiq` and only the `jaeger` service). UI at http://localhost:16686 —
services `traceiq-backend`, `traceiq-celery-worker`, `traceiq-celery-aggregator`
report. In-memory storage: traces vanish when the container restarts. Logs stay
`LOG_FORMAT=text` for readability; set `json` in that `.env` to see the
structured form.

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

**The local stack is current** as of 2026-09-07 (backend and frontend `:dev`
rebuilt and rolled twice that day: once for the squashed migration — the
backend log showed `e0f1a2b3c4d5 -> f2a3b4c5d6e7`, the first real migration a
current database has run through `bootstrap_db.py` — and once for SAML, whose
endpoints answer 404 until configured). The worker image was not rebuilt (no
worker code changed). See "The local deployment" below. It was ~6 weeks behind on
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

**Credential hygiene (C5): see `docs/CREDENTIAL_HYGIENE.md`.** The rewrite is
prepared, verified, and already applied to this clone — but **not pushed**, so
`origin` and this repo have divergent histories. See the git warning under
"Where we are" before running any git command that touches `origin`. That page
has the push commands and the rotation items outside this laptop's control.

**Migrations at head:** `f2a3b4c5d6e7` (index reconciliation) on top of the
squashed root `e0f1a2b3c4d5`. Verified against a real Postgres by
`scripts/verify_migrations.py` (fresh upgrade, drift check, downgrade to empty,
legacy bridge).

**Behaviour change worth remembering:** the default capture level is now
`standard`, so pre-existing projects stopped recording video, traces and HAR
until someone opts them up to `full`. That was deliberate — backfilling every
row to `full` to preserve the old behaviour was considered and rejected.

### Migrations (2026-09-07)

The chain in `app/alembic/versions/` now starts with a real squashed root,
`e0f1a2b3c4d5_squashed_initial_schema.py`, and `scripts/bootstrap_db.py` is
`alembic upgrade head` behind the advisory lock — it no longer calls
`create_all()`. The 49 pre-squash revisions are in `versions_legacy/`, off
`version_locations`; their head id equals the new root id, so a database that
finished them is already current, and one stamped inside legacy history is
bridged by `bootstrap_db.py` (legacy chain to its head, then the live chain).
Design notes are in the root migration's docstring and
`versions_legacy/README.md`.

What to run: `./run-tests-live.sh --migrations` (= `scripts/verify_migrations.py`,
also in CI) proves `upgrade head` == models via `alembic check`, that
`downgrade base` leaves an empty database, and that the bridge works.
`tests/test_migration_chain.py` pins the chain shape without a database.

Traps that remain:
- Non-model DDL (triggers, functions, grants, RLS) must be *in a migration*;
  autogenerate cannot see it. The audit trigger is restated in the root.
- `teststatus` is used by two tables — enum types in the root are created once
  with `create_type=False`, and autogenerate will emit `sa.Enum(...)` per column
  again if you regenerate; hoist them.
- `alembic.ini`'s `version_path_separator` must not carry an inline comment
  (configparser keeps it; every use of `version_locations` then fails).
- `script.py.mako` imports `sqlmodel` because autogenerate emits
  `sqlmodel.sql.sqltypes.AutoString()` without importing it.
- The old trap — "bootstrap builds from metadata so migration-only DDL is
  missing on fresh installs" — is gone, and with it the reason for the
  `after_create` copy of the audit trigger in `app/services/audit.py`; it stays
  only for the unit-test `create_all()` path. Keep the two texts identical.
- SQLAlchemy's `DDL()` applies `%`-interpolation (double literal `%`), and
  asyncpg rejects multiple statements per execute.

### Workstream H is worth pulling forward if procurement gets real

Not because of compliance, but because two items block a serious deployment:
`celery_beat` is a single point of failure with no leader election that stalls
the whole execution pipeline silently if it dies (RedBeat is wired but opt-in),
and Helm/K8s (H5) does not exist. The rollback gap is closed — see "Migrations".
