# Brief for agents building a roadmap work package

You are implementing ONE work package (WP) from `info/ROADMAP_EXECUTION_2026-10.md`
in the TraceIQ repo. Read `CLAUDE.md` (repo root) first — it is long but it is
the map of the codebase and lists the traps. `info/CODEBASE.md` has the data model.

## Where you work
- You are given a git worktree path and a branch name (made by the integrator
  with `git worktree add -b feat/<wp> ../TraceIQ-<wp> <base>`). Work ONLY there.
- Never touch: the main checkouts (`TraceIQ` on `feature/enterprise-auth-ai`,
  `TraceIQ-redesign` on `feature/redesign-a`, both siblings of your worktree),
  other `TraceIQ-wp*` worktrees, the owner's running stack (`~/traceiq-stack`,
  containers `traceiq-*`) beyond what the test scripts do. Never `git push`,
  `pull`, `merge` from origin, rebase onto origin, or change git config. Never
  edit `.claude/` or settings, `info/HANDOFF.md` or the tracker (the
  integrator does those).
- Node deps: the integrator symlinks `frontend/node_modules` and
  `execution-engine/node_modules` from the main checkouts. Never commit them
  (`git add` explicit paths). Don't `npm install` into them (shared); install
  into a scratch copy if you need a new package, and update the lockfile
  there (`--package-lock-only`).

## How to test
- Backend tests build their own image from the checkout on first use
  (`backend/scripts/test-image.sh`, tag keyed by requirements.txt) — no setup.
- Backend unit: `cd backend && ./run-tests.sh -q` (whole suite) or a file.
  Baseline: no failures, no errors (`tests/e2e` skips unless `TRACEIQ_E2E_API_URL` is set).
- Backend with real Postgres: `cd backend && TRACEIQ_SCRATCH_DB=<unique_name> ./run-tests-live.sh tests/integration/<file>.py -q`
  ALWAYS set `TRACEIQ_SCRATCH_DB` to a name unique to your WP (e.g. `traceiq_wp3`)
  — other agents run in parallel. New integration tests go in
  `tests/integration/`, self-skip without `TRACEIQ_LIVE_DB`, follow
  `tests/integration/test_flake_owners_db.py` for the fixture pattern.
- Migrations: `TRACEIQ_SCRATCH_DB=<unique>_mig ./run-tests-live.sh --migrations`
  must end with "all migration checks passed".
- Engine: `cd execution-engine && npm test` and `npx tsc --noEmit -p .`
- Frontend: `cd frontend && npx tsc --noEmit -p tsconfig.app.json && npx eslint <your files> && npm test`

## Rules that bite (from CLAUDE.md — do not skip)
- Migrations: one new file in `backend/app/alembic/versions/`, use the revision
  id and `down_revision` you are given (the head moves as WPs merge).
  Unnamed FKs (`op.create_foreign_key(None, ...)`), `op.f('ix_...')` index names
  matching `index=True` on the model, `sqlmodel.sql.sqltypes.AutoString()` for str.
  Downgrade must drop what upgrade made (FK names are `<table>_<col>_fkey`).
- Every new table with a path to `workspace` must be in `app/services/purge.py`
  `PURGE_PLAN` (or `PURGE_EXEMPT` with a reason) — `tests/test_purge_plan.py`.
  Every new FK into project/testsuite/testcase/testrun needs an `ON_DELETE`
  entry in `app/services/entity_delete.py` — `tests/test_entity_delete_plan.py`.
- New Celery tasks must route to a consumed queue — `tests/test_celery_routing.py`.
  Beat schedule entries go in `app/core/celery_app.py`.
- New step types: add to the worker switch (`execution-engine/src/core/test-executor.ts`
  or wherever the existing step switch lives — find it), to the backend
  `/api/step-types` catalog (`app/api/agent_reference.py`), and the frontend
  step editor if it has a per-type form. Workers hard-fail unknown step types.
- Anything the worker captures that could hold secrets goes through
  `execution-engine/src/core/redact.ts` AND `backend/app/services/redaction.py`
  (twins; add tests to both `redact.test.ts` and `tests/test_redaction.py`).
  Artifact uploads only via `core/artifact-store.ts`.
- User-supplied URLs fetched by the backend go through `app/core/net_guard.py`.
- Regexes supplied by users/test authors never run on Python `re` or a bare JS
  `RegExp` against content the site under test (or any outsider) controls —
  no syntax filter prevents catastrophic backtracking. Backend: compile with
  RE2 (`import re2`, see `app/services/inbox_providers.py:_compile`). Worker:
  `boundedRegexTest` in `execution-engine/src/core/evidence.ts` (vm timeout).
- Shared credentials (tracker connections, workspace secrets) need workspace
  membership, not just project access — a direct project grant can outlive it.
- Secrets at rest: `app/core/secrets.py` (encrypt/decrypt), never plaintext.
- Access control: every endpoint checks project/workspace access via
  `access_service` (min_role editor for writes); project-scoped API keys via
  `app.api.jobs.api_key_allows_project`. Cross-tenant leakage is the #1 bug class.
