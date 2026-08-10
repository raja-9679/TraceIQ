# Regulated-Domain Readiness Plan

What TraceIQ must change to be sellable into insurance, payments, and
enterprise SaaS procurement. Written 2026-08-07 against
`feature/enterprise-auth-ai`. Every file:line below was verified against the
working tree at that point — re-check before acting on an old copy of this doc.

## Status

**Phases 1, 2 and 3 are DONE** (2026-08-07, eight commits). Workstreams C, A,
B, D and E have landed; the file:line references in those sections describe the
code as it was *before* the fix and are kept as the rationale record, not as a
map of the current tree.

| Workstream | State |
|---|---|
| C — credential leaks | done |
| A1 — upload chokepoint | done |
| A2–A8 — redaction | done |
| B — capture policy | done |
| D — encryption at rest + TLS + key rotation | done |
| E — audit trail (E1–E5) | done |
| F1 — federated provisioning (the tenant bug) | done (2026-08-10) |
| F2 — SCIM 2.0 + deprovisioning | done (2026-08-10) |
| F4 — separation of duties | done (2026-08-10) |
| F5 — roles cleanup | done (2026-08-10) |
| F3 — SAML 2.0 | deferred — see SCOPE_NOTES.md |
| G — deletion/residency (G1–G4) | done (2026-08-10) |
| H — operability | **next** |
| I — proving it | partial: CI runs the full unit suite + a new engine suite |

What exists now that did not before:

- `execution-engine/src/core/redact.ts` + `backend/app/services/redaction.py` —
  mirror implementations with mirror corpora.
- `core/artifact-store.ts` — the single upload chokepoint, capture-level gated.
- `app/services/data_policy.py` + `Project.data_policy` (migration
  `b7c8d9e0f1a2`) + the `MAX_CAPTURE_LEVEL` instance ceiling.
- `app/core/secrets.py` — `v1:` envelope over a MultiFernet ring, `SECRETS_KEY`
  independent of `SECRET_KEY`, `scripts/rotate_secrets.py`.
- `db_url_for()` / `redis_url_with_tls()` — one derivation for the twenty engine
  and client constructions; `REQUIRE_TRANSPORT_SECURITY` for strict mode.
- `app/services/audit.py` + `app/api/audit.py` (migration `c8d9e0f1a2b3`) —
  hash chain, append-only trigger, actor context, CSV export, verify endpoint,
  and independent audit retention.

Test counts went from 18 running in CI to 377 (291 backend, 86 engine), and the
engine had no test runner at all before this.

### Corrections to this document, found by building it

Three claims in the sections below turned out to be wrong or incomplete, and
the fix is in the code rather than here:

1. **D3 said nine sync engines. There are nineteen.** Repointing them was
   mechanical but the count matters for anyone estimating.
2. **D3's `sslmode` → `ssl=true` translation is wrong.** SQLAlchemy's asyncpg
   dialect maps `ssl` back onto sslmode semantics, so `ssl=true` raises
   `ClientConfigurationError`. The translation must PRESERVE the value;
   coercing `verify-full` to a boolean would silently downgrade certificate
   verification to bare encryption.
3. **E2's trigger would not have existed on a fresh install.**
   `bootstrap_db.py` builds the schema from model metadata and stamps head
   without running migrations, and metadata cannot express a trigger — so new
   deployments, the ones most likely to be audited, would have had the table
   and no guard. The DDL is now attached to the table's `after_create` event as
   well. Any future migration that adds non-model DDL has the same trap.

Also: `auditlog` had foreign keys to `workspace` and `users` with no `ON
DELETE`, so deleting either was already blocked or forced the old code to
rewrite history. Both are dropped — an FK from an append-only history table
into a mutable entity table forces a choice between destroying history and
blocking deletion.

D5's transport checks WARN by default and are fatal only under
`REQUIRE_TRANSPORT_SECURITY`. Making them unconditionally fatal broke four
existing tests, which was the useful signal: it would stop every deployment
booting on upgrade, and the escape hatch operators reach for is
`ENVIRONMENT=development`, which disables the secret checks too.

The ordering is deliberate. Workstream C is small and must go first, because
every downstream protection is pointless while the product is actively writing
plaintext credentials into three tables. Workstream A is the single biggest
unlock and is gated on one mechanical refactor (A1).

---

## Domain gating summary

| Domain | Needs | Rough calendar |
|---|---|---|
| News / media | C only | weeks |
| Enterprise SaaS | C + F + H + I | ~1 quarter |
| Insurance | C + A + B + D + E + F1/F2 + G | ~2 quarters |
| Payments (CDE) | all of the above + F3 + certifications | 4+ quarters |
| Payments (non-CDE only) | same as enterprise SaaS + an enforceable capture floor (B4) | ~1 quarter |

