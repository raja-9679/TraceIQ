# Design: coverage-based impact analysis

**Status:** design only — not implemented. Supersedes path-prefix matching
against self-reported `TestCase.code_paths` once built.
**Estimated effort:** 2–3 weeks including the JS coverage pipeline and a
backfill period where both signals run side by side.

## Problem

`POST /api/runs/impact-analysis` matches PR-changed files against
`code_paths` that humans/agents typed onto each case. Self-reported paths
rot: they're wrong at creation (guesses), and nobody updates them when code
moves. Result: missed regressions (case not selected) and wasted runs
(over-broad globs).

## Proposal

Capture **which application files each test actually executes**, per run,
and match diffs against that observed signal.

### 1. Capture (execution-engine)

- **Frontend JS coverage** — Playwright's Chromium-only
  `page.coverage.startJSCoverage()` / `stopJSCoverage()` around each case.
  Yields executed ranges per script URL. Map bundle URLs → source files via
  the app's **source maps** (fetch `//# sourceMappingURL`, apply
  `source-map` lib). Apps without source maps degrade to bundle-URL
  granularity, still useful for route-level chunks.
- **Backend coverage (opt-in, later phase)** — the tested app exposes a
  coverage endpoint (istanbul middleware for Node, `coverage.py` WSGI wrap
  for Python) that TraceIQ's worker polls per case:
  `GET {app}/__coverage__?reset=true`. Requires app-side cooperation;
  document as an SDK-style integration, never a requirement.
- Worker normalizes to `{case_id → [file paths]}` per job, capped (e.g.
  2 000 files/case, node_modules and vendored chunks filtered) and ships it
  on the job result as `coverage: {files_by_case}`.

### 2. Storage (backend)

New table `casecoverage` (not columns on `TestCaseResult` — coverage is
per (case, file), needs indexing by file):

```
casecoverage
  id            PK
  project_id    FK, indexed
  test_case_id  FK, indexed
  file_path     str, indexed (normalized, repo-relative when mappable)
  last_seen_run_id FK
  hit_count     int          -- how many runs observed this edge
  updated_at    datetime
```

Aggregator upserts edges on finalize; edges not re-observed for N runs
decay (delete when `last_seen_run_id` is > 50 runs behind) so moved code
doesn't pin stale selections forever.

### 3. Matching (impact analysis v2)

`POST /api/runs/impact-analysis` gains `strategy: "coverage" | "paths" |
"both"` (default `both` during transition):

- coverage edge match: changed file has a `casecoverage` row → select case
  (confidence = hit_count / runs_observed).
- fall back to `code_paths` for cases with no coverage yet (new cases, API
  cases whose coverage comes only from the backend integration).
- response distinguishes `matched_by: coverage | code_paths` so the GitHub
  Action / agents can weigh them.

### 4. Repo-relative path mapping

Bundle → source mapping produces paths like `webpack://app/src/x.ts`.
Normalize: strip scheme prefixes, then longest-suffix match against the
diff's file list. Ambiguities keep both candidates (safe over-selection).

### 5. Rollout

1. Ship capture behind `COVERAGE_CAPTURE_ENABLED` (worker) — collect only.
2. After ~2 weeks of edges, expose `strategy=both`; UI shows per-case
   coverage counts on the App Surface endpoint.
3. Flip default to coverage-first; keep `code_paths` as authoring hints.

## Non-goals

- Statement/branch-level coverage metrics (this is selection, not a
  coverage report product).
- Firefox/WebKit JS coverage (Chromium-only API; selection quality from
  chromium runs generalizes).
- Enforcing backend coverage integration — always optional.

## Open questions

- Source-map fetching from prod deployments (often stripped) — likely
  staging-only capture, which is fine: selection needs topology, not prod.
- Coverage payload size on huge SPAs — mitigate with the per-case file cap
  plus server-side dedup before insert.
