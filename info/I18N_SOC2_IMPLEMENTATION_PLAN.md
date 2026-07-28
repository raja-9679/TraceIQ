# Implementation Plan — i18n and SOC 2

Derived from the code-level audit of 2026-07-28. Companion to
`SCOPE_NOTES.md` (deferred work) and `info/FEATURE_GAP_ANALYSIS.md`.

Every claim below was verified against the tree at commit `8b32d4a`; file:line
references are from that snapshot and will drift as work lands.

---

## 1. The two programs are not independent

Reading them as separate projects is the main way this goes wrong. Three
couplings decide the sequencing:

**They fight over the same files.** i18n rewrites all 341 `detail="..."` strings
across `app/api/`. SOC 2 remediation rewrites `api/auth.py`, `api/api_keys.py`,
`core/auth.py`, and `api/endpoints/test_runs.py`. Running both at once on the
same modules means constant rebasing. The error-code refactor and the auth
hardening must be scheduled as one pass over those files, not two.

**They share one prerequisite: there is no regression safety net.** The repo has
4 test files and 7 test functions (`tests/test_stale_run_detection.py`,
`tests/integration/test_async_execution.py`, `tests/e2e/test_execution_flow.py`,
`tests/e2e/test_parallel_execution.py`), and the e2e ones need a live API plus
`tests/e2e/conftest.py` fixtures. `frontend/` has **zero** tests. Touching 1,200+
frontend strings or the auth principal path on that foundation is how you ship a
regression you find in production. Tests are Phase 0 for both programs.

**CI is the shared deliverable.** SOC 2 needs it for CC7.1/CC8.1 control
evidence; i18n needs it to run string-extraction and lint checks that stop
English literals creeping back. One pipeline, two consumers.

**Ordering recommendation:** SOC 2 leads, because its risk is live and audits
have external deadlines. i18n's frontend-only work (Phase B0/B2) runs in
parallel from week 2 since it never touches `app/api/`. The one i18n item that
must interleave with SOC 2 is the error-code contract (B1).

---

## 2. Phase 0 — Foundations (week 1, blocks everything)

### 0.1 Contain the live exposure

The repo `raja-9679/TraceIQ` is **public** (verified via unauthenticated GitHub
API: `"visibility": "public"`). Every finding in the audit — the `SECRET_KEY`
monoculture, the SSRF surface, the `is_active` bypass — is currently readable
with file and line numbers by anyone.

| Step | Action | Notes |
|---|---|---|
| 0.1.1 | **Rotate the TraceIQ password for `raja.d@thehindu.co.in`** | `dump.sql` has published a `$2b$12$` bcrypt hash since 2025-12-20. Independent of all git work — do it first. |
| 0.1.2 | **Decide repo visibility** | Flipping to private is one click, reversible, and neutralises the disclosure value of every other finding. Highest leverage action in this document. |
| 0.1.3 | `git rm dump.sql` | Nothing references it: no `docker-entrypoint-initdb.d` mount, no script, no doc. Obsolete anyway (5 prototype tables vs 51 models). |
| 0.1.4 | History rewrite | `git filter-repo --path dump.sql --path infrastructure/env.prod --path backend/.env --invert-paths`, then force-push. Do `dump.sql` and the `.env` blobs (`9a8e523`, `b8dd5a3`) in **one** rewrite. |
| 0.1.5 | Rotate every credential in the old blobs | Postgres, MinIO, `SECRET_KEY`, LLM keys. Sequence after 0.2.1 so you rotate into a split-key world, not back into the monoculture. |

The `dump.sql` captured traffic (4,680 events, 1,037 real `cookie` values) is
analytics/adtech only — no `PHPSESSID`/`sessionid`/`auth_token`, and the
`authorization` hits are CORS `access-control-allow-headers` allowlists. Worth a
courtesy note to whoever owns thehindu.com; not an account-takeover vector and
not on the critical path.

**Caveat:** the force-push breaks every existing clone and invalidates open PR
diffs. The commit is on 13 remote branches. Coordinate before firing.

### 0.2 Test harness bootstrap

Not optional, and not a place to economise. Target is a net that fails loudly
when a refactor breaks behaviour — not coverage percentage.

- **Backend unit layer.** Add `tests/conftest.py` with an in-memory or
  transactional-rollback session fixture so tests stop requiring the full stack.
  Then write characterisation tests for exactly the modules Phase A rewrites:
  `core/auth.py` (JWT and API-key principal resolution, `is_active`),
  `services/access_service.py` (the four-layer allow/deny matrix),
  `api/endpoints/test_runs.py` (finalize idempotency — write the *failing* test
  first, it documents the bug).
