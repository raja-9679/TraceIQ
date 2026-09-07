# SOC 2 — control matrix and evidence map

Companion to `info/I18N_SOC2_IMPLEMENTATION_PLAN.md` (the plan) and
`info/REGULATED_READINESS.md` (what was built). This page is what you hand an
auditor or a prospect's security team: for each Trust Services Criterion that
TraceIQ's *product* controls can speak to, where the control is implemented,
how it is switched on, and where the evidence comes from. Organisational
controls (HR, vendor management, risk assessment, BCP) are the operating
company's and are out of scope here.

A Type II report needs an **observation window** (typically 3–12 months) during
which these controls demonstrably operate. Nothing below produces a report on
its own; it produces the evidence a CPA firm samples.

## Status legend

- **Built** — in the code, on by default or one setting away.
- **Opt-in** — in the code, must be enabled and *shown* enabled (instance
  settings are DB-backed so a screenshot is evidence; env vars are not).
- **Yours** — organisational or deployment-specific; TraceIQ provides hooks only.

## CC6 — Logical and physical access

| Control | Status | Implementation | Evidence |
|---|---|---|---|
| Unique accounts, authenticated | Built | JWT sessions + refresh-token rotation with family revocation (`app/api/auth.py`); API keys hashed at rest, prefix-only display | audit rows `auth.login`/`auth.logout`/`api_key.*`; `GET /api/workspaces/{id}/audit` |
| MFA | Opt-in | TOTP + recovery codes; `MFA_REQUIRED` forces enrolment before a session is issued (`docs/ENTERPRISE_AUTH.md`) | instance-settings screenshot; audit `auth.mfa_enrolled` |
| SSO / federation | Opt-in | OIDC, **SAML 2.0**, LDAP; JIT provisioning per `FEDERATED_PROVISIONING_MODE`; group→role/team maps re-applied every login | instance settings; audit `auth.login` with `method` |
| SSO-only mode | Opt-in | `PASSWORD_LOGIN_DISABLED` (instance admins keep break-glass) | instance settings |
| Provisioning / deprovisioning | Opt-in | SCIM 2.0 (`/scim/v2`); deactivation revokes live refresh tokens; `is_active` checked on every JWT and API-key request | audit rows with `actor_type=scim`, `actor_label=<IdP>` |
| Role-based authorization | Built | Workspace/Team/Project RBAC, `access_service.py`; viewer/editor/admin minimums per endpoint | `tests/integration/test_tenant_isolation_db.py` in CI; RBAC assignment audit rows |
| Separation of duties | Opt-in | `REQUIRE_SEPARATE_APPROVER` (instance floor) / per-workspace flag: the author of an agent proposal cannot accept it | proposal-policy endpoint returns `separation_enforced`; audit `proposal.accept` with author ≠ approver |
| Secrets at rest | Built | Fernet ring (`SECRETS_KEY`, `SECRETS_KEY_PREVIOUS`), `scripts/rotate_secrets.py`; stored browser sessions encrypted | `tests/test_secrets_envelope.py`; startup `[config]` advisories |
| Least privilege for machine access | Built | API keys scoped to a workspace and optionally a project; worker shared secret; metrics bearer token; DLQ payloads never echoed | `api_keys.py`, `jobs.py`, `dead_letter.py` |
| Transport security | Opt-in | `REQUIRE_TRANSPORT_SECURITY` turns plaintext Postgres/Redis/S3 into a boot refusal; `sslmode` preserved for asyncpg | boot log; Helm `config.requireTransportSecurity` |
| Outbound network control | Built | `net_guard.py` validates every user-supplied URL (SSRF), `ALLOW_PRIVATE_NETWORK_TARGETS` off by default | `tests/test_net_guard.py`; `/metrics` `traceiq_*` counters |

## CC7 — System operations (monitoring, incidents)

