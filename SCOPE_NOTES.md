# Scope Notes — `feature/ai-agent-integration`

This branch implements Phase A of the strategic plan
(`/home/raja/.claude/plans/analyse-how-we-can-silly-phoenix.md`) plus partial
scaffolding for Phase B. This document records what was deliberately left for
follow-up sessions and why.

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
- ✅ MCP server scaffold at `integrations/mcp-server/` with 8 tools (list_projects, list_suites, run_suite, get_run, get_run_results, get_failure_analysis, get_artifact_url, wait_for_run).
- ✅ GitHub Action scaffold at `integrations/github-action/` with action.yml + Node entrypoint. Detects PR SHA/branch/URL automatically.
- ✅ Alembic migration `c5d8f1a2b3c4_ai_agent_integration.py`.
- ✅ CLAUDE.md updated; this SCOPE_NOTES.md added.

### Phase B — scaffolding only
- ✅ `VisualBaseline` table + CRUD at `/api/visual-baselines`.
- ✅ `expect-visual-match` step recognized by execution-engine (captures candidate screenshot).
- ❌ Perceptual diff against stored baselines — **deferred**.

## Deferred to future sessions

### Phase A polish
- **Frontend UI** for API key management, refresh-token-aware auth context, outbound webhook config, and run-list git-context filters. The endpoints exist; the React pages do not yet consume them. The lowest-friction follow-up: add a Settings → Integrations panel with three tabs (API Keys, Webhooks, Visual Baselines).
- **`AuthContext` rework** to use refresh tokens — today the frontend treats expired access tokens as logout. After this branch, it can silently `/api/auth/refresh` instead.
- **GitHub Action `dist/` build** — the action ships only sources. CI release tooling needs to run `npm run build` and commit `dist/index.js` (GitHub's convention).
- **MCP server pip-installable distribution** — today the scaffold lives in `integrations/mcp-server/`; for adoption, publish to PyPI as `traceiq-mcp` and document the install/run flow.
- **MCP server: streamable HTTP transport** — only stdio transport is wired. For hosted MCP scenarios, add HTTP/SSE.
- **Structured failure-report schema** — `TestRun.ai_analysis` is a free-form dict. A typed `schemas/failure_report.py` would let agents consume failures as structured input (with `confidence`, `suggested_fix`, `category`).

### Phase B — Resilience (months of work)
- **Semantic selector layer**: add a parallel `intent` field to `TestStep` (`{type, intent, selector, value}`). Runner resolves `intent` via LLM when `selector` fails. Requires migration of existing step JSON, runner changes in `execution-engine/src/core/test-executor.ts`, frontend editor changes in `frontend/src/pages/SuiteDetails.tsx`. Deferred because the runner refactor is invasive.
- **Proactive selector healing**: scheduled task that diffs captured DOM against stored selectors after every passing run; proposes selector updates via audit log. Hooks into the existing reactive heal in `execution-engine/src/ai.ts`.
- **Full visual regression pipeline**: pixelmatch/odiff in the worker; smart masking; baseline approval workflow. Builds on `VisualBaseline` + `expect-visual-match` scaffold.
- **Persona system + auto-login**: in `info/todo.md` Phase 1. Reusable session artifacts keyed to a `Persona`.
- **Smart retry & flake separation**: cross-worker retry; `confidence` on every failure; auto-quarantine for hard-flake tests.

### Phase C — Coverage (months of work)
- **Browser extension recorder** (`info/todo.md` Phase 1). Standalone Chrome extension that records user journeys and POSTs to `/api/cases`.
- **Test-from-intent generation**: `POST /api/cases/generate` accepting NL description + target URL.
- **Test-from-OpenAPI/GraphQL**: parse schema → generate `http-request` + `feed-check` step pairs.
- **Coverage gap detection**: ingest PR diff, map to test suites, surface untested paths.
- **Deployment-comparison run type**: baseline vs candidate run with delta surfacing.
- **Continuous prod validation**: 5-10 min scheduled runs against prod with read-only persona.

## Migration & rollout notes

- `alembic upgrade head` is required before deploying this branch — `c5d8f1a2b3c4_ai_agent_integration` adds new tables and columns on `testrun`.
- `triggered_by` defaults to `'human'` for all existing rows (server-side default in the migration).
- API keys and webhooks are workspace-scoped and opt-in. No existing functionality changes for users who don't create either.
- `LLM_PROVIDER` defaults to `openai` if `OPENAI_API_KEY` is set (backward-compatible with the previous OpenAI-only behavior). Set `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` to switch.
- The execution-engine TypeScript code uses lazy `require()` for the OpenAI/Anthropic SDKs in `llm-provider.ts` so installing one but not the other still works.

## Verification checklist

After merging:

1. **Migrations apply cleanly:** `alembic upgrade head` against a copy of prod.
2. **JWT auth still works:** existing frontend logs in and uses the app normally.
3. **API key auth:** mint a key via UI/API; call `GET /api/projects` with `X-API-Key`; confirm 200.
4. **Refresh token:** confirm `/api/auth/login` returns a `refresh_token`; `/api/auth/refresh` rotates it; reuse of a revoked token returns 401 with "reuse detected".
5. **Outbound webhook:** register a webhook pointing at `httpbin.org/post`; trigger a run that finalizes; confirm the payload arrives with `X-TraceIQ-Signature`.
6. **PARALLEL run:** create a suite with 10 cases, set mode=parallel, trigger; confirm 10 individual jobs land on `jobs:pending` Redis stream tagged `execution_mode: parallel`.
7. **Git context:** trigger a run with `git_commit=abc123`; confirm the run record has the commit; filter `GET /api/runs?git_commit=abc123` returns it.
8. **MCP server smoke:** `python -m traceiq_mcp.smoke_test` returns project list.
9. **GitHub Action smoke:** on a sample repo, set `TRACEIQ_API_KEY` secret, run the action; confirm a TraceIQ run is created with PR context populated.
10. **LLM provider switch:** set `LLM_PROVIDER=anthropic`; trigger a failing run; confirm AI failure analysis still populates (or is empty with a clean log message if no key).