**Strategic note on payments:** serving a payments company's *cardholder data
environment* is a multi-year, calendar-bound compliance project with poor ROI.
Serving the same company's marketing site, merchant dashboard, onboarding
funnel, and support portal — which is most of their UI surface — requires only
an *enforceable, provable* capture floor (B4) so a QSA can confirm the tool is
incapable of entering CDE scope. That is a legitimate scoped product position
and costs almost nothing beyond work already planned.

---

## Workstream C — Stop leaking credentials (do first, ~1 week)

Small, entirely internal, no schema changes. Blocks nothing else but everything
else is undermined without it.

- **C1. `fill` does not interpolate.**
  `execution-engine/src/core/test-executor.ts:674` calls
  `locator.fill(step.value || '')` with no `resolve()` — unlike `goto` (:81),
  `http-request` (:128-131), `assert` (:1171). So `{{secret.PASSWORD}}` types
  the literal template, which pushes every user to put plaintext passwords in
  `TestCase.steps` (an unencrypted `Column(JSON)`, `backend/app/models.py:311`).
  Fix the call, then audit **every** handler in the switch
  (`test-executor.ts:79`) for the same omission, and add a test asserting each
  step type that accepts a user string resolves templates.
- **C2. Credentials fan out into two more tables.** Plaintext step values are
  copied verbatim into `AuditLog.changes`
  (`backend/app/api/endpoints/test_cases.py:116`, `:244` —
  `changes=case.model_dump(mode='json')`) and every `TestCaseRevision.snapshot`
  (`models.py:423`). Redact step values on the way into both.
- **C3. `GET /api/jobs/poll` hands out decrypted secrets.**
  `backend/app/api/jobs.py:45-61` returns the raw job payload including
  decrypted project secrets to any workspace-scoped API key. Scoping is
  `_require_api_key_workspace` only (`jobs.py:34-42`) — no role check, and the
  `ApiKey.project_id` narrowing that exists on the model (`models.py:1181`) is
  not applied. Add both.
- **C4. `/metrics` is unauthenticated.** `backend/app/api/observability.py:67`
  has no `Depends`, while the adjacent `queue_health` (`:150-152`) is guarded.
- **C5. Rotate committed credentials, then `git filter-repo`.** Committed `.env`
  history and the tracked `dump.sql` mean the honest answer to "have credentials
  been exposed in version control" is currently yes. Rotation is independent of
  the history rewrite and should not wait for it. Every regulated buyer's
  security questionnaire asks this.

---

## Workstream A — Redaction (the biggest unlock, ~4-6 weeks)

### A1. Create one artifact upload chokepoint (prerequisite, mechanical)

Today there are **three** MinIO clients and **eleven** upload call sites:

| File | Client | Upload sites |
|---|---|---|
| `execution-engine/src/worker.ts` | `:150` | `:1195` video, `:1204` trace, `:1216` screenshots, `:1227` HAR, `:1238` console log, `:1245` network log — all inside `uploadArtifacts()` (`:1171`) |
| `execution-engine/src/mobile-worker.ts` | `:102` | `:567` screenshots, `:586` video |
| `execution-engine/src/runner.ts` | `:14` | `:331` screenshots, `:346` per-case video, `:362` run video, `:372` traces — inline in a `finally` block, no helper |

Introduce `execution-engine/src/core/artifact-store.ts` with one client and one
`uploadArtifact(source, objectName, kind)` entry point; repoint all eleven. No
behaviour change, no redaction yet. This is what makes A5–A7 a single-place
change instead of an eleven-place change.

### A2. The redaction module

`execution-engine/src/core/redact.ts` — pure functions, heavily unit-tested,
no I/O:

- `redactHeaders(h)` — denylist: `authorization`, `cookie`, `set-cookie`,
  `x-api-key`, `x-auth-token`, `proxy-authorization`, `x-csrf-token`, plus
  policy-supplied extras. Case-insensitive.
- `redactBody(body, contentType)` — JSON key denylist (`password`, `passwd`,
  `token`, `secret`, `cvv`, `cvc`, `card`, `pan`, `ssn`, `aadhaar`, `otp`,
  `account_number`, `pin`) walked recursively, plus a regex sweep over string
  leaves and over non-JSON bodies: Luhn-validated 13–19 digit runs, 12-digit
  Aadhaar with Verhoeff check, email, E.164 phone, `eyJ`-prefixed JWTs.
- `redactText(s)` — same regex sweep, for `execution_log` entries and error
  messages.

Replacement token should record what was removed (`[REDACTED:pan]`) so failures
stay debuggable.

### A3. Apply at the result chokepoint

