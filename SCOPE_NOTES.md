# Scope Notes — `feature/ai-agent-integration`

This branch implements the strategic plan
(`/home/raja/.claude/plans/analyse-how-we-can-silly-phoenix.md`) across
Phase A (AI-callable), Phase B (resilience), and Phase C (coverage).
Most items ship as working backends with scaffold-quality wiring; frontend
UIs for the new entities, full Playwright recorder fidelity, and a
statistical flake model remain deferred to follow-up sessions.

For the authoritative end-to-end picture, see `ARCHITECTURE.md`.
For a full feature-coverage scorecard against a modern-platform checklist (and the prioritized roadmap derived from it), see `info/FEATURE_GAP_ANALYSIS.md`.

---

## Shipped on this branch

### Phase A — Make TraceIQ AI-callable
- ✅ API key auth (`X-API-Key`), workspace-scoped, revocable, expirable. CRUD at `/api/workspaces/{id}/api-keys`.
- ✅ Refresh tokens (rotation-on-use, family-based revoke on replay). `/api/auth/refresh`, `/api/auth/logout`.
- ✅ Git context columns on `TestRun`: `git_commit`, `git_branch`, `git_pr_url`, `git_repo`, `triggered_by`, `agent_id`, `api_key_id`.
- ✅ `RunTrigger` enum (`human`, `schedule`, `api_agent`, `ci`, `webhook`).
- ✅ `POST /api/runs` accepts a JSON body of git context; defaults `triggered_by=api_agent` when called with an API key.
- ✅ `GET /api/runs` filterable by `git_commit`, `git_branch`, `triggered_by`, `agent_id`.
- ✅ Outbound webhook registry (`WorkspaceWebhook`) + Celery dispatch on run finalize (HMAC-SHA256 signed).
- ✅ LLM provider abstraction — `OpenAIProvider`, `AnthropicProvider`, `NullProvider`. Backend `app.ai.providers`, execution-engine `execution-engine/src/llm-provider.ts`. Switch via `LLM_PROVIDER` env.
- ✅ PARALLEL execution mode routed in `app/worker.py` via `dispatch_parallel_jobs`. Capped by `PARALLEL_MAX_CONCURRENCY`.
- ✅ MCP server scaffold at `integrations/mcp-server/` with 8 tools.
- ✅ GitHub Action scaffold at `integrations/github-action/` with `action.yml` + Node entrypoint.
- ✅ Alembic migration `c5d8f1a2b3c4_ai_agent_integration.py`.

### Phase B — Resilience
- ✅ **Semantic-selector layer**: `TestStep.intent` (additive Pydantic field). Runner's reactive heal already consults the LLM on miss; intent makes the durable contract explicit.
- ✅ **Persona system**: `Persona` table + CRUD + `POST /persona/{id}/refresh` that delegates to a Celery task. Worker hook for `storageState` hydration documented in `ARCHITECTURE.md`; execution-engine refresh-endpoint implementation is pending.
- ✅ **Proactive selector heal**: `SelectorHealProposal` table + `propose_selector_heals_for_run` Celery task wired into `/finalize`. Gated by `PROACTIVE_HEAL_ENABLED=true`. Accept / reject endpoints rewrite the step's selector.
- ✅ **Visual regression diff**: `VisualBaseline` CRUD + `expect-visual-match` step now performs perceptual diff via pixelmatch (`execution-engine/src/visual-diff.ts`) when a baseline exists. Mask regions supported. Fails the step on `diffRatio > tolerance`.
- ✅ **Smart retry + flake quarantine**: `retry_count`, `confidence`, `is_flaky` on `TestCaseResult`. New `FlakeRecord` table with quarantine flag. `/api/flakes/{id}/{quarantine|release}`. Quarantined cases are filtered at dispatch time in `worker.py:_filter_quarantined`.

