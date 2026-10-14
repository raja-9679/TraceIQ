# Targeted runs from impact analysis — design

**Date:** 2026-10-14 · **Branch:** `feat/targeted-runs` (off `feature/enterprise-auth-ai`)
**Roadmap:** quick win #2 in `info/FEATURE_ROADMAP_2026-10.md` (gap B7).

## Goal

PR feedback in minutes: run only the cases a change touches, and never give
false confidence when cases were skipped.

## Today

- `POST /api/runs?suite_id=X[&case_id=Y]` runs a whole suite (recursively) or
  one case. It creates one `TestRun` per case × browser × device in SEPARATE
  mode, one per suite × browser × device in CONTINUOUS mode.
- `POST /api/runs/impact-analysis {project_id, changed_files}` returns
  `matched_cases` (each with `suggested_action` run | review |
  run_then_review and `reasons`), `unmatched_files`, and
  `cases_without_code_paths`. It is project-wide.
- The GitHub Action (`integrations/github-action/src/index.js`) already calls
  impact analysis, but only to *report* matches; it always runs the full
  configured suite.
- The quality gate (`app/api/endpoints/quality.py:evaluate_gate_for_runs`)
  scores a commit by the pass rate of that commit's runs — so a run of 4
  passing cases out of 37 would read 100% and pass.

## Decisions (agreed 2026-10-14)

1. **Fallback is strict.** A targeted PR run executes the full suite if any
   changed file maps to no case in the suite, if nothing matched, or if impact
   analysis fails.
2. **The gate may pass a targeted run, labelled partial.** A new
   informational `coverage` check reports "targeted — N of M cases ran"; it
   never fails the gate.
3. **Selection is recorded on `TestRun`** (two nullable columns), not in a new
   table.

## Design

### 1. Data model

`TestRun` gains two nullable columns (one Alembic migration):

| column | type | meaning |
|---|---|---|
| `selection_mode` | `str` | `full` (whole suite), `targeted` (`case_ids`), `case` (single `case_id`); NULL = created before this change |
| `selection_total` | `int` | number of cases in the requested suite's scope at creation time (the "M" in "N of M") |

Not foreign keys, no new table — nothing changes for `PURGE_PLAN`
(`tests/test_purge_plan.py` stays green) or erasure.

### 2. `POST /api/runs` accepts `case_ids`

`RunCreateContext` (JSON body) gains `case_ids: Optional[List[int]]`.

- `suite_id` stays required: it is the permission check, quota check and scope.
- Validation (400 with the offending ids in `detail`):
  - every id must be a case in the suite or one of its descendant modules;
  - at most 500 ids after de-duplication; empty list is rejected;
  - `case_ids` together with `case_id` or `tags` is rejected (ambiguous).
- Each selected case runs with **its own suite's** effective settings, suite
  path and browser/device matrix — the same values a full run of that suite
  would give it. (The existing single-`case_id` path uses the requested
  suite's settings; it is left unchanged.)
- Runs created this way are stamped `selection_mode="targeted"`,
  `selection_total=M` where M is the number of cases in the requested suite's
  scope. Full runs are stamped `full` with the same M; single-case runs
  `case`.
- Quota: counted per created run, exactly as today.
- Cases in a CONTINUOUS-mode suite are created as individual case runs when
  targeted. This matches the worker: a run with `test_case_id` dispatches just
  that case in every mode (`app/worker.py`), and the distributed path already
  dispatches CONTINUOUS cases as independent jobs, so no shared browser state
  is lost.
- Quarantined cases are filtered out before the request's runs are created
  (the worker would drop them and fail the run with "No test cases found").
  They are reported back in the response as skipped, and counted in M but not
  in N — never silently run or silently dropped.

### 3. Impact analysis accepts `suite_id`

`ImpactAnalysisRequest` gains `suite_id: Optional[int]`. When set:

- `matched_cases` is restricted to cases in that suite's subtree;
- `unmatched_files` is computed against that subtree only — a file matched
  only by cases outside the suite counts as unmatched (keeps the strict
  fallback honest);
- `cases_without_code_paths` is counted within the subtree.

Without `suite_id` the response is unchanged (project-wide).

### 4. GitHub Action

New input `mode: suite | targeted`, default `suite` (existing workflows are
unchanged).

`targeted`:

1. Get the PR's changed files (existing code path).
2. `POST /api/runs/impact-analysis {project_id, suite_id, changed_files}`.
3. **Fallback to the full suite** when: the call fails, `unmatched_files` is
   non-empty, or `matched_cases` is empty. The reason is kept
   (e.g. "`src/billing.ts` has no mapped test").
4. Otherwise `POST /api/runs` with `case_ids` = matched case ids.

New outputs: `selection-mode` (`suite` | `targeted` | `fallback`),
`cases-run`, `cases-total`, `fallback-reason`.

PR comment: a header line **"Targeted run: 4 of 37 cases"** or
**"Full suite (fallback: …)"**; matched cases listed with their `reasons`;
cases whose `suggested_action` is `review` are run and flagged "may need
editing". The bundled `dist/` is rebuilt and committed.

### 5. Quality gate

`evaluate_gate_for_runs` adds a `coverage` check, **always `passed=True`**
(informational):

- some run for the commit has `selection_mode="full"` → "full suite";
- only targeted/case runs → "targeted — N of M cases ran" (N = distinct
  `test_case_id` across the commit's runs, M = max `selection_total`);
- no selection data (older runs) → "unknown (runs predate selection
  tracking)".

The pass-rate check is unchanged: it covers the cases that ran.

### 6. MCP server

`run_suite` gains `case_ids: Optional[List[int]]` (client + tool + output
model unchanged); docstring points agents at `select_tests_for_diff` →
`run_suite(case_ids=…)`. `smoke_test.py` inventory updated if signatures are
asserted.

## Error handling

- Out-of-scope / unknown ids → 400 listing them; nothing is created.
- Impact analysis failure in the Action → full-suite fallback with the error as
  the reason (never a silent targeted run, never a skipped run).
- A selection that is entirely quarantined → 400 ("all selected cases are
  quarantined"); the Action falls back to the full suite.
- Cases deleted between impact analysis and run creation → 400 from the API;
  the Action then falls back to the full suite.

## Testing

- Backend unit: `case_ids` validation (scope, duplicates, cap, combinations),
  per-case settings and matrix, selection stamping for full / targeted / case,
  impact analysis with `suite_id` (restriction + unmatched semantics), the
  coverage check for full / targeted / unknown.
- Backend live DB: migration upgrade/downgrade round trip
  (`./run-tests-live.sh --migrations`), a targeted run against Postgres.
- Action: unit tests for the fallback decision and the comment/outputs text.
- MCP: `python -m traceiq_mcp.smoke_test`.
- End to end on the local stack: a suite with several cases, a targeted run of
  two, the right runs exist with the right stamps, and the gate reports
  "targeted — 2 of N".

## Out of scope

- Gate *enforcement* on coverage, critical-priority checks, release readiness
  (roadmap quick win #4).
- Smarter fallback thresholds (decision 1 chose strict).
- Changing the single-`case_id` path's settings inheritance.