- **Frontend smoke layer.** Vitest plus React Testing Library. Do not chase
  coverage; render the four heavy pages (`SuiteDetails`, `Settings`,
  `WorkspacePage`, `TestRunDetails`) with mocked API responses and assert they
  mount. That alone catches the class of error i18n extraction produces (a
  broken key, a missing interpolation) which is otherwise invisible until a user
  hits the page.
- **Date/timezone tests.** Pure functions, trivial to test, and B0 depends on
  them. Include a naive-UTC input and a non-UTC `TZ` to pin the bug.

Effort: **M**. Roughly a week, and it pays for itself in Phase A alone.

### 0.3 CI pipeline

There is no `.github/` directory at all — confirmed absent. Create:

```
.github/
├── workflows/
│   ├── ci.yml            # pytest + vitest + ruff + eslint + tsc -b, on PR
│   ├── security.yml      # CodeQL (python, javascript), pip-audit, npm audit,
│   │                     # gitleaks/trufflehog on full history, Trivy on images
│   └── i18n.yml          # (from B2) fail PR on new hardcoded JSX strings
└── dependabot.yml        # pip, npm ×2, docker, github-actions
```

Plus branch protection on `main`: require CI green, require one review, block
force-push. Recent commits went straight to `main`, so this is also the fix for
"no change-management evidence."

This is the cheapest possible CC7.1/CC8.1 evidence and you are currently at
zero. Do not defer it — an auditor asks for it in the first hour.

---

## 3. Track A — SOC 2

### A1 — Contain real vulnerabilities (weeks 1–3)

These are exploitable today, not paperwork. Ordered by blast radius.

**A1.1 Break the `SECRET_KEY` monoculture.** One value is currently the JWT
signing key, the worker/webhook shared secret (`WEBHOOK_SECRET or SECRET_KEY` at
`api/endpoints/test_runs.py:699`, with `WEBHOOK_SECRET` set in no compose file),
*and* the KEK for tenant secrets via unsalted SHA-256 (`core/secrets.py:16`).
Rotation is self-blocking: rotating invalidates every stored customer secret.

Introduce three independent settings — `JWT_SECRET_KEY`, `WEBHOOK_SECRET`,
`SECRETS_KEK` — each required in production with a fatal startup check rather
than a silent fallback. Replace the SHA-256 derivation with a real KDF
(scrypt or Argon2id, per-tenant salt). Ship a one-time re-encryption migration
for `ProjectSecret.value_encrypted`, `IssueTrackerConfig.auth_secret_encrypted`,
and `User.mfa_secret`, reading with the old derivation and writing with the new.
Switch the shared-secret comparisons to `secrets.compare_digest`.

Sequence this **before** 0.1.5 so credential rotation happens once, into the new
scheme.

**A1.2 Close the access-lifecycle bypass.** `is_active` is checked only at
password login (`api/auth.py:141`) and appears nowhere in `core/auth.py`. Add
the check to both `_user_from_jwt` and `_principal_from_api_key`. Then make
`ApiKey.workspace_id`/`project_id`/`role_id` actually constrain authorization —
today the API-key path returns the creating user and RBAC runs against that
user's full cross-workspace access, so a "project-scoped" key is unscoped.
Restrict key minting to workspace admins (currently any member,
`api/api_keys.py:61`). Add an admin deactivate/offboard endpoint — `api/admin.py`
has no DELETE routes — that also clears `UserProjectAccess` and
`UserTestCaseAccess`.

**A1.3 Kill the SSRF cluster.** Two moves, and the second does most of the work.

One shared validator, applied everywhere user-supplied URLs are fetched:

```python
# app/core/net_guard.py
def validate_outbound_url(raw: str) -> str:
    """Scheme allowlist, resolve-then-check, reject private/loopback/link-local.
    Must be re-applied after every redirect hop."""
```

Call sites: `api/case_generation.py:282` and `:347`,
`api/workspace_webhooks.py:86`, `services/issue_trackers.py:34`, plus the six
worker fetch sites in `execution-engine/src/core/test-executor.ts`
(`:148, 361, 401, 487, 548, 1221`). **Delete `verify=False`** at
`case_generation.py:347` — it is the only instance in the repo, and that path
feeds fetched content into an LLM prompt and returns the output, which
exfiltrates internal pages through the model.