### Phase C — Coverage
- ✅ **Test-from-intent generation**: `POST /api/cases/generate` accepts `{description, target_url, test_suite_id}` and produces a draft `TestCase` via the LLM provider.
- ✅ **Test-from-OpenAPI**: `POST /api/cases/from-openapi` accepts a schema URL or inline schema and emits one `TestCase` per operation with `http-request` + `feed-check` steps.
- ✅ **Deployment-comparison runs**: `baseline_run_id` + `target_url` on `TestRun`. New `POST /api/runs/comparison` endpoint; `GET /api/runs/{id}/comparison` returns side-by-side delta.
- ✅ **Browser-recorder Chrome extension**: MV3 scaffold at `integrations/browser-recorder/` (manifest, content script, background SW, popup). Records goto/click/fill/press-key; POSTs to `/api/cases` with `X-API-Key`.

### Phase D — Agent owns the test suite
- ✅ **TestCase agent-ownership fields**: `code_paths` (JSON array), `is_ai_authored`, `ai_confidence`, `last_human_reviewed_at`, `last_human_reviewed_by_id`.
- ✅ **`Workspace.ai_generation_limit_daily`** (default 100) — per-workspace cap enforced via Redis counter. Returns 429 when exhausted.
- ✅ **`CaseProposal` queue**: agents submit create/update/delete/move proposals; humans accept or reject via `/api/case-proposals/{id}/{accept,reject}`. Accepted proposals are applied (creating, patching, deleting, or moving cases). API keys CANNOT accept/reject — humans only.
- ✅ **Impact analysis**: `POST /api/runs/impact-analysis` — path-prefix and glob matching of `changed_files` against `TestCase.code_paths`. Returns matched cases + unmatched files (gap candidates).
- ✅ **App surface**: `GET /api/apps/{project_id}/surface` — suite tree with case counts, routes covered (distinct goto URLs), code-paths covered, recent runs, case counts (total / AI-authored / human-reviewed / with-code_paths).
- ✅ **Case run history**: `GET /api/cases/{case_id}/run-history` — last N runs touching a given case, with pass/fail summary.
- ✅ **Propose-vs-direct mode on `/api/cases/generate`**: API key callers are forced to `mode=propose`. Humans default to `direct` but may opt into `propose` for review-trail.
- ✅ **Tautology detector**: Celery task `scan_for_tautologies` gated by `TAUTOLOGY_DETECTOR_ENABLED=true` — flags AI-authored, never-reviewed cases that have passed N consecutive runs in <500 ms.
- ✅ **MCP tools added**: `discover_app_surface`, `select_tests_for_diff`, `get_run_history`, `create_suite`, `propose_create_case`, `propose_update_case`, `propose_delete_case`, `set_code_paths`, `generate_case_proposal`.

### Schema migrations
- ✅ Alembic migration `d6f9a3b4c5d6_phase_b_c.py` adds `persona`, `selectorhealproposal`, `flakerecord` tables and `testrun.{baseline_run_id, target_url, persona_id}` + `testcaseresult.{retry_count, confidence, is_flaky}` columns.
- ✅ Alembic migration `e7a1b2c3d4e5_phase_d.py` adds `caseproposal` table, `caseproposalaction` enum, `testcase.{code_paths, is_ai_authored, ai_confidence, last_human_reviewed_at, last_human_reviewed_by_id}`, and `workspace.ai_generation_limit_daily`.

### Documentation
- ✅ `ARCHITECTURE.md` — comprehensive system doc (services, data model, lifecycle, auth, AI integration, RBAC, extension points, ops, glossary).
- ✅ `CLAUDE.md` updated (React/Router versions, expanded backend tree, Celery aggregator + beat commands, AI-agent integration section).
- ✅ `SCOPE_NOTES.md` — this file.

---

## Still deferred to future sessions

These items either require significant frontend work, external products, or design decisions that need stakeholder input.