`execution-engine/src/core/job-queue.ts:400` — `JSON.stringify(result)` inside
`completeJob()` (`:386`). **Every** web result (`worker.ts:232`) and mobile
result (`mobile-worker.ts:129`) passes through this one line. Redact
immediately before serialisation and the whole result payload is covered at
once, including `response_data` (`worker.ts:727`), `network_events` (`:728`),
per-case sub-objects (`worker.ts:1005-1014`), and the mobile equivalents
(`mobile-worker.ts:254-272`).

The legacy `runner.ts` path is separate and needs its own call — the dispatch
points are `runner.ts:408` (LPUSH) and `runner.ts:417-424` (HTTP fallback),
plus the progressive per-case POST at `runner.ts:70-97`.

### A4. Redact network events at source

`execution-engine/src/core/network-interceptor.ts:28-40` builds the event
literal with `requestHeaders` (`:38`) and `responseHeaders` (`:39`) from
`allHeaders()`. Redact here so unredacted values never live past the listener.
Also the two ad-hoc request-header capture handlers for API steps at
`test-executor.ts:154-171` and `:555-563`.

### A5. Mask screenshots

Playwright supports `mask:` on `page.screenshot()`. Feed it locators from the
project policy (B). Capture sites: `worker.ts:657` (failure), `:991`
(continuous failure), `test-executor.ts:875` (explicit step), `:892`
(visual-match candidate), `mobile-worker.ts:423`/`:448`/`:319`.

Note: masking changes pixels, so a masked baseline and a masked candidate
compare correctly, but existing baselines captured unmasked will all diff on
first run after this ships. Plan a baseline re-promotion migration.

### A6. Post-process the HAR

HAR is JSON. Before upload at `worker.ts:1227`, parse and walk
`log.entries[].request.headers`, `.postData.text`, `.response.headers`,
`.content.text`, `.cookies`. Reuse A2's functions.

### A7. Traces cannot be redacted — gate them

A Playwright trace zip is a full DOM snapshot recording plus sources
(`worker.ts:504`, `runner.ts:151`, `:290` — `screenshots: true, snapshots:
true, sources: true`). There is no safe way to scrub it. The honest handling is
policy: traces only at `capture_level: full`, which requires explicit opt-in
with a UI warning. Do not claim traces are redacted.

### A8. Backend defence in depth

The worker may be an older image (the image bakes code at build time), so the
backend must not trust it. Add `backend/app/services/redaction.py` and apply at
all three ingestion paths:

| Path | Assignment sites |
|---|---|
| Redis results stream (Celery) | `app/tasks/result_aggregator.py:530-537`, `:556-559`, `:670-677`, `:693-696`; network events `:571-573`, `:706-711` |
| Legacy webhook list (Celery) | `app/tasks/webhook_tasks.py:46-55`, `:96-113` |
| HTTP webhook endpoint | `app/services/test_service.py:407-418`, `:459-473` |

Local workers (`app/api/jobs.py:64`) re-inject into the results stream, so they
are covered by the first row.

Response-side belt: `TestCaseResultRead` (`models.py:614-635`) and
`TestRunRead` (`:637-641`) expose every body and header field over the API —
that DTO pair is the single chokepoint if you want redaction on read as well as
on write.

---

## Workstream B — Capture policy (what the buyer actually asks to see)

### B1. `Project.data_policy`

`Project` (`backend/app/models.py:126-154`) already carries three nullable JSON
policy blobs on exactly this convention — `quality_gate_policy` (`:132-133`),
`ci_settings` (`:137-138`), `security_settings` (`:142-143`), each defaulting to
a module-level constant when null. Add `data_policy` the same way:

```
capture_level:      none | minimal | standard | full
store_bodies:       bool
redact_headers:     [str]      # extras beyond the built-in denylist
redact_body_fields: [str]
redact_patterns:    [str]      # named built-ins: pan, cvv, aadhaar, email, phone, jwt
mask_selectors:     [str]      # CSS selectors masked in every screenshot
retention_days:     int        # overrides the global; see G2
```

Level semantics: `none` = pass/fail and timing only; `minimal` = masked failure
screenshot; `standard` = masked screenshots + redacted bodies and network
events, no HAR, no trace, no video; `full` = everything, explicit opt-in.

### B2. Ship it to the worker

`app/worker.py:288-297` already puts `har_capture` into the job payload from
suite settings — same mechanism, add the policy blob next to it. The worker
reads it in `job-queue`/`worker.ts` and passes it into A2's functions.

### B3. Default new projects to `standard`

This is the single most important product decision in this document. Today
capture is effectively `full` with no policy at all. A default of `standard`
means a new customer cannot accidentally hoover up PII on day one, and `full`
becomes a deliberate, logged, reversible choice.

### B4. Instance-level floor