Then the structural fix: move `execution-worker` onto an egress-only Docker
network with no route to `redis`, `minio`, `postgres`, or `backend`. Workers
reach the queue through an explicit broker interface, not by being co-resident
with everything. This neutralises most worker-side SSRF regardless of what a
test step asks for, and it is one compose change.

Note `TestRun.allowed_domains` currently *looks* like an egress allowlist but
only gates header injection (`core/network-interceptor.ts:107-125`) — either
make it enforce, or rename it so it stops reading as a control it isn't.

**A1.4 Make `finalize` idempotent.** `api/endpoints/test_runs.py:682` goes
straight from the secret check to loading the run, then fires six side-effecting
tasks. A retry sends duplicate customer emails and duplicate webhooks into
customer CI. The correct guard already exists ~90 lines below in
`force_complete` — lift it. Add `UniqueConstraint(test_run_id, test_name)` on
`TestCaseResult`, stop appending `network_events` on redelivery
(`result_aggregator.py:526`), and add an `XAUTOCLAIM` reaper for the hardcoded
`'aggregator-1'` consumer so a worker death stops silently losing results.

**A1.5 Fix the broken deletes.** `services/workspace_service.py:505` is a no-op
`SELECT` commented "this is just to show what we'd do"; with no CASCADE,
deleting a non-empty project raises an unhandled `IntegrityError` → 500.
Implement real cascade (explicit ordered deletes or DB-level `ondelete`), and
have `delete_workspace` handle its projects.

### A2 — Build the evidence layer (weeks 3–6)

This is the part auditors actually sample, and it is your weakest area.

**A2.1 Security-event audit logging — the single biggest Type I blocker.**
`AuditLog` exists (`models.py:1076`) with 14 write sites covering test-asset
CRUD only. Nothing security-relevant is recorded.

Extend the model with `ip_address`, `user_agent`, `request_id`, `actor_type`
(user/api_key/system), `api_key_id`, `outcome` (success/failure), and a
non-nullable `tenant_id`. Stop nullifying `workspace_id` during workspace
deletion (`workspace_service.py:525`) — mutating audit rows inside a destructive
operation is exactly what gets probed.

Then instrument the events that are currently invisible:

- Authentication: login success **and failure**, logout, MFA enable/disable,
  password reset request and completion, refresh-token replay detection
- Authorization: role and permission grants/revocations (`api/workspaces.py`
  never imports `AuditLog`), tenant-admin bulk assignment (`api/admin.py:75`)
- Credential lifecycle: API key create/revoke (`api/api_keys.py:49, 130`)
- Destruction: run, project, workspace, and user deletion — note
  `DELETE /api/runs?all=true` currently destroys everything a user can reach with
  no trace at all

Add append-only enforcement (revoke UPDATE/DELETE from the app role; consider
hash-chaining rows), a documented retention period, and an admin/auditor read
view. Fix the fallback branch at `test_runs.py:654` that ignores its path params.

**A2.2 Observability.** Zero Sentry/OTel/Datadog across all three manifests, and
tasks use bare `print()` with ~15 swallowed `except Exception` blocks, so
systemic failure is invisible. Add error reporting, structured JSON logging with
a redaction helper, healthchecks on `backend` and all three Celery services
(`/health/ready` already exists and is good — `api/observability.py:114`),
resource limits beyond the one service that has them, and alerts on
`jobs:dead-letter` plus task failure. Reconcile the two conflicting stale-run
reapers (`cleanup_tasks.py:18` vs `result_aggregator.py:702`).

**A2.3 Backups.** Currently only manual `pg_dump`/`mc mirror` steps in
`PRODUCTION_DEPLOY.md`. Needs automation, offsite copies, documented RPO/RTO,
encryption, and — the part everyone skips — a **scheduled restore test** whose
output is the audit artifact. All data currently sits on local Docker volumes.

### A3 — Controls and hardening (weeks 5–9)

- **TLS.** No `443`/`ssl`/`tls`/`acme` match anywhere in `infrastructure/*.yml`;
  `frontend/nginx.conf:2` is `listen 80` with no HSTS or security headers.
  Meanwhile `env.prod` advertises `https://…thehindu.co.in` and prod publishes
  `8000` and `9000` straight to the host, so termination lives outside version
  control and is unauditable. Bring a reverse proxy (Caddy or Traefik) into the
  repo with automatic certs, HSTS, and security headers. Add `sslmode=require`
  to Postgres URLs, `rediss://` plus `requirepass` for Redis, and stop
  force-prepending `http://` in `core/storage.py:10`.