### Phase A polish
- **Frontend UI** for API keys, refresh-token-aware auth context, outbound webhooks, personas, heal proposals, flake records, visual baselines, deployment comparison. Every entity here has working backend endpoints; React pages have not been built. Recommended follow-up: a Settings → Integrations panel with tabs.
- **GitHub Action `dist/` build** — the action ships only sources. CI release tooling needs to run `npm run build` and commit `dist/index.js` (GitHub's convention).
- **MCP server pip-installable distribution** — today the scaffold lives in `integrations/mcp-server/`; for adoption, publish to PyPI as `traceiq-mcp`.
- **MCP server: streamable HTTP transport** — only stdio is wired. Hosted MCP scenarios need HTTP/SSE.
- **Structured failure-report schema** — `TestRun.ai_analysis` is a free-form dict. A typed `schemas/failure_report.py` would give agents a contract.

### Phase B polish
- **Persona-refresh handler in execution-engine** — backend task POSTs to `<engine>/persona-refresh`; that endpoint does not exist yet. The task degrades gracefully when the engine returns 4xx.
- **Per-step DOM capture in execution-worker** — `heal_tasks.propose_selector_heals_for_run` reads DOM from `run.execution_log[*].dom`. The worker captures DOM for some step types but not consistently per step; until it does, proactive heal proposals will rarely be generated.
- ~~**Statistical flake scoring**~~ — SHIPPED since this note was written: `result_aggregator.py` computes `flake_score` (alternation ratio over the last 20 results) with auto-quarantine ≥ 0.4 and auto-release < 0.15, backed by `FlakeRecord`.
- **Visual baseline approval UI** — the backend promote workflow now exists (`POST /api/visual-baselines/promote` copies a run's candidate screenshot to a durable baseline); the React "promote this candidate" UI is still needed for human-in-the-loop adoption.

### Phase C polish
- **Coverage gap detection** — partially shipped via Phase D's `impact-analysis` endpoint (returns unmatched files). The "auto-draft a test for unmatched files" pipeline is open: an agent must explicitly call `generate_case_proposal` on each unmatched file. Possible next step: a server-side "auto-propose for every unmatched file" mode.
- **Continuous prod validation** — already possible via the cron `TestSchedule` model + a `target_url`-bearing comparison run; needs a UI to set up the recurring schedule.
- **Recorder fidelity** — drag-drop, iframe interactions, hover-only flows, network capture. Today's recorder is goto/click/fill/press-key only.

### Local development bridge — prerequisite for SaaS commercial use

The core agent-development loop is:
1. Agent writes code locally → local dev server starts (e.g. `localhost:3000`)
2. Agent creates / updates TraceIQ test cases via MCP (with `code_paths`)
3. Agent triggers a run → passes/fails
4. Agent calls impact analysis → TraceIQ identifies other affected cases
5. Agent runs regression tests for those cases too
6. All green → safe to commit / open PR

This loop is exactly what Mode 1 was designed for. **The gap is step 3**: TraceIQ's execution workers run on the server and cannot reach `localhost` on the developer's machine. Until this is solved, TraceIQ can only test apps that are already deployed somewhere (staging, prod) — useful, but it misses the highest-value moment: catching regressions before the commit.

**Chosen approach: local execution worker with HTTP polling.**
- Ship a thin `traceiq-worker` CLI (likely `npx traceiq-worker`) the developer runs alongside their dev server.
- Instead of connecting to Redis directly (internal to the server), the local worker **polls the TraceIQ API** for pending jobs assigned to a `local_worker_id` the developer registers at workspace setup.
- Worker runs Playwright against `localhost`; POSTs results back to TraceIQ over HTTPS.
- TraceIQ server stays fully closed — only the public REST API is needed.
- Agent (Claude Code or similar) can auto-start the local worker as part of its dev-loop setup.

**Why not tunnel (ngrok / Cloudflare)?**
Tunnel approach works today with zero TraceIQ changes, but exposes the local dev server publicly — uncomfortable for internal, unreleased, or credential-bearing apps. Local worker keeps everything local.

**✅ BUILT (2026-07-23), with small deltas from the sketch above:**
- Auth reuses **workspace API keys** (no new worker-token concept): the key's
  workspace namespaces the job queue (`jobs:local:{workspace_id}:{worker_id}`),
  so a worker can never poll another tenant's jobs.
- `POST /api/runs` accepts `local_worker_id` in the body; dispatch routes that
  run's jobs to the local Redis list (1-day TTL) instead of `jobs:pending`.
- `GET /api/jobs/poll?worker_id=…` (API-key auth) pops the next job; 204 idle.
- Results POST to **`POST /api/jobs/result`** (not the webhook endpoints): the
  server validates run/workspace ownership, increments the run's progress
  hash, and feeds the normal `jobs:results` stream — aggregation, finalize,
  notifications identical to server execution. Verified end-to-end.
- CLI: `npm run worker:local` in `execution-engine/`
  (`src/local-worker.ts`, env: TRACEIQ_URL / TRACEIQ_API_KEY /
  TRACEIQ_WORKER_ID). Reuses the full TestExecutor step engine.

Still deferred for the local worker: continuous (sub-suite) jobs — v1 is
single-test jobs only (SEPARATE/PARALLEL); artifact upload (screenshots/video/
trace stay on the dev machine — no MinIO from outside the cluster); an
`npx traceiq-worker` standalone package (today it runs from the repo).

### Phase MOB — native mobile app testing (started 2026-07-25)

Full phase plan in `info/FEATURE_GAP_ANALYSIS.md` §31 + "Phase MOB". Built so far (MOB-2 complete, MOB-3 scaffold):

- ✅ `ExecutorType.MOBILE_APPIUM` — rides the executor keystone; no schema change needed for the value itself.
- ✅ `MobileAppBuild` registry (APK/AAB/IPA → MinIO `app-builds/{project_id}/`) + CRUD at `POST/GET /api/projects/{id}/app-builds`, `GET/DELETE /api/app-builds/{id}`. Editor-gated uploads, extension-vs-platform validation.
- ✅ `TestRun.app_build_id` (+ `app_build_id` in the `POST /api/runs` body, validated against the suite's project). Migration `b3c4d5e6f7a8_mobile_app_testing`.
- ✅ Dispatch routing: mobile jobs land on a dedicated `jobs:mobile:pending` stream (`mobile-workers` consumer group) so Playwright workers never claim them; job settings carry a `mobile_app` descriptor (internal presigned URL + platform + package_id).
- ✅ 11 native step types (`mobile-tap`, `mobile-swipe`, `mobile-type`, …) in the `agent_reference.py` catalog with an Appium locator convention (`~accessibility-id`, `xpath=`, `id=`, `android=`, `ios=`).
- ✅ `execution-engine/src/mobile-worker.ts` — reuses `JobQueue` (stream/group via env), drives Appium over the raw W3C WebDriver protocol (`src/core/webdriver-client.ts`, zero new npm deps). `npm run worker:mobile` / `dev:mobile-worker`.
- ✅ Compose profile `mobile`: `appium` + `mobile-worker` services (`docker compose --profile mobile … up`).
- ✅ Executor inference: cases containing `mobile-*` steps get `executor=mobile_appium` automatically on create/update (`test_cases.py`, same pattern as `load-test`).
- ✅ Frontend: App Builds page (`/app-builds` — upload APK/AAB/IPA, list, download, delete; Environments-style project scoping); app-build + environment pickers next to Run Now in `SuiteDetails.tsx` (pinned onto every run from the page); 11 mobile step types in the TestBuilder step editor under a "Mobile App (Appium)" category with Appium-locator inputs; `triggerRun` now sends a `RunCreateContext` JSON body.
- ✅ Screenshot artifacts: `mobile-screenshot` steps + automatic on-failure captures upload to MinIO (`runs/{run_id}/screenshots/`, same layout as web).
- ✅ Interpolation: `{{env.X}}` / `{{secret.X}}` / `{{data.X}}` / `{{fake.KIND}}` resolve in mobile step selectors/values/params via the shared `execution-engine/src/core/interpolate.ts` (extracted from test-executor so both executors use one implementation).
- ✅ Deploy runbook: `PRODUCTION_DEPLOY.md` §2.7 (migration, image rebuilds, opt-in mobile profile, device options, smoke checklist, rollback).

- ✅ MOB-5 — selector heal on mobile: locator failures propose an LLM-healed Appium locator from the XML page source (`AIEngine.healMobileLocator`, shared cache/per-run cap); suggestions flow through the existing `heal_suggestions` → `SelectorHealProposal` pipeline unchanged; `RUNTIME_HEAL_ENABLED=true` retries the step in place on a unique match. Failure-analysis heuristics gained Appium-session and missing-app-build patterns (mobile locator/timeout errors already matched the existing patterns).

- ✅ MOB-4 — device-cloud adapter: `MOBILE_DEVICE_PROVIDER=local|browserstack|saucelabs|lambdatest` (`execution-engine/src/device-cloud.ts`). Cloud providers get the binary pushed to their app storage automatically (bs://… / storage:… / lt://…, cached per build for the worker's lifetime), vendor options blocks (`bstack:options` etc.) injected, hub auth via basic-auth header. This is the iOS path.

- ✅ Mobile polish (2026-07-26): `mobile-expect-visual-match` (pixelmatch against baselines keyed browser='mobile'; candidate + diff uploaded as artifacts; capture-only when no baseline); `mobile-extract-value` feeding `{{name}}` runtime variables within a case; best-effort MP4 screen recording per job (`appium/start|stop_recording_screen` → `runs/{run_id}/videos/`). All 13 mobile step types in the catalog + step editor.

Still deferred for mobile:
- **A local Android device** for the self-hosted path: attach `budtmo/docker-android` (KVM required) or a USB device via host Appium. (Cloud providers need no local device.)
- End-to-end smoke against a real device/cloud account — the only remaining gap in Phase MOB.

### Phase F (future) — Mode-2 discovery + server-side codebase analysis

Explicitly NOT built in Phase E. The MCP design doesn't preclude them — they can ship later without touching what's shipped now.

- **Mode-2 discovery tools** for agents that have ONLY a URL + token (no source-code access). `fetch_openapi`, `probe_api_endpoint`, `probe_ui_route`, `crawl_ui`, `register_app_credential`. Backend-side (token never leaves TraceIQ; uses execution-workers' existing Playwright + network reach).
- **Server-side codebase tools** for richer Mode-1 helpers: `find_untested_code(project_id, code_globs[])`, `derive_routes_from_fastapi(code_root)`, etc. Lets the server help with "which files have no test coverage." Requires TraceIQ to have a code-mount path (deployment-specific); keep optional.
- **Auto-approval policy** for self-created entities. With `created_by_agent_id` + `agent_session_id` shipped, a workspace can opt into "auto-accept delete proposals where the proposal's agent_session_id matches the entity's." Risky default; ship UI first.
- **`describe_step_types` source-of-truth check.** CI script that parses `execution-engine/src/core/test-executor.ts` and warns when the curated list in `agent_reference.py` diverges from the runner.

### Phase D polish
- **Frontend UI for the proposal queue** — endpoints exist but there's no React page. The "Inbox" view should show pending proposals with a side-by-side diff (current case vs. proposed), accept/reject buttons, and a quick "regenerate" action. This is the single most impactful UI gap blocking real-world Phase D use.
- **GitHub Action integration with impact analysis** — the existing action triggers a full suite run; it should call `select_tests_for_diff` first and run only the matched cases (with a fallback to the full suite for safety).
- **Coverage-based impact analysis (replaces path-prefix)** — instrument the worker so per-file coverage data is captured during each run, then store it on `TestCaseResult` or a separate `CaseCoverage` table. Match diffs against actual coverage instead of self-reported `code_paths`. Much more accurate; ~weeks of work.
- ~~**Auto-apply policy**~~ — SHIPPED 2026-07-26: `Workspace.auto_apply_threshold` + `PUT /api/workspaces/{id}/proposal-policy` (humans only). CREATE/UPDATE proposals with `ai_confidence ≥ threshold` merge at submit time on all three proposal-creation paths; DELETE/MOVE always wait. Backed by `TestCaseRevision`; threshold control on the Proposals page. (The session-matching auto-approval for deletes above remains open.)
- **Run-history per case** is currently best-effort: it matches `TestCaseResult.test_name == case.name`. If two cases share a name across suites it conflates results. Fix is to backfill `TestCaseResult.test_case_id` (column doesn't exist yet) and update the worker to emit it.
- **Bulk impact analysis** — the current endpoint processes one diff per call; a high-PR-volume org may want batched analysis.

### Phase I18N / SOC2 — parked items (audited 2026-07-28)

Full plan in `info/I18N_SOC2_IMPLEMENTATION_PLAN.md`. Recorded here so the
deferred pieces don't get lost.

- **RTL support — explicitly deferred, not dropped.** Blocked on a commercial
  commitment to an Arabic or Hebrew locale, because it's a mass mechanical
  rewrite rather than an incremental feature: ~300 physical-direction Tailwind
  utilities (`mr-2` ×78, `mr-1.5` ×49, `text-right` ×38, plus
  `pl-*`/`pr-*`/`left-*`/`right-*`/`border-l`) and **zero** logical equivalents
  anywhere in the tree — no `ms-*`, `me-*`, `ps-*`, `pe-*`, `text-start`,
  `text-end`. Absolutely-positioned input icons (`pl-9` paired with `left-3`)
  mirror incorrectly and need per-component judgement. Do the logical-property
  migration as one sweep when it's actually needed; piecemeal conversion leaves
  the codebase in a worse mixed state than either endpoint.
- **Needs no work — don't re-audit.** Charset/collation is already correct
  (UTF8 client encoding, no fixed-width `VARCHAR(n)` on user text, no explicit
  `COLLATE` in any of the 39 migrations), and no text is baked into images or
  SVGs (icons are glyph-only `lucide-react`; every apparent `<text` grep hit is
  actually `<textarea`).
- **AI analysis retranslation is a product decision, not a bug.** LLM failure
  analyses are persisted, so a user switching locale will still see old analyses
  in the original language. Threading a locale into the prompt builders is cheap
  (every provider's `complete()` already accepts `system`), but back-filling or
  re-generating historic analyses is deliberately out of scope — decide whether
  to retranslate on read, regenerate on demand, or leave them.
- **Schedule timezones** — `TestSchedule` has no `timezone` column and croniter
  runs on naive UTC, so the `Every Monday at 9AM` presets are wrong for every
  non-UTC customer and the UI just discloses "Times are evaluated in UTC."
  Deferred behind the `TIMESTAMPTZ` migration because DST correctness needs a
  tz-aware croniter base, not just a column.
- **SOC 2 Type II observation window** — Type I is a point-in-time design
  opinion and is what the plan targets. Type II additionally needs 3–12 months
  during which the audit-log and monitoring evidence actually accumulates, so it
  cannot be compressed by engineering effort. Sequence the auditor engagement
  accordingly.

---

## Phase E — Mode-1 MCP completion (agent has source-code access)

- ✅ **Provenance fields.** `created_by_agent_id` + `agent_session_id` (indexed) on `testsuite`, `testcase`, `caseproposal`. Backed by `X-Agent-Id` + `X-Agent-Session-Id` headers; threaded through every create endpoint (suites, cases, generation, openapi-import, agent-ownership proposals).
- ✅ **MCP read tools.** `list_cases`, `get_case`, `get_suite`, `list_case_proposals`, `describe_step_types`, `get_authoring_guide` — backed by existing or new REST endpoints (`/api/step-types`, `/api/agent-guide`).
- ✅ **MCP structural writes.** `delete_suite` (with FK-aware cascade in `recursive_delete_suite`; 409 on referencing schedules).
- ✅ **Bulk ops.** `POST /api/cases/bulk-propose` (N proposals → N per-item results), `POST /api/cases/bulk-set-code-paths` (`{case_id: [paths]}` atomic-per-row).
- ✅ **Step-type catalog.** Hand-curated list of 25 step types with shapes + examples + gotchas in `agent_reference.py`. Source of truth for what the runner supports.
- ✅ **AGENT_GUIDE.md** at `integrations/mcp-server/AGENT_GUIDE.md` (canonical) + `backend/app/AGENT_GUIDE.md` (bundled). 12 sections, Sarvajna-anchored, includes 5 well-documented pitfalls (`feed-check` shape, `json-path` prefix, `fill` on `<select>`, `expect-url` glob, `wait-for-selector` during redirect).
- ✅ Alembic migration `f8b3c4d5e6f7_phase_e.py`.

## Migration & rollout notes

- Four new migrations apply on top of `b2c4e6f8a0d1`:
  1. `c5d8f1a2b3c4_ai_agent_integration` — Phase A
  2. `d6f9a3b4c5d6_phase_b_c` — Phase B/C
  3. `e7a1b2c3d4e5_phase_d` — Phase D (caseproposal, agent-ownership cols)
  4. `f8b3c4d5e6f7_phase_e` — Phase E (agent provenance cols)
  Run `alembic upgrade head` before deploying this branch.
- `triggered_by` defaults to `'human'` for existing rows (server-side default).
- `retry_count` defaults to 0, `is_flaky` to false — existing `testcaseresult` rows backfill cleanly.
- API keys, webhooks, personas, baselines are opt-in. No existing functionality changes for users who don't create them.
- `LLM_PROVIDER` defaults to `openai` if `OPENAI_API_KEY` is set (backward-compatible). Set `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` to switch.
- `PROACTIVE_HEAL_ENABLED` defaults to `false` — turn on only after verifying per-step DOM capture is in place.
- New backend Python deps: `anthropic==0.39.0`, `PyYAML==6.0.1`.
- New execution-engine deps: `pixelmatch`, `pngjs`. Existing deployments unaffected when these aren't installed — the visual-diff step degrades to capture-only.
- Browser extension is unsigned; install via Chrome's developer mode. Publishing to the Chrome Web Store is a separate process.

---

## Verification checklist

Static checks completed by the in-process harness (`/tmp/traceiq-verify/run_checks.py`) for Phase A: **33 PASS, 0 FAIL**. Phase B/C adds the same shape of static-checkable surface (model imports, endpoint registration, signing function, dispatcher routing) — re-run the harness after merging to confirm the new routes are registered.

End-to-end items that need the live cluster (mark complete after staging deploy):

1. `alembic upgrade head` against staging — both new migrations apply cleanly.
2. JWT login + refresh-token rotation still works for human users.
3. API-key auth: mint a key, hit `/api/projects` with `X-API-Key`.
4. Outbound webhook: register, finalize a run, confirm httpbin receives signed POST.
5. PARALLEL run: 10-case suite in PARALLEL mode against 4 workers — confirm 10 jobs land on `jobs:pending` with `execution_mode=parallel`.
6. Git context: `POST /api/runs` with `git_commit=abc123`; `GET /api/runs?git_commit=abc123` returns it.
7. MCP smoke: `python -m traceiq_mcp.smoke_test`.
8. GitHub Action smoke: open a PR on a sample repo with `TRACEIQ_API_KEY` secret.
9. LLM provider switch: set `LLM_PROVIDER=anthropic`; trigger a failing run; confirm `ai_analysis` populates.
10. Persona create + manual `session_state` set; trigger a run with `persona_id` set; confirm execution-worker hydrates storage state.
11. Visual baseline create; trigger `expect-visual-match` step; confirm pass when baseline matches and fail when it doesn't.
12. Flake quarantine: mark a `FlakeRecord` quarantined; trigger a run; confirm the case is skipped at dispatch.
13. `POST /api/cases/generate` with a real LLM key; confirm a draft case is created.
14. `POST /api/cases/from-openapi` with a small public schema (e.g. petstore); confirm cases are created.
15. `POST /api/runs/comparison` with a baseline; confirm a comparison run is created with `baseline_run_id` set and the comparison endpoint returns deltas.
16. Browser recorder: load unpacked, record three clicks on a site, save; confirm `/api/cases` receives them.
17. Phase D — discover_app_surface: `GET /api/apps/{id}/surface` returns the project's suite tree, routes covered, and case counts.
18. Phase D — impact analysis: `POST /api/runs/impact-analysis` with a known changed file matches a case whose `code_paths` covers it; an unmatched file is surfaced under `unmatched_files`.
19. Phase D — proposal queue: with an API key, `POST /api/cases/generate` with no `mode` defaults to propose; returns a CaseProposal id. Human accepts via `POST /api/case-proposals/{id}/accept`; the resulting case has `is_ai_authored=true` and `last_human_reviewed_at` set.
20. Phase D — API keys CANNOT accept proposals: hitting `/api/case-proposals/{id}/accept` with `X-API-Key` returns 403.
21. Phase D — budget cap: set `Workspace.ai_generation_limit_daily=1` and fire two `/api/cases/generate` calls; second returns 429.
22. Phase D — tautology detector (manual): set `TAUTOLOGY_DETECTOR_ENABLED=true`, run the beat task or call `app.tasks.tautology_tasks.scan_for_tautologies.delay()`, confirm a `CaseProposal` is generated for an AI-authored case with N consecutive sub-500-ms passes.
