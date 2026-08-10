# Data residency and deletion

What TraceIQ does with customer data, where it lives, and how it goes away.
Written for the person filling in a security questionnaire — every claim here is
backed by code or by a test, and the things that are *not* true are stated as
such.

## Residency: one instance per jurisdiction

**TraceIQ has no per-tenant region routing, and building it is not planned.**

There is no `region` column anywhere in the schema. One Postgres, one Redis and
one object-storage bucket serve every tenant on an instance. Adding per-tenant
routing would mean threading a region through every query, every Celery task and
every artifact key — a rewrite, not a feature.

The supported answer is a **deployment topology**: run one self-hosted instance
per jurisdiction.

```
  eu.traceiq.yourco.com      →  Postgres (eu-west-1), MinIO (eu-west-1)
  in.traceiq.yourco.com      →  Postgres (ap-south-1), MinIO (ap-south-1)
  us.traceiq.yourco.com      →  Postgres (us-east-1), MinIO (us-east-1)
```

Each instance is a complete TraceIQ: its own database, object store, workers and
instance settings. Nothing is shared between them, so data cannot cross a border
by accident — which is a stronger guarantee than application-level routing, not a
weaker one. The trade-offs are real and worth stating to a buyer: users need an
account per instance, and there is no cross-region reporting.

This is a deliberate architectural position, recorded in
`info/REGULATED_READINESS.md` under G4.

### Where data physically sits within one instance

| Data | Store |
|---|---|
| Suites, cases, runs, results, audit log | Postgres |
| Screenshots, video, traces, HAR, app binaries | Object storage (MinIO or S3) |
| Job queue, live run state, rate limits | Redis |
| Secrets (project secrets, provider keys, session state) | Postgres, encrypted (`app/core/secrets.py`) |

Execution workers hold data only for the duration of a run. Browser profiles are
ephemeral; nothing is written outside the container's temp directory.

**LLM calls leave the instance** if you configure a hosted provider. Failure
analysis sends the failing step, its error and (at `full` capture) trace
excerpts to whichever provider is configured. For a deployment that cannot allow
that, point `LLM_PROVIDER` at a self-hosted Ollama or an OpenAI-compatible
endpoint inside your own network, or leave AI disabled — the product works
without it.

## What is captured in the first place

Retention is the second line of defence. The first is not capturing the data:
see `Project.data_policy` (`docs`/`info/REGULATED_READINESS.md` workstream B).
`capture_level` defaults to `standard`, and **video, traces and HAR require
`full`** because none of them can be meaningfully redacted — a trace is a
complete DOM recording. Credentials and PII are redacted twice, once at capture
time in the worker and again at ingestion in the backend, because a worker image
can be older than the backend.

## Retention

Nothing is deleted by default. Every window below is off until an operator sets
it, which is the right default for a test system — but it means "how long do you
keep our data?" answers "until you tell us otherwise" out of the box.

| Setting | Governs | Default |
|---|---|---|
| `Project.data_policy.retention_days` | Runs, results and their artifacts, per project | unset |
| `RUN_RETENTION_DAYS` | Instance-wide ceiling for the above | unset |
| `AUDIT_RETENTION_DAYS` | The audit log | keep forever |
| `DERIVED_RETENTION_DAYS` | Case revisions, LLM usage events, orphaned flake records | keep forever |
| `ARTIFACT_ORPHAN_SWEEP_ENABLED` | Object-storage rows whose owning record is gone | off |

**The shorter window always wins.** A project may ask for less than the
instance-wide setting but never more: the instance setting is a ceiling an
operator imposes, not a suggestion (`app/services/retention.py`).

**Audit retention is deliberately separate** from artifact retention. PCI DSS
Requirement 10 wants a year of audit history regardless of how long you keep
test videos, and coupling them would mean shortening one to save disk silently
shortened the other.

**The orphan sweep defaults to report-only.** A job keyed on "the database does
not mention this object" deletes live customer artifacts if its reachability
query is wrong, so read a report before enabling deletion.

## Deleting a workspace

`DELETE /api/workspaces/{id}` purges the workspace and everything reachable from
it: projects, suites, cases, runs, results, secrets, personas, visual baselines,
app builds, webhooks, API keys, teams, memberships, invitations, proposals,
revisions, LLM usage events, and the object-storage prefixes for all of it.

It is irreversible, so it requires typing the workspace name:

```bash
# See what would go, without touching anything
curl -X DELETE "$API/workspaces/42?dry_run=true" -H "Authorization: Bearer $TOKEN"

# Actually do it
curl -X DELETE "$API/workspaces/42?confirm=Acme%20Production" -H "Authorization: Bearer $TOKEN"
```

Both return a per-table row count, so the caller gets a record of what was
removed.

**The audit log is retained.** It is append-only by database trigger, carries no
foreign key to the workspace, and its purpose is to outlive the objects it
describes — "what happened in the workspace that was deleted" is a question an
auditor asks. Audit rows age out under `AUDIT_RETENTION_DAYS` instead.

Completeness is enforced by a test, not by review:
`backend/tests/test_purge_plan.py` walks the foreign-key graph and fails if any
table that can reach a workspace is neither purged nor explicitly exempt with a
stated reason. A table added by a future feature cannot silently leak.

## Erasing a user (GDPR Art. 17)

`DELETE /api/auth/me`. Returns a report in three parts, because what a
data-protection officer needs is a defensible statement of scope rather than a
blanket assurance.

**Erased** — email, name and password hash scrubbed; TOTP secret and recovery
codes destroyed; SCIM external id cleared (otherwise the next directory sync
recognises it and resurrects the account); account tokens, notification settings
and refresh tokens deleted. API keys the person minted are **revoked** — a live
credential belonging to someone with no account is a standing liability, and
nobody is left to rotate it.

**Retained, de-identified** — authorship on the workspace's own records
(`TestCase.created_by_id`, `TestRun.user_id`, and the rest). These belong to the
customer, not the individual. The id survives so "who last edited this" still
works; the row it points at no longer names anyone. Deleting them would destroy
a customer's test history because an employee left.

**Retained deliberately** — the audit trail. It cannot be rewritten (append-only
trigger) and its `actor_label` holds the email as it was at the time. It is kept
under the legal-obligation basis and bounded by `AUDIT_RETENTION_DAYS`, not by an
erasure request. The erasure record itself deliberately does not contain the
address.

The account is soft-deleted rather than row-deleted: it is referenced as
owner/creator across tenants, suites, cases and runs, so a hard delete would
either cascade into a customer's history or leave dangling references.

**A tenant owner cannot be erased out from under their tenant.** A tenant must
have an owner; transfer ownership first if the organisation is to outlive the
person.

## Deprovisioning from your directory

See `docs/ENTERPRISE_AUTH.md`. SCIM `active:false` (or `DELETE`) deactivates the
account **and revokes its live refresh tokens** — `is_active` is re-checked on
every request in both the JWT and API-key paths, so access ends immediately
rather than at the next token expiry.
