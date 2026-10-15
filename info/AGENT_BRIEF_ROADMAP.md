# Brief for agents building a roadmap work package

You are implementing ONE work package (WP) from `info/ROADMAP_EXECUTION_2026-10.md`
in the TraceIQ repo. Read `CLAUDE.md` (repo root) first — it is long but it is
the map of the codebase and lists the traps. `info/CODEBASE.md` has the data model.

## Where you work
- You are given a git worktree path and a branch name. Work ONLY there.
- Never touch: `/home/raja/Work/repos/TraceIQ` (main clone, redesign branch),
  `/home/raja/Work/repos/TraceIQ-base`, other worktrees, `/home/raja/traceiq-test`
  (the running local stack), docker images/containers other than what the test
  scripts start. Never `git push`, `git pull`, `git merge` from origin, rebase
  onto origin, or change git config. Never edit `.claude/` or settings.
- Node deps: if `execution-engine/node_modules` or `frontend/node_modules` is
  missing in your worktree, symlink it:
  `ln -s /home/raja/Work/repos/TraceIQ-base/execution-engine/node_modules execution-engine/node_modules`
  `ln -s /home/raja/Work/repos/TraceIQ/frontend/node_modules frontend/node_modules`
  Never commit these symlinks (`git add` specific paths, not `-A` at the root).

## How to test
- Backend unit: `cd backend && ./run-tests.sh -q` (whole suite) or a file.
  Baseline: 4 errors in `tests/e2e/*` are expected (need a live server).
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