- **Container posture.** All five Dockerfiles run as root — no `USER`, no
  `cap_drop`, no `read_only`, no `no-new-privileges`. Aggravated by
  `RAW_PLAYWRIGHT_ENABLED`/`ALLOW_PYTHON_SCRIPTS`, which the repo itself labels
  "RCE in worker." Add non-root users, drop capabilities, digest-pin base images
  (`appium/appium:latest`, `dpage/pgadmin4`, and `minio/minio` currently float).
- **Compose hygiene.** Remove insecure fallbacks that silently produce unsafe
  deploys: `POSTGRES_PASSWORD:-password`, `MINIO_ROOT_PASSWORD:-minioadmin`,
  `ZAP_API_KEY:-changeme`, hardcoded pgAdmin `admin@example.com`/`admin` on host
  port 8014, and `BACKEND_CORS_ORIGINS:-["*"]` in **both** prod files
  (`docker-compose.prod.yml:95`).
- **Auth policy.** No account lockout or failed-login counter exists anywhere.
  The 8-character minimum applies only on reset (`api/auth.py:580`), so
  registration accepts a 1-character password. Rate limiting is in-memory
  slowapi behind `uvicorn --workers 4` with no `--proxy-headers`, so limits are
  4× and keyed on the proxy IP — move to Redis storage. Add per-workspace
  enforced-MFA policy; note API-key auth bypasses MFA entirely today. Extend
  rate limiting beyond `/api/auth/*`.
- **Data protection.** Encrypt `TestSuite.settings` (the documented home for API
  auth headers, inherited down the tree and returned as `effective_settings` to
  any viewer), `Persona.auth_headers`/`session_state`, `AuthSession.storage_state`,
  and `WorkspaceWebhook.secret`. Redact auth headers before writing
  `network_events`/`TestCaseResult` and before HAR capture (currently
  `content: 'embed'`, i.e. full bodies into MinIO). Stop printing `step.value`
  (`test-executor.ts:36`). Stop copying step values into `AuditLog.changes`
  (`endpoints/test_cases.py:141`) and rendering them raw in the UI audit tab to
  viewer-role users (`frontend/src/pages/SuiteDetails.tsx:1740`).
- **Retention.** `purge_old_runs` is correctly written but dead everywhere:
  `RUN_RETENTION_DAYS` defaults to 0 and `celery_worker` uses an explicit
  `environment:` list instead of `env_file`, so setting it in `.env` never
  reaches the container. Fix the wiring, set a non-zero default, stop deleting
  DB rows when the artifact delete fails (currently orphans MinIO objects
  permanently), and add a MinIO lifecycle policy plus audit-log retention.
- **Erasure.** `DELETE /api/auth/me` is soft-only (`api/auth.py:641`) and leaves
  `RefreshToken.ip_address`/`user_agent`, `UserSettings.notification_email`, and
  every captured artifact. Build a real erasure path.
- **Upload limits.** App-build upload has a good extension allowlist and UUID
  prefixing (`api/app_builds.py:80`) but **no size limit at any layer** and no
  magic-byte check. Add a global body-size limit and `client_max_body_size`.
- **Unauthenticated engine endpoint.** `POST /run` has no auth on any route
  (`execution-engine/src/server.ts:188`) and takes an attacker-controlled
  `callbackUrl`; dev compose publishes port 3000 to the host. Absent from prod
  compose, but fix or remove it.

### A4 — Audit readiness (weeks 9–12)

Type I is a point-in-time design opinion, so the goal is "controls exist and are
documented," not "controls have a year of history." Produce: a system
description, a data-flow and asset inventory, an access-review procedure with
evidence, an incident-response runbook (the `dump.sql` exposure is your first
real entry — write it up), a change-management policy that matches the now-real
branch protection, and a vendor list (AWS/MinIO, LLM providers, email/Slack).
Pick the auditor before A3 finishes; their control matrix will reorder some of
A3 and it is cheaper to learn that early.

Then Type II needs an observation window (typically 3–12 months) during which
the A2 evidence must actually accumulate — which is the real reason A2 cannot
slip.

---

## 4. Track B — i18n