Add `MAX_CAPTURE_LEVEL` to the `policies` group of the instance-settings
REGISTRY (`app/services/instance_settings.py:126-136` — the group already holds
`MFA_REQUIRED`, `ALLOW_PRIVATE_NETWORK_TARGETS`, `OUTBOUND_ALLOWED_HOSTS`,
`RUN_RETENTION_DAYS`, and the `str` type is supported by `_parse` at `:147`).
No project may exceed the instance floor.

This is the specific control that makes a scoped payments offering possible: an
operator sets `MAX_CAPTURE_LEVEL=minimal`, and no project — however
misconfigured — can capture a HAR or a trace. That is auditable and provable,
which is what a QSA needs.

---

## Workstream D — Encryption and transport (~3-4 weeks)

- **D1. MinIO over TLS.** `app/core/storage.py:21-23` forces `http://` onto any
  scheme-less endpoint, so the internal client is always plaintext. The Node
  workers already read `MINIO_USE_SSL` (`worker.ts:153`,
  `mobile-worker.ts:105`) but `runner.ts:17` hardcodes `useSSL: false`, and the
  variable is set in no compose file.
- **D2. Server-side encryption.** `upload_fileobj` (`storage.py:98-107`)
  already builds an `ExtraArgs` dict, so `ServerSideEncryption` merges in
  cleanly. `upload_file` (`:94-96`) takes no `ExtraArgs` at all and needs a
  signature change; so does `copy_object` (`:112-119`) — miss that one and
  promoted visual baselines land unencrypted.
- **D3. Postgres TLS.** `DATABASE_URL` is consumed verbatim by one async engine
  (`app/core/database.py:9-18`) and **nine** independent sync engines, each
  repeating `.replace("+asyncpg", "")` — `app/worker.py:18`,
  `tasks/cleanup_tasks.py:12`, `analysis_tasks.py:22`,
  `notification_tasks.py:42`, `security_tasks.py:23`, `persona_tasks.py:25`,
  `tautology_tasks.py:35`, `report_tasks.py:25`, `ticket_tasks.py:24`, plus
  `services/instance_settings.py:185`. asyncpg takes `ssl=`, psycopg2 takes
  `sslmode=`, and they are not interchangeable in a query string. Add a single
  `db_url_for(sync: bool)` helper in `config.py` and repoint all ten.
- **D4. Redis TLS and auth.** `rediss://` is a URL change across six clients
  (`core/redis.py:10`, `services/job_dispatcher.py:29`, `worker.py:22`,
  `api/observability.py:32`, `api/case_generation.py:73`,
  `api/agent_ownership.py:390`) but Celery additionally needs `broker_use_ssl`
  and `redis_backend_use_ssl` dicts, which do not exist in
  `core/celery_app.py`. Separately: only the **community** compose sets
  `--requirepass` (`docker-compose.community.yml:89-101`); dev, prod, prod-1
  and distributed all run Redis with no password, and decrypted job secrets
  cross it.
- **D5. Make it fail closed.** `Settings.validate_for_deployment()`
  (`config.py:163-257`) checks secrets, CORS, and MinIO credentials but has no
  transport assertions. Add: production requires `sslmode` on `DATABASE_URL`,
  `rediss://` or a password on the broker, and https on MinIO.
- **D6. Key management.** `app/core/secrets.py` is 25 lines: an unsalted SHA-256
  of `"traceiq-project-secrets:" + SECRET_KEY`, b64'd into a Fernet key. No key
  id, no versioning, no `MultiFernet`. Consequences: rotation is destructive by
  design, and because `SECRET_KEY` also signs JWTs (`config.py:38`), JWT
  rotation and secret rotation cannot be decoupled. A silent-drop path already
  exists at `instance_settings.py:207-208`, where a rotated key makes
  admin-saved SMTP/OIDC/LLM secrets quietly revert to env values. Fix: a
  versioned envelope (`v1:<ct>`), `MultiFernet` for overlap, a `SECRETS_KEY`
  distinct from `SECRET_KEY`, and an optional KMS/Vault provider behind the same
  two exported functions — only `encrypt_secret`/`decrypt_secret` are exported,
  so the blast radius is tiny.
- **D7. `AuthSession.storage_state`** (`models.py:481-492`) holds live cookies
  and localStorage as plaintext JSON and is not routed through the secrets
  module at all. Same for `Persona.session_state`.

---

## Workstream E — Audit trail (~3 weeks)

