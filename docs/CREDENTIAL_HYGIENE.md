# Credential hygiene — what was ever committed, what was done about it

Workstream C5 of `info/REGULATED_READINESS.md`. Written 2026-09-07 for the
security questionnaire question *"Have credentials ever been committed to
version control?"* The honest answer is **yes, historically**; this page is the
evidence of what, and of the remediation.

## What was in git history

Scanned with gitleaks over all 20 branches (324 commits) plus a manual
extraction of every version of every environment file ever tracked.

| Material | Where | Exposure |
|---|---|---|
| `dump.sql` (7.6 MB Postgres dump, Dec 2025) | repo root, tracked until 2026-08-11 | 1 user row with a bcrypt password hash; 39 test-run rows whose target URLs embed **three third-party `appKey` values** (the application under test, not TraceIQ) |
| `infrastructure/.env` (7 versions) | tracked until 2026-08 | dev secrets, and for a period the **production** `SECRET_KEY`, Postgres and MinIO credentials of the `*.thehindu.co.in` deployment |
| `backend/.env` (3 versions) | tracked | dev `DATABASE_URL` / MinIO credentials (localhost values) |
| `frontend/.env`, `.env.dev`, `.env.production` | tracked | API base URLs only (no secrets) — removed for hygiene |

Gitleaks also flags five strings in `backend/tests/` and
`execution-engine/src/core/redact.test.ts`: those are the redaction test corpus
(synthetic JWTs and keys) and are intentional.

## Rotation (must precede the rewrite — done first)

| Credential | Status |
|---|---|
| Running local stack (`~/traceiq-test/.env`) | **Not exposed.** Compared value-by-value against every historical value: zero matches. |
| Dev compose stack (`infrastructure/.env`, `backend/.env`) | **Rotated 2026-09-07.** New `SECRET_KEY`, `WEBHOOK_SECRET`, Postgres and MinIO credentials written; the new Postgres password applied to the existing dev volume (`ALTER ROLE`); the dev database held no encrypted secrets, so the `SECRET_KEY` change lost nothing. Pre-rotation copies in `~/traceiq-history-backup/*.pre-rotation` (mode 600) — delete once the dev stack is confirmed working. Note `backend/.env` must stay world-readable (644): `run-tests.sh` mounts the tree into a container running as uid 10001 and pydantic-settings opens `.env` at import, so a 600 file breaks every test with `PermissionError`. |
| `infrastructure/env.prod` (untracked local file) | Leaked values replaced with `CHANGE-ME-…` placeholders that production refuses to boot on. |
| Leaked user password hash | The same account exists on the local stack with a **different** hash. If that password was ever reused elsewhere, change it there. |
| **Production `*.thehindu.co.in` credentials** | **YOUR ACTION.** Rotate `SECRET_KEY` (invalidates sessions; set `SECRETS_KEY` first if stored secrets exist — see `docs/OPERATIONS.md`), the Postgres role password and the MinIO root credentials on that deployment. On 2026-09-07 the three hostnames resolved in DNS but did not answer HTTPS from this network, so the deployment may already be gone; rotate anyway if the database or bucket still exists anywhere. |
| **Three `appKey` values** of the application under test | **YOUR ACTION**, on that application's side. They are not TraceIQ credentials. |

## History rewrite

Done with `git filter-repo` on a fresh `--mirror` clone of the GitHub
repository:

- removed from every commit on every branch: `dump.sql`, `backend/.env`,
  `infrastructure/.env`, `frontend/.env`, `frontend/.env.dev`,
  `frontend/.env.production`;
- replaced, wherever else they appeared (e.g. `infrastructure/env.prod`
  history): the two `SECRET_KEY` values, the production Postgres/MinIO
  credentials, the three `appKey` values and the bcrypt hash → `***REMOVED-BY-HISTORY-REWRITE***`.

Verification on the rewritten repository: 0 occurrences of the removed paths in
any commit; 0 occurrences of the 11 secret strings in any blob; gitleaks reports
only the five test-corpus strings; **every branch's tree is byte-identical to
the original minus exactly the removed files** (main and 18 older branches had
`dump.sql`/`.env` files at their tips; `feature/enterprise-auth-ai` did not).
324 commits became 270 — commits that only touched removed files vanish.

### State at the time of writing

The rewritten repository is prepared but **not yet pushed**: the force-push is
the one step that requires a human decision, because it rewrites every commit
id on the shared remote. Pre-rewrite history is preserved in full:

```
~/traceiq-history-backup/TraceIQ-pre-rewrite-2026-09-07.bundle   # git bundle --all of the remote, before
~/traceiq-history-backup/TraceIQ-rewritten.git                    # the filter-repo result (bare)
```

To complete the rewrite:

```bash
# 1. If `main` is protected on GitHub, temporarily allow force pushes
#    (Settings → Branches → main → "Allow force pushes"), then:
cd ~/traceiq-history-backup/TraceIQ-rewritten.git
git remote add origin git@github-work:raja-9679/TraceIQ.git   # if not present
git push --mirror origin

# 2. Re-point the working clone (its history is the OLD one). Tree is clean.
cd ~/Work/repos/TraceIQ
git fetch origin --prune
git checkout -B feature/enterprise-auth-ai origin/feature/enterprise-auth-ai
git branch -f main origin/main
git reflog expire --expire=now --all && git gc --prune=now

# 3. Every other clone must re-clone (or repeat step 2). Pull will not work.
```

Then ask GitHub Support to purge cached views of the old commits — rewritten
objects remain fetchable by SHA until GitHub garbage-collects them, and the
repository's PR/commit pages may still render them. Until that is done, treat
the old SHAs as still disclosed; the rewrite stops *future* clones from carrying
the material, it does not un-disclose it. The bundle keeps a copy for forensic
purposes; delete it when no longer needed.

## Preventing a recurrence

Already in place: `.gitignore` covers `.env*` (except `*.example`) and `*.sql`
dumps; `backend/.dockerignore` and `frontend/.dockerignore` keep local env files
out of images; CI asserts the community compose file refuses to render without
secrets. Added with this work: a gitleaks scan should run in CI on every push
(`zricethezav/gitleaks` action, `--redact`); the five test-corpus strings need a
`.gitleaksignore` (fingerprints from the report) rather than a rule exception.