Verified starting point: no `i18next`/`react-intl`/`@formatjs`, no
`babel`/`gettext`, no locale catalogs, no `useTranslation` call sites, no
`Accept-Language` handling anywhere in `backend/app`, and a static
`<html lang="en">` with no `dir`.

### B0 — Fix the timestamp bug first (week 2, frontend-only, parallelisable)

Do this as a bug fix, not as i18n. It ships value on its own and creates the
seam everything else needs.

The backend stores **naive** UTC (`datetime.utcnow()` at ~40 sites; not one of
the 39 migrations uses `TIMESTAMPTZ`), so the API emits ISO strings with no `Z`.
There are 9 duplicated `formatDate` helpers and only `Settings.tsx:87`
normalises. The other 8 pass naive strings to `new Date()`, which parses them as
**browser-local**, so every timestamp is off by the viewer's offset — and
`lib/utils.ts:18` then shifts again to a hardcoded `Asia/Kolkata` with a
hardcoded `en-IN` and `hour12: true`, double-skewing.

Steps: migrate columns to `TIMESTAMPTZ` and switch to timezone-aware datetimes
(a large but mechanical migration — do it behind the tests from 0.2); collapse
the 9 helpers into one locale- and timezone-aware function; **wire it to the
already-existing `user_settings.timezone`/`date_format`**, which are written to
the DB by `Settings.tsx` and read by nothing. Add an ESLint rule banning
`new Date(` and `toLocaleString(` outside that helper so it can't re-fragment.

Also unify number formatting: 25+ bare `.toLocaleString()` calls follow browser
locale while dates followed `en-IN`, so one screen can mix conventions. Currency
is hardcoded USD string-built at `Billing.tsx:122` — move to
`Intl.NumberFormat`. Pass an explicit `locale` to the `date-fns`
`formatDistanceToNow` calls (`UsersPage.tsx:529`, `WorkspacePage.tsx:639, 689`).

### B1 — Decide the error-message contract (must interleave with A1/A2)

**This is the decision that makes or breaks the whole program.** There are 110
frontend sites reading `err.response.data.detail`, with ~10 pages each defining
their own one-line `errDetail` helper. Backend English is rendered directly into
the UI. Translate only the frontend and you ship a permanently half-English
product.

Convert the 341 `detail="..."` literals into stable machine-readable codes plus
parameters — English becomes one catalog rather than the wire format:

```python
raise AppError("suite.cannot_add_case_with_submodules", params={"suite": s.name})
# → {"code": "...", "message": "<English default>", "params": {...}}
```

The frontend resolves `code` against its catalog and falls back to `message`.
Do this **as part of the Phase A pass over `app/api/`**, not as a separate
sweep — those are the same files A1.1/A1.2 rewrite, and one pass beats two.
Highest-traffic modules first: `api/agent_ownership.py` (31 sites),
`api/endpoints/test_runs.py` (30), `api/auth.py` (30), `api/security.py` (28).

Engine-side errors need the same treatment for a different reason: strings from
`execution-engine/src/worker.ts:323` and friends are **persisted to the DB** as
run error text, so they must be codes resolved at render time, not translated at
write time.

### B2 — Stand up the infrastructure (week 3+, parallel to A)

- Add `react-i18next` (fits React 19 + Vite; an extractor can be scripted
  against the existing `@babel/parser` dependency).
- Mount the provider in `frontend/src/main.tsx` — currently just
  `<StrictMode><App/><Toaster/></StrictMode>` with no provider slot, and
  `src/context/` holds only `AuthContext.tsx`.
- Make `<html lang>` and `dir` dynamic; persist locale to `UserSettings`
  alongside the timezone work from B0.
- Adopt ICU message format from day one so plurals and gender work later.
- **Pilot on `components/layout/DashboardLayout.tsx:50-74`** — the 25-item nav
  array is a clean, low-risk first conversion that proves the whole toolchain.
- Land the `i18n.yml` CI check that fails a PR introducing new hardcoded JSX
  text, so the backlog can only shrink.

### B3 — Extract (weeks 4–10, the bulk)

~1,200–1,600 distinct keys across 35 pages and 22.6k LOC. Go largest-first:
`SuiteDetails.tsx` (1905 LOC) → `Settings.tsx` (1468) → `WorkspacePage.tsx`
(1270) → `TestRunDetails.tsx` (1400) → the long tail.

Fix three things in the same pass, while each file is open:

1. **Pluralisation → ICU.** Only ~8 sites, two flavours: ternaries
   (`LLMUsage.tsx:56`, `ComparisonView.tsx:135`) and the `(s)` dodge
   (`Traceability.tsx:33`, `Webhooks.tsx:208, 460`, `TestBuilder.tsx:117, 150`).
   Neither survives languages with more than two plural forms.
2. **Restructure the `·`-joined microcopy.** Sentences assembled from fragments
   in English word order won't reorder — e.g. `QualityDashboard.tsx:346`,
   `Security.tsx:349`, `TestRunDetails.tsx:324`. These need to become whole
   translatable sentences, which takes judgement per site.
3. **Hardcoded enumerations.** Timezone labels are literal `SelectItem` children
   (`Settings.tsx:346`); schedule presets are English *and* UTC-only
   (`ScheduleModal.tsx:30-33`).

Then the backend-side surfaces: a locale middleware (`Accept-Language` with the
user preference winning), the catalog, and the notification templates in
`tasks/notification_tasks.py` — 512 lines of f-string-concatenated email, Slack,
and Teams bodies, which need a template engine and must key off the
**recipient's** locale, not the request's. Fix the enum leak at `:161` where
`status.value.upper()` renders raw DB values as display text. Account emails are
assembled inline in `api/auth.py:564, 566, 620` and need the same treatment.

### B4 — Schedules, AI output, and the second locale (weeks 8–12)

- **Schedule timezones.** `TestSchedule` (`models.py:753`) has no `timezone`
  column; croniter runs on naive UTC in both `tasks/schedule_tasks.py:20` and
  `api/endpoints/schedules.py:84`. The UI is honest rather than correct
  ("Times are evaluated in UTC"), which makes the `Every Monday at 9AM` preset
  wrong for every non-UTC customer. Add the column, make croniter tz-aware,
  handle DST, localise the presets, retire the disclaimer. **M–L** — a schema
  change plus real DST correctness work.
- **AI output language.** Cheap: every provider's `complete()` already takes a
  `system` kwarg, so thread a locale into `ai/engine.py:16`,
  `services/failure_analysis.py:86`, `api/case_generation.py:159`, and
  `tasks/heal_tasks.py:83`. Persist the language alongside each analysis — LLM
  output is stored, so a user switching locale won't retranslate old analyses.
  That's a product decision to make explicitly rather than discover. Selector-
  healing prompts return code, not prose, and correctly stay locale-independent.
- **Add a real second locale.** Everything before this is unvalidated
  scaffolding. Pick one target, translate, and run a pseudolocalisation pass
  (accented, ~40% longer strings) in CI to surface layout breakage before a
  translator ever sees it.

### Deferred → `SCOPE_NOTES.md`

**RTL is out of scope for v1** and should be recorded as parked, not dropped.
There are ~300 physical-direction Tailwind utilities (`mr-2` ×78, `mr-1.5` ×49,
`text-right` ×38, plus `pl-*`/`pr-*`/`left-*`/`right-*`/`border-l`) and
**zero** logical equivalents — no `ms-*`, `me-*`, `ps-*`, `pe-*`, `text-start`.
Absolutely-positioned input icons (`pl-9` with `left-3`) mirror incorrectly.
This is a mass mechanical rewrite of nearly every component; only start it once
an Arabic or Hebrew locale is actually committed to commercially.

Two areas need **no** work, worth recording so nobody re-audits them: charset and
collation are already correct (UTF8, no fixed-width `VARCHAR(n)` on user text),
and there is no text baked into images or SVGs (icons are glyph-only
`lucide-react`; all 17 apparent `<text` grep hits are `<textarea`).

---

## 5. Phase-by-phase execution view

**Five execution phases**, built from ten workstreams (one shared foundation,
four SOC 2, five i18n). The tracks overlap, so ten workstreams compress into five
calendar windows. RTL sits outside all five by design (see `SCOPE_NOTES.md`), and
the SOC 2 Type II observation window is a waiting period after Phase 5, not an
engineering phase.

Do not start a phase before the previous one's exit criteria are met. The exit
criteria matter more than the week numbers — they are what makes each phase
falsifiable.

### Phase 1 — Foundation and containment (week 1)

*Workstreams: 0.1, 0.2, 0.3. Blocks everything else.*

- Rotate the password for `raja.d@thehindu.co.in` (published bcrypt hash).
- Decide repo visibility; if private, do it now.
- `git rm dump.sql`; one `filter-repo` rewrite covering `dump.sql` **and** the
  `.env` blobs (`9a8e523`, `b8dd5a3`); coordinate the force-push across 13
  remote branches.