- **E1. One helper.** `AuditLog(...)` is constructed inline at **17** sites
  (`api/endpoints/test_cases.py:66,116,244,338`;
  `test_suites.py:91,299,357,472,500`; `test_runs.py:861`;
  `schedules.py:97,261,290`; `case_revisions.py:118`; `agent_ownership.py:911`;
  `services/workspace_service.py:35,99`). Replace with
  `app/services/audit.py:record()`, and extend the model with `ip_address`,
  `user_agent`, `actor_type` (user | api_key | agent), and `prev_hash`/`hash`
  for a tamper-evident chain.
- **E2. Append-only.** Add a DB trigger rejecting UPDATE and DELETE on
  `auditlog`. Note `workspace_service.py:543-547` currently **mutates** audit
  rows (`log.workspace_id = None`) on workspace deletion — that has to become a
  tombstone.
- **E3. Cover the missing events.** None of these write audit rows today: login
  success, login failure, logout, MFA enrolment and challenge, SSO and LDAP
  login (`api/auth.py` writes nothing but `last_login_at` at `:214`), run
  deletion (`test_runs.py:556`, `:600-637` — deletes runs and MinIO artifacts
  with zero audit), API-key create and revoke (`api/api_keys.py`), role grants,
  instance-settings changes, project-secret create and delete
  (`api/environments.py`).
- **E4. Export, and close the fail-open.** The only read endpoint is
  `GET /api/audit/{entity_type}/{entity_id}` (`test_runs.py:665-691`); it
  handles only `suite` and `case` and **silently falls back to returning the
  caller's own rows** for anything else (`:686-688`) instead of 403. Replace
  with a workspace-scoped, paginated, filterable list plus CSV/JSON export for
  SIEM ingestion.
- **E5. Independent retention.** PCI wants a year retained with three months
  immediately available. Audit retention must not be coupled to
  `RUN_RETENTION_DAYS`.

---

## Workstream F — Identity (~6 weeks)

- **F1. The OIDC tenant bug — DONE.** SSO JIT and LDAP JIT both called
  `provision_standalone_user`, which creates a **new Tenant** and grants the
  user **Tenant Admin** of it, so enabling SSO for a 500-person org produced
  500 isolated, self-administered tenants. Both paths now go through
  `provision_federated_user()` / `sync_federated_access()`, governed by
  `app/services/federation.py` (instance settings group `federation`):

  - `FEDERATED_PROVISIONING_MODE` — `standalone` (the legacy behaviour, kept as
    the **default** so existing installs upgrade unchanged) / `workspace` (join
    `FEDERATED_WORKSPACE_ID` with `FEDERATED_DEFAULT_ROLE`, no tenant of their
    own) / `deny` (no JIT provisioning at all — 403 for an unknown email; the
    mode a SCIM-driven deployment will want once F2 lands).
  - `FEDERATED_GROUP_ROLE_MAP` / `FEDERATED_GROUP_TEAM_MAP` — IdP group
    mapping, **re-evaluated on every login** so losing a group in the IdP
    removes the access here. Create-only mapping would look authoritative and
    never revoke anything. With no role map configured the role is never
    overwritten, so an in-app promotion survives.
  - Group→role is restricted to `Workspace Admin` / `Workspace Member`.
    Directory groups are frequently self-service, so letting one name
    `Tenant Admin` would hand out a privilege-escalation path.
  - Misconfiguration **fails closed** (503), never a silent fall back to
    standalone — that would recreate the bug in a deployment whose admin
    believes it is fixed. `PUT /api/admin/instance-settings` validates the
    policy on save, including that the target workspace exists.
  - OIDC groups come from `OIDC_GROUPS_CLAIM` (default `groups`); LDAP reads
    `memberOf` and reduces each DN to its CN. Also fixed alongside: the SSO
    callback issued tokens for a **deactivated** account (the principal paths
    re-check `is_active` per request, so the window was small, but it was wrong).
  - Tests: 35 unit (`tests/test_federated_provisioning.py`) + 21 against a real
    Postgres (`tests/integration/test_federated_provisioning_db.py`, run via
    `backend/run-tests-live.sh`). Verified end to end over HTTP against a mock
    OIDC provider: provisioning into the target workspace with no new tenant,
    group→role and group→team, demotion on group removal, `deny` → 403,
    vanished workspace → 503 with no account created, and `standalone`
    unchanged. **Documented in `docs/ENTERPRISE_AUTH.md`.**

  Not covered, deliberately: no UI for picking the workspace from a dropdown
  (the settings screen is a flat key/value form), and no migration of the
  tenants an existing SSO deployment has already accumulated — merging tenants
  is a data-surgery problem, not a provisioning one.
