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
| Reviewed, tested changes | Built | CI: 500+ unit tests, integration job on real Postgres/Redis/object store (the pinned RustFS image), migration round-trip, coverage gates on security modules, Helm lint/kubeconform | CI history; branch protection (**yours**) |
| Schema change safety | Built | Migrations are the only path (squashed root, legacy bridge); replicas serialised by advisory lock; `downgrade base` verified | `scripts/verify_migrations.py` in CI |
| Agent-made changes reviewed | Built | Case proposals queue; auto-apply threshold; `AUTO_APPLY_DISABLED` instance switch; suite-settings changes never auto-applied | proposal audit rows; instance settings |
| Reproducible releases | Built | Pinned base images, pinned dependencies, published images by tag | release workflow; image digests |

## A1 / PI / C1 / P — availability, processing integrity, confidentiality, privacy

| Control | Status | Implementation | Evidence |
|---|---|---|---|
| Availability of scheduler | Built | Beat heartbeat with TTL (`/health/beat`), RedBeat option, DLQ replay | metrics `traceiq_beat_healthy`, alert rules |
| Backup / restore | Yours (backups) / Built (drill) | Postgres + object store are standard; `docs/OPERATIONS.md` prescribes snapshot before upgrade; `scripts/restore_drill.sh --report-to` records each restore test | your backup logs; restore-drill history (Controls, evidence pack) |
| Data deletion | Built | Workspace purge cascades 39 tables + object prefixes (`tests/test_purge_plan.py` walks the FK graph); per-project retention; orphan sweep report-only by default | purge dry-run output; retention task logs |
| Right to erasure | Built | `DELETE /api/auth/me`: erased / retained-de-identified / retained-with-reason | erasure audit row (no email) |
| Data residency | Yours | Single-region deployment; `docs/DATA_RESIDENCY.md` | your deployment record |
| Tenant isolation | Built (application layer) | Every boundary is a code check — no RLS, one bucket; covered by `test_tenant_isolation_db.py` | CI |

## Controls and evidence packs in the product

The rows above are also available as **editable data** per workspace: on the
Controls page a workspace admin loads the "SOC 2 starter set" (idempotent —
loading it again only adds controls that are missing), assigns owners and
adjusts evidence sources and freshness. Each control is evaluated live from
the product's own data (access reviews, audit chain, data policies, finding
SLAs, restore drills, MFA, SSO, API-key expiry) plus manual evidence, and is
passing / failing / stale / excepted / not assessed. Exceptions need a second
admin to approve them and expire within 365 days.

The **compliance evidence pack** (Evidence packs page) is one zip for a
workspace and a date range: the audit-log CSV and the chain verification,
access-review exports, controls with status and exceptions, effective data
policies, SARIF per project with the finding-SLA summary, and restore-drill
history — with a signed `manifest.json` (SHA-256 of every file, HMAC-SHA256
with a key derived only for this purpose). Upload a pack on the same page to
check nothing in it was changed.

## What an auditor will ask that is not in the code

1. **Who has instance-admin?** `GET /api/admin/instance-admins`; keep the list
   short and the grants audited (they are).
2. **Is MFA actually on?** Screenshot of `MFA_REQUIRED` in Instance settings,
   dated; plus the audit rows showing enrolments.
3. **Show me the last 90 days of access reviews.** A product feature since
   WP7: the Access reviews page runs a campaign (every person and API key,
   keep/revoke with a note, sign-off recorded in the audit trail) and exports
   it as CSV and a printable HTML report. The CC6.3 control's
   "access review signed off within 90 days" source tracks whether one is due,
   and the evidence pack includes every campaign in the period.
4. **Show me a restore test.** Run `scripts/restore_drill.sh` quarterly with
   `--report-to <TraceIQ URL>` (API key from an instance admin): each result
   is stored as restore-drill history, evidences the A1.2 control ("restore
   drill passed within 90 days") and is included in the evidence pack.
5. **Show me the pen test.** `docs/SECURITY_ASSESSMENT.md` is the internal
   pre-assessment; the external test is scheduled by you (scope there).
6. **Patch cadence.** The advisory CI jobs list what is outstanding; the
   assessment doc records what was accepted and why (e.g. the Starlette 1.x
   advisories need a FastAPI major bump).

## Type II readiness — the honest position

Product controls: **in place** for every row marked Built or Opt-in above.
Evidence layer: audit trail, metrics, logs and CI exist; **the sampling
routines** (quarterly access review, quarterly restore drill, ticketed
vulnerability triage) are tracked by the product's controls but have to be run
on a calendar by people. Start the
observation clock only once those run on a calendar, because the auditor
samples the window, not the code.