| Control | Status | Implementation | Evidence |
|---|---|---|---|
| Audit trail, tamper-evident | Built | Append-only trigger + hash chain (`app/services/audit.py`); `/audit/verify` | verify endpoint output; `tests/test_audit_chain.py`; DB trigger present (`verify_migrations.py`) |
| Audit retention | Opt-in | `AUDIT_RETENTION_DAYS` separate from run retention; default forever | instance settings |
| Security monitoring | Opt-in | Prometheus `/metrics` + `infrastructure/monitoring/alerts.yml`; `/health/beat`; structured JSON logs with request/trace ids; OpenTelemetry; Sentry | Grafana dashboard; alert history; log shipper retention |
| Vulnerability management | Built (advisory) / Yours | CI: `pip-audit`, `npm audit`, Trivy-able images; `docs/SECURITY_ASSESSMENT.md` records the 2026-09-07 baseline and fixes | CI runs; assessment doc; your patch cadence |
| Secret leakage prevention | Built | CI `secret-scan` (gitleaks, full history); `.dockerignore`; no default secrets in compose or Helm (CI asserts) | CI runs; `docs/CREDENTIAL_HYGIENE.md` |
| Data capture minimisation | Built | Per-project `data_policy`, instance `MAX_CAPTURE_LEVEL` ceiling; redaction at capture and ingestion (PAN/Aadhaar/JWT, header denylist) | `tests/test_redaction.py` (+ TS twin); project policy screen shows `clamped` |
| Incident response hooks | Yours | Outbound webhooks (HMAC-signed), Slack/Teams/email, Sentry | your runbook |

## CC8 — Change management

| Control | Status | Implementation | Evidence |
|---|---|---|---|
| Reviewed, tested changes | Built | CI: 500+ unit tests, integration job on real Postgres/Redis/MinIO, migration round-trip, coverage gates on security modules, Helm lint/kubeconform | CI history; branch protection (**yours**) |
| Schema change safety | Built | Migrations are the only path (squashed root, legacy bridge); replicas serialised by advisory lock; `downgrade base` verified | `scripts/verify_migrations.py` in CI |
| Agent-made changes reviewed | Built | Case proposals queue; auto-apply threshold; `AUTO_APPLY_DISABLED` instance switch; suite-settings changes never auto-applied | proposal audit rows; instance settings |
| Reproducible releases | Built | Pinned base images, pinned dependencies, published images by tag | release workflow; image digests |

## A1 / PI / C1 / P — availability, processing integrity, confidentiality, privacy

| Control | Status | Implementation | Evidence |
|---|---|---|---|
| Availability of scheduler | Built | Beat heartbeat with TTL (`/health/beat`), RedBeat option, DLQ replay | metrics `traceiq_beat_healthy`, alert rules |
| Backup / restore | Yours | Postgres + object store are standard; `docs/OPERATIONS.md` prescribes snapshot before upgrade | your backup logs |
| Data deletion | Built | Workspace purge cascades 39 tables + object prefixes (`tests/test_purge_plan.py` walks the FK graph); per-project retention; orphan sweep report-only by default | purge dry-run output; retention task logs |
| Right to erasure | Built | `DELETE /api/auth/me`: erased / retained-de-identified / retained-with-reason | erasure audit row (no email) |
| Data residency | Yours | Single-region deployment; `docs/DATA_RESIDENCY.md` | your deployment record |
| Tenant isolation | Built (application layer) | Every boundary is a code check — no RLS, one bucket; covered by `test_tenant_isolation_db.py` | CI |

## What an auditor will ask that is not in the code

1. **Who has instance-admin?** `GET /api/admin/instance-admins`; keep the list
   short and the grants audited (they are).
2. **Is MFA actually on?** Screenshot of `MFA_REQUIRED` in Instance settings,
   dated; plus the audit rows showing enrolments.
3. **Show me the last 90 days of access reviews.** Not a product feature —
   export `/api/workspaces/{id}/audit/export` monthly and sign off on it.
4. **Show me a restore test.** Not a product feature — do one per quarter.
5. **Show me the pen test.** `docs/SECURITY_ASSESSMENT.md` is the internal
   pre-assessment; the external test is scheduled by you (scope there).
6. **Patch cadence.** The advisory CI jobs list what is outstanding; the
   assessment doc records what was accepted and why (e.g. the Starlette 1.x
   advisories need a FastAPI major bump).

## Type II readiness — the honest position

Product controls: **in place** for every row marked Built or Opt-in above.
Evidence layer: audit trail, metrics, logs and CI exist; **the sampling
routines** (monthly access review export, quarterly restore test, ticketed
vulnerability triage) are organisational and have not started. Start the
observation clock only once those run on a calendar, because the auditor
samples the window, not the code.