- **F2. SCIM 2.0 — DONE.** `app/api/scim.py` + `app/services/scim.py`,
  migration `d9e0f1a2b3c4`. Users, Groups, filtering, PATCH, PUT, DELETE and
  ServiceProviderConfig, mounted at both `/scim/v2` and `/api/scim/v2` (the
  shipped nginx only proxies `/api/`).

  - Guarded by the `SCIM_TOKEN` instance setting, constant-time compared. Blank
    means the endpoints answer **404** — an unconfigured instance must not be an
    open provisioning API, and 404 does not advertise it either.
  - Provisions into `FEDERATED_WORKSPACE_ID` and refuses to run without it;
    every read and write is scoped to that workspace, so a SCIM credential
    cannot enumerate accounts elsewhere.
  - **Deactivation revokes live refresh tokens.** `is_active` was already
    enforced in both principal paths so access tokens died within minutes, but a
    live refresh token kept minting new ones — that was the half of the finding
    that actually left a door open.
  - `DELETE` deactivates rather than destroying: IdPs issue it routinely on
    offboarding and a hard delete would orphan runs, results and audit history
    irreversibly. Recorded as `scim_deactivate` in the append-only log with the
    IdP as the actor, which needed a new explicit `actor_type`/`actor_label`
    override in `audit.build_entry` (SCIM has no TraceIQ principal, and
    recording it as `system` makes "who removed this access" unanswerable).
  - A `POST` for an email that already exists **adopts** the account instead of
    409ing forever, so SCIM can be enabled on an instance that already has
    users, without duplicating anyone.
  - Groups map onto Teams (teams are what carry project access). Deleting a
    Group never touches its members' accounts.
  - The real work is provider disagreement: Okta sends
    `{"op":"replace","value":{"active":false}}` with no path; Entra sends
    `{"op":"Replace","path":"active","value":"False"}` — a **string**. Since
    `bool("False")` is `True`, the naive implementation silently leaves a
    deprovisioned user active. Both shapes are tested explicitly.
  - Tests: 34 unit (`tests/test_scim.py`) + 30 over real HTTP against a real
    Postgres (`tests/integration/test_scim_db.py`). Migration verified
    upgrade → downgrade → re-upgrade against a real database.

  Known interaction, documented rather than resolved: SCIM Groups and
  `FEDERATED_GROUP_TEAM_MAP` both write team membership and will fight over the
  same team. Operators pick one.
- **F3. SAML 2.0.** Zero code today (grep for `saml` returns only roadmap
  lines). A large share of insurance and banking IdPs remain SAML-first, so
  this gates those deals regardless of OIDC support.
- **F4. Separation of duties — DONE.** `app/services/proposal_policy.py`,
  migration `e0f1a2b3c4d5`.

  The finding understated it: `CaseProposal` had **no `created_by_id` column at
  all**, so there was nothing to compare `decided_by_id` against and the check
  could not have existed even in principle. The column now records the human
  behind the call — including when an API key made it, because "propose with my
  key, accept in the UI a second later" was otherwise a trivial way around the
  queue.
  - `workspace.require_separate_approver` (opt-in) refuses self-approval;
    `REQUIRE_SEPARATE_APPROVER` is an instance-level **floor** a workspace
    cannot lower. `AUTO_APPLY_DISABLED` kills policy-driven application
    instance-wide. Both are instance *settings*, not env vars, so "prove
    auto-apply is off" is a screenshot rather than trust.
  - Defaults are off. A solo user whose own agent files proposals and who then
    accepts them in the UI is a normal workflow; enforcing by default would
    break every existing install for a control nobody asked for.
  - Unattributed proposals (rows predating the column) are **not** conflicts —
    treating "we don't know" as a violation would freeze every existing queue.
  - Rejection is deliberately not gated: withdrawing your own proposal is
    harmless, and gating it would leave proposals nobody can clear.
  - The policy endpoint reports `separation_enforced` alongside the stored flag
    so the UI can show when the instance is overriding the workspace; the
    Proposals page grew a toggle and reads it.
  - Tests: 11 unit + 12 against a real Postgres.

  **Migration trap found here, worth remembering:** naming the foreign key
  explicitly made `downgrade` fail on every *fresh* install, because
  `bootstrap_db.py` builds from model metadata and SQLAlchemy names it
  `caseproposal_created_by_id_fkey` instead. Passing `None` gets the same name
  on both paths. Same family as the audit-trigger trap in `c8d9e0f1a2b3`:
  anything a migration names that metadata also creates will diverge.