- Backend test harness: `tests/conftest.py` with a transactional session
  fixture, then characterisation tests for `core/auth.py`,
  `services/access_service.py`, and a deliberately *failing* finalize-idempotency
  test.
- Frontend Vitest + RTL smoke tests mounting the four heavy pages.
- Date/timezone unit tests, including a naive-UTC input under a non-UTC `TZ`.
- `.github/workflows/ci.yml` + `security.yml` + `dependabot.yml`; branch
  protection on `main`.

**Exit criteria:** CI green and required on PRs; force-push cannot bypass `main`;
the password is rotated; `dump.sql` is absent from all history; tests exist and
pass for every module Phase 2 rewrites. *Application credentials are deliberately
NOT rotated yet — that waits for the key split in Phase 2, so rotation happens
once.*

### Phase 2 — Stop the bleeding (weeks 2–3)

*Workstreams: A1 (all), B0, B2. Runs in parallel — A1 is backend, B0/B2 are
frontend.*

SOC 2 side, in this order:
- Split `SECRET_KEY` into `JWT_SECRET_KEY` / `WEBHOOK_SECRET` / `SECRETS_KEK`
  with fatal production startup checks; replace the unsalted SHA-256 with
  scrypt/Argon2id; ship the re-encryption migration; use `compare_digest`.
- **Then** rotate every credential from the leaked blobs, into the new scheme.
- Add `is_active` to both `_user_from_jwt` and `_principal_from_api_key`; make
  `ApiKey` scope fields actually constrain RBAC; restrict minting to admins; add
  the admin offboard endpoint that also clears `UserProjectAccess` /
  `UserTestCaseAccess`.
- Add `core/net_guard.py` and apply it at all ten fetch sites; **delete
  `verify=False`**; move `execution-worker` to an egress-only network.
- Lift the `force_complete` guard into `finalize`; add
  `UniqueConstraint(test_run_id, test_name)`; stop re-appending `network_events`;
  add the `XAUTOCLAIM` reaper.
- Implement real cascade in `delete_project` / `delete_workspace`.

i18n side:
- `TIMESTAMPTZ` migration and timezone-aware datetimes.
- Collapse the 9 `formatDate` helpers into one; wire it to the existing
  `user_settings.timezone` / `date_format`; add the ESLint ban on raw
  `new Date(` / `toLocaleString(`.
- Unify numbers and currency on `Intl.NumberFormat`; pass explicit locales to
  `date-fns`.
- Install `react-i18next`, mount the provider in `main.tsx`, make `lang`/`dir`
  dynamic, adopt ICU, pilot on `DashboardLayout.tsx:50-74`, add `i18n.yml`.

**Exit criteria:** no known exploitable vulnerability remains; one key can be
rotated without invalidating tenant secrets; a deactivated user and a revoked API
key both lose access immediately; a replayed `finalize` sends no duplicate email;
timestamps render correctly under a non-UTC browser; the nav renders from the
catalogue and `i18n.yml` fails a PR that adds a hardcoded string.

### Phase 3 — Evidence layer and the error contract (weeks 3–6)

*Workstreams: A2 (all), B1.*

- Extend `AuditLog` with `ip_address`, `user_agent`, `request_id`, `actor_type`,
  `api_key_id`, `outcome`, non-nullable `tenant_id`; stop nullifying
  `workspace_id` on workspace deletion.
- Instrument the events that are currently invisible: login success **and**
  failure, logout, MFA changes, password reset, refresh replay, role and
  permission grants, tenant-admin assignment, API-key create/revoke, and every
  deletion path including `DELETE /api/runs?all=true`.
- Enforce append-only (revoke UPDATE/DELETE from the app role; consider hash
  chaining); set retention; build the auditor read view; fix the
  `test_runs.py:654` fallback.
- Error reporting (Sentry/OTel), structured logging with a redaction helper,
  healthchecks on `backend` and all Celery services, resource limits,
  dead-letter and task-failure alerts, reconcile the two stale-run reapers.
- Automated encrypted offsite backups with documented RPO/RTO **and a scheduled
  restore test** whose output is the audit artifact.
- Convert the 341 `detail="..."` literals to `AppError` codes + params, frontend
  resolution with `message` fallback, engine errors to codes. Do this in the same
  pass over `app/api/` as the Phase 2 auth work where files overlap.

