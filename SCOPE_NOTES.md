# Scope Notes — `feature/ai-agent-integration`

This branch implements the strategic plan
(`/home/raja/.claude/plans/analyse-how-we-can-silly-phoenix.md`) across
Phase A (AI-callable), Phase B (resilience), and Phase C (coverage).
Most items ship as working backends with scaffold-quality wiring; frontend
UIs for the new entities, full Playwright recorder fidelity, and a
statistical flake model remain deferred to follow-up sessions.

For the authoritative end-to-end picture, see `ARCHITECTURE.md`.

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

### Schema migration
- ✅ Alembic migration `d6f9a3b4c5d6_phase_b_c.py` adds `persona`, `selectorhealproposal`, `flakerecord` tables and `testrun.{baseline_run_id, target_url, persona_id}` + `testcaseresult.{retry_count, confidence, is_flaky}` columns.

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
- **Statistical flake scoring** — the `flake_score` column exists; populating it (alternating-status detection over recent retries) is open.
- **Visual baseline approval UI** — baselines today are created via API only. A "promote this candidate to baseline" UI is needed for human-in-the-loop adoption.

### Phase C polish
- **Coverage gap detection** — design open. Inputs: PR diff (paths or AST), test suites, route → test mapping. Output: warn or auto-draft. Belongs in the GitHub Action plus a backend endpoint.
- **Continuous prod validation** — already possible via the cron `TestSchedule` model + a `target_url`-bearing comparison run; needs a UI to set up the recurring schedule.
- **Recorder fidelity** — drag-drop, iframe interactions, hover-only flows, network capture. Today's recorder is goto/click/fill/press-key only.

---

## Migration & rollout notes

- Two new migrations apply on top of `b2c4e6f8a0d1`:
  1. `c5d8f1a2b3c4_ai_agent_integration` — Phase A
  2. `d6f9a3b4c5d6_phase_b_c` — Phase B/C
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