- **F5. Roles — DONE (reconciled, not built out).**

  `scripts/setup_rbac.py` is **deleted** and replaced by
  `scripts/backfill_rbac.py`. The old script seeded an `org:`-scoped permission
  vocabulary that no live check asks for, so running it produced
  plausible-looking roles granting nothing — and two error messages in the
  codebase told operators to run it when a role was missing. Seeding now has one
  owner (`init_rbac`); the replacement keeps only the parts that were genuinely
  useful and idempotent: backfilling `UserProjectAccess.role_id` and
  `TeamProjectAccess.role_id` from the legacy `access_level` string, and granting
  Tenant Admin to tenant owners who have no system role. Both stale messages now
  point at `bootstrap_db.py`.

  `Role.tenant_id` is **kept, documented as reserved, and made safe**, rather
  than dropped or built out. The real problem was not that the column was dead —
  it was that `rbac_service.get_role_by_name` matched on **name alone and
  returned whichever row came back first**. Every grant site looks up
  "Workspace Admin" by name, so the first tenant-scoped role with that name
  could have been granted to another tenant's workspace owner. A bare lookup now
  considers system roles only; passing `tenant_id` prefers that tenant's role
  and falls back to the system one. 7 tests against a real Postgres, including
  the shadowing case and cross-tenant invisibility.

  Building tenant-scoped roles properly (CRUD, a permission picker, and passing
  `tenant_id` at every grant site) is parked in `SCOPE_NOTES.md` — it is a
  feature, not cleanup.

---

## Workstream G — Deletion and residency — DONE (2026-08-10)

- **G1. Real workspace/tenant purge — done.** `app/services/purge.py`.
  `delete_workspace` deleted the workspace row and its teams; 39 tables of
  customer data and every MinIO object survived as orphans — invisible through
  the API, still there, still the wrong answer to "have you deleted our data?".
  `DELETE /api/workspaces/{id}` now purges the whole tree behind a typed
  confirmation (`confirm=<workspace name>`), with `dry_run=true` reporting what
  would go, and returns per-table row counts.

  The guarantee is not the code, it is
  `tests/test_purge_plan.py`: it walks the foreign-key graph in
  `SQLModel.metadata` and **fails if any table that can reach `workspace` is
  neither in `PURGE_PLAN` nor in `PURGE_EXEMPT` with a stated reason.** A
  hand-written list would have rotted on the next feature; this cannot. Object
  keys are collected *before* the rows are deleted — they are derived from run
  and build ids, so the ordering is the difference between a purge and a purge
  that leaves every video in the bucket.

  `auditlog` is exempt, deliberately: append-only by trigger, no FK to
  workspace, and its whole value is outliving what it describes.

- **G2. Per-project retention — done.** `app/services/retention.py`.
  `Project.data_policy.retention_days` was scaffolding nothing read, so a project
  whose data-policy screen said "keep runs 30 days" kept them forever — the screen
  was a false statement to whoever read it in a security review. `purge_old_runs`
  now resolves the window per project (once per project, not per run) and the
  **shorter** of project/instance always wins: an instance-wide setting is a
  ceiling, not a suggestion. 0 / absent / unparseable means keep forever, never
  delete-now — a negative value would put the cutoff in the future and empty the
  table, so it is treated as unset.

  Two new sweeps: `purge_derived_records` (`DERIVED_RETENTION_DAYS`) expires
  `TestCaseRevision` snapshots, `LLMUsageEvent` rows and orphaned `FlakeRecord`s
  — always keeping the newest revision per case so restore still works — and
  `purge_orphaned_artifacts` (`ARTIFACT_ORPHAN_SWEEP_ENABLED`, **report-only by
  default**) deletes objects whose owning row is gone. Only `runs/` was ever
  cleaned; `baselines/` and app binaries leaked permanently.
  `storage.delete_prefix` is now paginated — the old code capped at
  `list_objects_v2`'s 1000 keys and silently left the rest, which is worse than
  failing.

- **G3. Erasure that actually erases — done.** `app/services/erasure.py`.
  `DELETE /api/auth/me` scrubbed the `users` row and revoked refresh tokens;
  account tokens, MFA secret and recovery codes, notification settings and the
  API keys the person had minted all survived. Now: those are erased, the SCIM
  external id is cleared (otherwise the next directory sync recognises it and
  resurrects the account), and their API keys are **revoked** — a live credential
  belonging to a deleted human is a standing liability with nobody left to rotate
  it.

  The endpoint returns a three-part report: erased / retained de-identified /
  retained with reason. Authorship columns (`TestCase.created_by_id`,
  `TestRun.user_id`, …) are the *customer's* business records and stay pointing
  at the scrubbed row — the id survives, the identity does not. Deleting them
  would destroy a customer's test history because an employee left, which would
  be a worse bug than the one being fixed. The audit trail is retained under the
  legal-obligation basis and bounded by `AUDIT_RETENTION_DAYS`; the erasure
  record itself deliberately does not contain the email.

  A data-protection officer needs a defensible statement of scope, not a promise
  that nothing survived anywhere.