**Exit criteria:** every event in the list above produces an immutable audit row
carrying actor, IP, and outcome; a restore from backup has actually been
performed and timed; no endpoint returns a user-facing English sentence as its
wire format.

### Phase 4 — Hardening and extraction (weeks 5–9)

*Workstreams: A3, B3. The longest phase; B3 is the single biggest chunk of work
in the document.*

- TLS: reverse proxy in-repo with automatic certs, HSTS, security headers;
  `sslmode=require`, `rediss://` + `requirepass`, MinIO TLS; stop force-prepending
  `http://` in `core/storage.py:10`.
- Containers: non-root users, `cap_drop`, `no-new-privileges`, digest-pinned
  bases.
- Compose: remove every insecure fallback (`:-password`, `:-minioadmin`,
  `:-changeme`, pgAdmin defaults) and the `["*"]` CORS default in both prod files.
- Auth policy: account lockout, registration password policy, Redis-backed rate
  limiting with `--proxy-headers`, per-workspace MFA enforcement, rate limits
  beyond `/api/auth/*`.
- Data protection: encrypt `TestSuite.settings`, `Persona.*`,
  `AuthSession.storage_state`, `WorkspaceWebhook.secret`; redact auth headers
  before `network_events`/`TestCaseResult`/HAR; stop logging `step.value`; stop
  copying secrets into `AuditLog.changes` and rendering them to viewers.
- Retention: fix the `env_file` wiring, non-zero default, stop orphaning MinIO
  objects on failed deletes, add lifecycle and audit-log retention.
- Real erasure path; global upload/body size limits; authenticate or remove
  `POST /run`.
- Extract ~1,200–1,600 keys largest-first (`SuiteDetails` → `Settings` →
  `WorkspacePage` → `TestRunDetails` → tail), converting plurals to ICU and
  restructuring `·`-joined microcopy in the same pass.
- Backend locale middleware, catalogue, notification templates keyed to
  **recipient** locale, account emails, and the `status.value.upper()` enum leak.

**Exit criteria:** TLS config lives in the repo; no container runs as root; a
credential typed into a `fill` step appears in no log, audit row, or artifact;
retention actually deletes on schedule; `i18n.yml` reports zero remaining
hardcoded strings in the UI.

### Phase 5 — Audit readiness and locale completion (weeks 8–12)

*Workstreams: A4, B4.*

- System description, data-flow and asset inventory, access-review procedure
  with evidence, incident-response runbook (the `dump.sql` exposure is entry
  one), change-management policy matching the now-real branch protection, vendor
  list.
- Engage the auditor — ideally before Phase 4 ends, since their control matrix
  will reorder some of A3.
- Add `TestSchedule.timezone`, tz-aware croniter with DST handling, localised
  presets, retire the "evaluated in UTC" disclaimer.
- Thread locale into the four prompt builders; persist the generated language.
- Ship a real second locale plus a pseudolocalisation CI pass.

**Exit criteria:** an auditor has the Type I evidence package; a schedule fires at
09:00 local across a DST boundary; the product runs end-to-end in a second
language with no English leakage.

### After Phase 5 — Type II observation window

3–12 months during which the Phase 3 audit-log and monitoring evidence
accumulates. Cannot be compressed by engineering effort, which is the real reason
Phase 3 must not slip.

### Effort

| | Duration | Dominated by |
|---|---|---|
| SOC 2 Type I readiness | 10–12 weeks | Phase 2 (~3 weeks) + Phase 3 |
| i18n to a shipped second locale | 8–10 weeks | Phase 4 extraction |
| **Combined** | **14–16 weeks** | overlap, not 22 weeks serial |

One focused engineer. The combined figure beats the serial sum because Phase 2
and Phase 4 each run backend and frontend work in parallel.

## 6. If you only do five things

1. **Rotate the password; make the repo private.** Hours of work, removes the
   most exposure of anything here.
2. **Stand up CI with secret and dependency scanning.** A day. Currently zero,
   and it is the first thing an auditor asks for.
3. **Split `SECRET_KEY` into three keys.** Unblocks all future rotation, which
   is otherwise permanently self-blocking.
4. **Add security-event audit logging.** The single biggest Type I blocker, and
   the one with no shortcut.
5. **Consolidate `formatDate` and fix the naive-UTC parsing.** Fixes a live
   customer-visible bug, deletes 8 duplicates, activates settings you already
   built, and creates the seam i18n needs. Best ROI in the document.