- Admin-only operator features: `get_current_instance_admin`.
- Audit significant changes with `app.services.audit.record`.
- Config that operators edit at runtime: instance settings REGISTRY in
  `app/services/instance_settings.py`, not new env vars, when appropriate.
- Frontend: React 19 + TanStack Query + shadcn. API clients in `src/api/*.ts`.
  New pages register in `src/App.tsx` and the sidebar nav (find where nav items
  live; there is a nav test in `src/**/__tests__` — keep it passing). Labelled
  controls (no icon-only buttons). Components export only components (fast
  refresh lint) — put helpers in a `.ts` file.
- MCP server (`integrations/mcp-server`): only extend if the WP says so;
  `smoke_test.py:EXPECTED_TOOLS` must list every tool.
- Match the surrounding code's style and comment density. Comments say WHY.

## Done means
1. Feature implemented end to end (model → API → worker if needed → UI).
2. Tests: unit tests for logic, live-DB test for anything touching queries or
   access control, engine tests for worker code. All suites green (baseline
   e2e errors aside). Migration check passes if you added one.
3. `CLAUDE.md`: add a concise bullet under "AI-agent integration surface"
   describing what was built and its non-obvious rules (follow the style of
   the "Targeted runs" / "Flake owners" bullets).
4. Commit(s) on your branch, message style `feat(area): ...` with a body,
   ending the subject with `[skip ci]`. NO `Co-Authored-By` trailer.
5. Self-review your diff once for: access control on every new endpoint,
   cross-project/tenant leaks, secrets in logs/payloads, migrations up/down.
6. Final report (under 400 words): what was built, files/endpoints/steps added,
   migrations (revision id), tests run with counts, anything mock-verified or
   deliberately left out, and whether the worker/frontend/backend image needs
   rebuilding.

## Lessons from WP12/WP13 (2026-10-16) — apply to every new package
- SEPARATE commits for backend/engine/integrations/docs and for `frontend/` (the
  integrator cherry-picks backend to `feature/enterprise-auth-ai`, frontend to
  `feature/redesign-a`). Never mix them in one commit.
- Don't edit `frontend/src/components/layout/nav.ts` / `nav.test.tsx`; register
  routes in `App.tsx` and list them (path + title) in the final report.
- New models: ONE contiguous block at the END of `backend/app/models.py`, before
  the `# Seal every AuditLog row…` section, with a `# WP14x — …` header.
- Migration: one file, the revision id you are given, `down_revision` = the head
  you are given; the integrator re-chains.
- Public/unauthenticated pages: slowapi rate limit, reduced payload, audit without
  secrets, tokens NEVER in a URL path/query (link `/<route>#<token>`, page POSTs it;
  see share links). Unguessable slugs are identifiers, fine in a path.
- Recipients/chat: `app/services/project_notifications.py` + `post_webhook`; never the
  instance-wide webhook. SMTP via `instance_settings.effective`.
- Redesign UI look; reuse `components/redesign/primitives.tsx` and
  `components/redesign-local/groupD/settingsForm.tsx`; narrow cards → stack labels
  above inputs (FieldRow is side-by-side).
- Baseline: backend unit and live suites have NO failures and no errors (since 2026-10-21 `tests/e2e` skips without a running API).

## Lessons from WP15/WP16 (2026-10-21) — apply to every new package
- Run statuses: CANCELLED / TIMED_OUT are terminal WITHOUT a verdict — use
  `app/services/run_status.py` (`TERMINAL` vs `VERDICT`), frontend
  `src/lib/runStatus.ts`. Once-per-finished-run work goes in
  `app/services/run_completion.py` SIDE_EFFECT_TASKS.
- Validation runs (WP16c) must be excluded from every aggregate over runs or
  results: `app/services/run_scope.py` (`counted()` / `counted_result()`).
- RLS (WP15a): a new table with a path to a workspace/project needs an entry
  in `app/services/rls.py` RLS_POLICIES (+ a migration with the policy text
  frozen, matching `rls.policy_sql`) or RLS_EXEMPT with a reason —
  `tests/test_rls_plan.py`. Never add to RLS_DEFERRED (it only shrinks).
  Refusal asserts in live tests: `status_code in DENIED`
  (`tests/integration/denied.py`), so they hold under RLS enforce too.
- Redaction twins also share vector files (`tests/vectors/*.json`, read by
  both test suites): a rule change needs a vector, and the twins must agree.
- Frontend API types: after a backend API change, `npm run gen:api` in
  `frontend/` (CI fails on drift); new clients use `src/api/schemaTypes.ts`.
- Gate checks: add in `evaluate_gate_for_runs`, set `missing_data=True` when
  evidence is absent; the policy is versioned (`gate_policy_versions.py`).
- Workflows stay `workflow_dispatch`-only; pin actions by full commit SHA.