- **G4. Residency — documented, not engineered.** `docs/DATA_RESIDENCY.md`.
  One instance per jurisdiction, stated as the supported topology with its
  trade-offs (account per instance, no cross-region reporting) rather than
  pretending per-tenant routing exists. Also documents that hosted-LLM failure
  analysis leaves the instance, and how to keep it inside the network.

Tests: 22 unit (purge completeness) + 14 unit (retention selection) + 11 live
(purge execution, audit survival, chain still verifies) + 16 live (erasure
boundary in both directions).

---

## Workstream H — Operability

- **H1. Beat HA.** Exactly one `celery_beat` with no leader election, using the
  default file-backed `PersistentScheduler` on a container-local path
  (`docker-compose.community.yml:188-199`). It drains `jobs:results` every 2s
  (`core/celery_app.py:60-63`), so if it dies the entire execution pipeline
  stalls silently — no finalization, no aggregation, no schedules, no retention,
  no alert. `redbeat` is a drop-in `beat_scheduler` backed by a Redis lock; a
  pg advisory lock wrapper is the alternative.
- **H2. DLQ replay and alerting.** `core/job-queue.ts:370-373` dead-letters
  after three retries; `:459-466` only `console.error`s every hundred loop
  iterations. Dead jobs sit forever with no requeue path. Add a replay endpoint
  and surface `dead_letter_depth` (already computed in
  `api/observability.py:170`) as an alertable metric.
- **H3. A real initial migration.** The baseline `1f266105057e` is an empty
  `pass` stub with `down_revision = None`; `init_db()` has `create_all`
  commented out (`core/database.py:31-35`); the entrypoint shells out to
  `scripts/bootstrap_db.py` instead (`docker-entrypoint.sh:51-57`). Net effect:
  schema truth lives in the SQLModel definitions rather than the migration
  chain, and **there is no trustworthy rollback for a failed upgrade**. That is
  a hard blocker in any change-controlled environment. Also: three merge points
  with tuple `down_revision`s, and no advisory lock guarding `RUN_MIGRATIONS`
  across replicas.
- **H4. Observability.** `/metrics` exists and is real but nothing scrapes it —
  no Prometheus config, no Grafana dashboard, no alert rules anywhere in
  `infrastructure/`. No tracing at all (zero OTel), so a slow run must be
  correlated across three services by hand. No structured logging — stdlib
  `logging` plus raw `print()` in hot paths (`job_dispatcher.py:118`, all of
  `cleanup_tasks.py`, `llm_usage.py:145`). No error tracking.
- **H5. Helm chart / K8s manifests.** Compose only today.

---

## Workstream I — Proving it (the part buyers actually check)

- **I1. Run the tests.** 80 `def test_` functions exist across
  `backend/tests/`; CI (`.github/workflows/ci.yml:33-37`) runs **two files, 18
  tests**. It excludes pure-unit files that need no live stack
  (`test_impact_analysis_v2.py`, `test_case_proposal_apply.py`,
  `test_stale_run_detection.py`, `test_instance_settings.py`,
  `test_result_case_link.py`). Frontend CI is `npm run build` only — no tests.
- **I2. Convert the `verify_*.py` scripts.** Eight of the 22 files in
  `backend/tests/` contain zero test functions — they are ad-hoc manual
  scripts. Critically, **every RBAC and multi-tenant-isolation check is in that
  category**, so the isolation guarantees are entirely unverified by CI. Given
  that tenant isolation is application-layer only (no RLS, no `CREATE POLICY`
  anywhere, one shared bucket), these need to be real, running tests.
- **I3. A redaction test corpus.** Build a fixture set of realistic payloads —
  card numbers, Aadhaar, JWTs, session cookies, health fields — and assert none
  survive any of the three ingestion paths. This is the artifact you hand a
  buyer's security team, and it is worth more than any doc in this repo.
- **I4. CI hygiene.** Coverage gate (`pytest-cov` is already in
  `requirements-dev.txt:9` but unwired — no `--cov`, no `.coveragerc`, no
  threshold), plus `pip-audit`, `npm audit`, SAST, and secret scanning. None
  exist today.
- **I5. Third-party pen test, then SOC 2 Type II.** Calendar-bound; start the
  clock as soon as D and E land.

---

## Suggested sequencing

| Phase | Contents | Est. |
|---|---|---|
| 1 | C1–C4, A1 | 2-3 weeks |
| 2 | A2–A8, B | 4-6 weeks |
| 3 | D, E | 6-8 weeks |
| 4 | F | 6 weeks |
| 5 | G, H, I | ongoing, parallelisable |
| 6 | Pen test → SOC 2 Type II | 2-4 quarters calendar |

Phases 1 and 2 together are what convert TraceIQ from "cannot be used where
PII exists" to "can be used anywhere except a cardholder data environment."
That is the highest-leverage ~2 months of work available in this codebase.
