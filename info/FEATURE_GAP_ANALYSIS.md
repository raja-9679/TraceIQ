# TraceIQ Feature Gap Analysis

**Date:** 2026-07-17
**Method:** the "Essential Features of a Modern Test Automation Platform" checklist (30 areas) was evaluated against the actual codebase (backend, execution-engine, frontend, integrations). Every EXISTS claim below carries a file-path reference. Verdicts: **HAVE** / **PARTIAL** / **MISSING**, with a needed/not-needed recommendation.

**Headline:** TraceIQ already covers ~19 of the 30 areas — in places more than `SCOPE_NOTES.md` claims (statistical flake scoring and the visual perceptual diff are implemented despite being listed as deferred there). Real gaps cluster into four groups: frontend UIs for shipped backends, execution robustness (retry/mocking/artifacts), enterprise auth (SSO/MFA), and a small set of high-leverage step types (accessibility, web-vitals, data generation). Several checklist items are deliberately rejected as off-strategy.

---

## Scorecard

### 1. Test creation — PARTIAL (strong)
- **HAVE:** step-by-step builder (`frontend/src/pages/TestBuilder.tsx`), ~30-type step editor + element picker (`frontend/src/components/test-builder/StepComponent.tsx`, `ElementPickerDialog.tsx` — screenshot-based via `POST /api/inspect/page`), natural-language AI generation (`frontend/src/components/GenerateCaseDialog.tsx` → `POST /api/cases/generate` in `backend/app/api/case_generation.py`, grounded by live-probing the target URL), OpenAPI import (`POST /api/cases/from-openapi`), assertion generators (`SchemaGeneratorModal.tsx`, `FeedAssertionGeneratorModal.tsx`).
- **PARTIAL:** browser recorder — MV3 extension (`integrations/browser-recorder/`) records goto/click/fill/press-key only; no drag-drop, iframes, hover, network capture; unsigned.
- **MISSING (rejected):** multi-language code editor (JS/TS/Python/Java/C#/Kotlin) — off-strategy; TraceIQ is a step-JSON platform and `run-script` (JS in-page, gated Python) is the escape hatch. Drag-and-drop flow builder — the step builder already serves this.

### 2. Element locator engine — PARTIAL
- **HAVE:** raw Playwright selector strings (CSS, `xpath=`, text, role, testid engines work implicitly — `getLocator` in `execution-engine/src/core/test-executor.ts:65`); iframes via `switch-frame` (`worker.ts handleFrameSwitch`).
- **MISSING:** structured multi-strategy storage (primary/secondary/fallback), explicit smart-locator priority, dedicated shadow-DOM handling. **Needed later** — the `TestStep.intent` field plus AI heal is TraceIQ's chosen direction; a locator-strategy builder in the step editor is the pragmatic next step.

### 3. AI self-healing — PARTIAL
- **HAVE:** reactive heal proposals during execution (`execution-engine/src/worker.ts maybeProposeHeal` + `ai.ts AIEngine.healSelector`; LRU-cached, capped per run); proactive post-run heal (`backend/app/tasks/heal_tasks.py`, gated by `PROACTIVE_HEAL_ENABLED`); accept/reject endpoints rewrite the step. Legacy inline-retry path exists in the Python runner (`backend/app/runner/smart_page.py`).
- **MISSING (needed, P1):** opt-in *runtime* self-heal — retry the step with the healed selector when the DOM match is unique, flag the result. Also: consistent per-step DOM capture in the worker (blocks proactive heal today — `SCOPE_NOTES.md` Phase B polish).

### 4. Test execution — PARTIAL
- **HAVE:** distributed workers over Redis Streams; SEPARATE / CONTINUOUS / PARALLEL modes all routed (`backend/app/worker.py:121-143`, `dispatch_parallel_jobs` capped by `PARALLEL_MAX_CONCURRENCY`); cron scheduling (`backend/app/tasks/schedule_tasks.py`, `TestSchedule` model); job-abandonment retry ×3 (`execution-engine/src/core/job-queue.ts:324`); `goto` internal retry ×3.
- **MISSING (needed, P0):** per-test retry with exponential backoff — the `auto_retry` setting exists (`backend/app/settings_models.py:21`) but is never honored; `TestCaseResult.retry_count` column already exists to record it.
- **MISSING (needed, P1):** local execution bridge (`traceiq-worker` CLI + `GET /api/jobs/poll`) — designed in `SCOPE_NOTES.md` ("Local development bridge"), called the key SaaS blocker; not built.

### 5. Browser & device support — HAVE
chromium/firefox/webkit (`execution-engine/src/core/browser-manager.ts`); device emulation via Playwright `devices` + custom mobile descriptor, cross-browser UA overrides (`worker.ts getDeviceConfig`). Safari = webkit engine; Edge = chromium channel (add only on demand).

### 6. Assertions — PARTIAL (broad)
- **HAVE:** text / not-text / visibility / hidden / URL / count / attribute / value / regex / gt-lt (generic `assert`), json-path, json-schema (Ajv), xpath (feeds), visual match — all in `test-executor.ts`.
- **MISSING (needed, P0 — small):** `expect-title`, CSS-property assertion (computed style). Add to `test-executor.ts` + `StepComponent.tsx` dropdown + `backend/app/api/agent_reference.py` catalog.
- Accessibility assertion: see §11.

### 7. API testing — PARTIAL
- **HAVE:** REST via `http-request` (multi-URL batch, status/json-path/json-schema assertions, `test-executor.ts:119-318`); request chaining via `extract-value` + `{{env.KEY}}` / `{{secret.KEY}}` / `{{data.KEY}}` / runtime-var interpolation; `feed-check` (RSS/XML + XPath); cookies via storageState; bearer/JWT via headers.
- **MISSING (needed, P2):** GraphQL helper (query/variables fields + data-path assertions — raw POST works today). OAuth2 client-credentials token helper for tested apps.
- **MISSING (rejected):** SOAP, gRPC — no demand signal; off the target market.

### 8. Database testing — MISSING (rejected for now)
No SQL step anywhere. Deliberately rejected: platform-executed customer SQL is security-sensitive and off the UI-testing core. `run-script` is the workaround; revisit only on customer pull.

### 9. File validation — PARTIAL
- **HAVE:** `upload-file` (base64/paths), `download-file`, `handle-dialog` (`test-executor.ts:1062-1134`); image comparison via visual testing.
- **MISSING (later, on demand):** PDF/Excel/CSV/ZIP content validation steps.

### 10. Visual testing — HAVE
`expect-visual-match` step → pixelmatch + pngjs perceptual diff (`execution-engine/src/visual-diff.ts`), tolerance (default 1%), mask/ignore regions, dimension-mismatch padding, diff image uploaded; baseline CRUD + promote workflow (`backend/app/api/visual_baselines.py`, `POST /visual-baselines/promote`); worker resolve endpoint (`/api/internal/visual-baselines/resolve`).
- **MISSING (needed, P0 UI):** baseline approval/promotion UI. **Later:** AI visual comparison, layout-shift detection, responsive/dark-mode matrix comparisons.

### 11. Accessibility testing — MISSING (needed, P1)
No axe-core/Lighthouse anywhere (`page.accessibility.snapshot()` in `backend/app/runner/smart_page.py` is only a heal aid). Add a `check-accessibility` step via `@axe-core/playwright`: WCAG violations as assertions + report artifact. Cheap, high perceived value.

### 12. Performance monitoring — MISSING (needed, P1)
No web-vitals capture (LCP/CLS/TTFB/INP). Per-request timings exist in `execution-engine/src/core/network-interceptor.ts`; `controller/metrics-collector.ts` is infra metrics only. Add a `PerformanceObserver` collector injected on `goto`, store vitals on `TestCaseResult`, chart in analytics. Fits the "Server-Side Trace Intelligence" differentiator.

### 13. Network testing — PARTIAL (needed, P1)
- **HAVE:** full request/response logging (`network-interceptor.ts setupNetworkListeners`); interception that injects headers/query-params per domain (`setupRouteInterception` — always `route.continue()`).
- **MISSING:** response mocking (`route.fulfill`), request blocking (`route.abort`), latency simulation, offline mode, HAR capture (`recordHar`). Mocking/blocking unlock deterministic tests — build as `mock-response` / `block-request` steps.

### 14. Authentication (of tested apps) — HAVE (core)
storageState reuse + `is_auth_setup` login-capture pattern (`worker.ts:293,401,709`); Personas (`Persona` table + refresh task). **Missing/later:** dedicated OAuth device/SAML/Azure-AD login helpers — headers + auth-setup case cover most flows today.

### 15. Test data management — PARTIAL (needed, P1)
- **HAVE:** data-driven rows via `TestCase.dataset` (`backend/app/models.py:292`), one execution per row (`worker.py _expand_cases`), `{{data.KEY}}` interpolation.
- **MISSING:** faker generation (`{{fake.email}}`, `{{fake.uuid}}` — add `@faker-js/faker` to the engine's interpolator) and CSV/JSON dataset file import feeding `dataset`.

### 16. Environment management — HAVE
`ProjectEnvironment` (base_url + variables, default flag) + write-only Fernet-encrypted `ProjectSecret` (`backend/app/models.py:323-353`, `backend/app/core/secrets.py`, `backend/app/api/environments.py`); full CRUD UI (`frontend/src/pages/Environments.tsx`); `TestRun.environment_id` pins runs. Per-project scope (per-suite not needed yet).

### 17. Reporting — PARTIAL
- **HAVE:** trends/pass-rate/duration endpoints (`backend/app/api/analytics.py`), dashboard w/ 14-day trend (`frontend/src/pages/Dashboard.tsx`), cross-browser matrix (`TestMatrix.tsx`), screenshots/video/trace/network in `TestRunDetails.tsx`, AI failure analysis on runs and cases.
- **MISSING (needed, P0):** flaky-tests analytics view (backend flakiness endpoint exists; only a suite-level badge in `SuiteDetails.tsx` today), per-test historical trend charts.

### 18. Debugging — PARTIAL
- **HAVE:** custom trace timeline parsing Playwright `trace.zip` client-side (`frontend/src/components/TraceTimeline.tsx`) — action timeline, timings, errors; network tab; videos; screenshot gallery.
- **MISSING (later):** DOM snapshot rendering (data is in the trace, viewer doesn't render it), console logs in UI, pause/step-through live execution, HAR viewer.

### 19. Artifact management — PARTIAL
- **HAVE:** video (.webm), Playwright trace (.zip), screenshots (+ failure.png), visual-diff images, downloads → MinIO `test-artifacts` (`worker.ts uploadArtifacts`).
- **MISSING (needed, P0 — small):** console logs (captured to stdout only, `worker.ts:339`) and network logs (webhook payload only) not persisted as downloadable artifacts.

### 20. CI/CD integration — PARTIAL
- **HAVE:** GitHub Action (`integrations/github-action/`) — triggers run with git context, polls, idempotent PR comment with per-case results + artifact links + AI analysis, impact-analysis report, `fail-on` gate. MCP server (~30 tools, `integrations/mcp-server/`) incl. `select_tests_for_diff`. REST + API keys cover any CI generically.
- **MISSING (later, on demand):** GitLab CI / Jenkins / CircleCI / Bitbucket templates (thin wrappers over the REST API). Action polish: ship `dist/`, run only impact-matched cases (`SCOPE_NOTES.md` Phase D polish).

### 21. Notifications — HAVE
Email (SMTP), Slack (Block Kit), Teams (MessageCard) — `backend/app/tasks/notification_tasks.py`, per-project overrides; HMAC-SHA256-signed outbound webhooks with event filters (`backend/app/tasks/outbound_webhook_tasks.py`). Discord: rejected as dedicated channel — a generic webhook covers it.

### 22. Test management — PARTIAL (needed, P0)
- **HAVE:** hierarchical suites (self-referential `parent_id`), ownership (created_by/updated_by + agent provenance), `code_paths`, quarantine state.
- **MISSING:** tags, labels, priority, severity on `TestCase`/`TestSuite` (`backend/app/models.py:170-320` has none). Add JSON `tags` + `priority` columns, filter at dispatch, tag-based run selection (`POST /api/runs?tags=smoke`), UI filters.

### 23. Requirements traceability — MISSING (rejected)
`code_paths` → impact analysis (`POST /api/runs/impact-analysis`) is TraceIQ's agent-native answer to traceability; classic requirement↔defect matrices are legacy-enterprise and off-strategy.

### 24. Defect integration — MISSING (needed, P2)
No Jira/Linear/GitHub Issues integration. Start with GitHub Issues (credentials already flow through the Action), auto-attach failure analysis + artifact links; Jira second.

### 25. User management — PARTIAL
- **HAVE:** multi-tenant RBAC (system/workspace/project/team scopes — `backend/app/services/rbac_service.py`, `access_service.py`), API keys (hashed, scoped, expirable — `models.py:641`), refresh-token rotation w/ family revocation, audit logs (`AuditLog`, `models.py:588`), rate limiting (slowapi).
- **MISSING (needed, P2 — enterprise):** SSO (OIDC first, SAML later), MFA (TOTP), IP allowlisting (IPs recorded on refresh tokens but not enforced).

### 26. AI features — PARTIAL (strong core)
- **HAVE:** failure analysis — heuristic classifier + structured-JSON LLM deepening → typed `FailureReport` (`backend/app/services/failure_analysis.py`, `schemas/failure_report.py`, driven by `tasks/analysis_tasks.py`); run-level rollup; TS-side analyzer (`execution-engine/src/controller/ai-analyzer.ts`); selector-heal (see §3); AI test generation grounded in live page probing (see §1); tautology detector; provider abstraction covering OpenAI/Anthropic/Gemini/Ollama/Groq (`backend/app/ai/providers.py`, `execution-engine/src/llm-provider.ts`).
- **MISSING (later):** root-cause correlation with app backend logs/DB (analysis inputs today: error, steps, HTTP status, network failures, console); AI chat assistant; AI test generation from Figma. Duplicate-engine cleanup: two failure-analysis implementations (Python service vs TS analyzer) and two heal paths (TS propose-only vs legacy Python `smart_page.py`) — pick the canonical path.

### 27. Analytics — PARTIAL
- **HAVE:** pass/fail/duration trends, flakiness endpoint, **statistical flake scoring with auto-quarantine** (alternation ratio over last 20 results, quarantine ≥0.4, release <0.15 — `backend/app/tasks/result_aggregator.py:262-373`, `FlakeRecord`), quarantined cases skipped at dispatch.
- **MISSING:** browser-coverage analytics, richer historical views (see §17).

### 28. Observability — PARTIAL (needed, P2)
- **HAVE:** stale-run cleanup on Celery beat (`backend/app/tasks/cleanup_tasks.py`, `result_aggregator.check_stale_runs`); engine-side queue/worker/throughput metrics in Redis (`execution-engine/src/controller/metrics-collector.ts`).
- **MISSING:** Prometheus `/metrics`, worker/queue-health admin endpoint (engine metrics unexposed via API), readiness vs liveness probes (`/health` is a bare 200).

### 29. Security — PARTIAL
- **HAVE:** Fernet-encrypted secrets, HMAC-signed webhooks, hashed API keys, audit logs, rate limiting, refresh-token replay revocation.
- **MISSING:** MFA, IP restrictions (see §25), signed artifacts (later). Also carry the known issues from `CLAUDE.md`: unscoped `DELETE /api/runs?all=true`, warn-only webhook/finalize checks, `CORS ["*"]` default.

### 30. Plugin system — MISSING (rejected)
Premature. MCP + outbound webhooks + REST API are the extensibility surface; revisit if a marketplace motion emerges.

### 31. Mobile app testing — PARTIAL (added 2026-07-25)
- **HAVE:** mobile **web** testing — Playwright device emulation (`devices` + custom mobile descriptors, cross-browser UA overrides) applied per job (`execution-engine/src/worker.ts getDeviceConfig`); `TestRun.device` flows through every dispatch path.
- **MISSING:** **native** app testing — installing an APK/IPA and driving it with taps/swipes. No Appium anywhere, no app-binary storage, no native step types.
- **Verdict: needed — new pillar (Phase MOB below).** Fits the executor keystone (`PLATFORM_VISION.md` §2) exactly: a `mobile_appium` executor is "a new worker image that claims from the stream and reports through the same contract". Suites, RBAC, schedules, artifacts, notifications, AI failure analysis all come for free; the real cost is device infrastructure (Android emulators are self-hostable, iOS simulators require macOS — device-cloud integration instead).

---

## Prioritized roadmap

### P0-launch — commercial blockers (product readiness, not features; see "Beyond the checklist" below)
0a. **Account lifecycle** — password reset (backend flow + wire the dead "Forgot?" link in `Login.tsx:369`), email verification on signup, self-serve account deletion.
0b. **Security hardening** — scope `DELETE /api/runs?all=true`, enforce (not warn) webhook/finalize checks, lock down CORS default, remove `/mock/bihar-election`.
0c. **Data retention** — age-based purge of TestRuns/results (Celery beat) + MinIO artifact lifecycle/scheduled `delete_objects` sweep.
0d. **Per-workspace quotas & queue fairness** — workspace concurrency caps at dispatch, fair claiming across tenants on `jobs:pending`.
0e. **Billing & metering** — plan tiers, run quotas, seat limits, Stripe; extend the `ai_generation_limit_daily` pattern into a general quota system.

### P0 — highest leverage, mostly small
1. **Frontend UIs for shipped backends** — CaseProposal review inbox (SCOPE_NOTES: "single most impactful gap"), visual-baseline approval, heal-proposal review, API-key & webhook pages, flaky-tests analytics view. Pure React work; all endpoints exist.
2. **Per-test retry with exponential backoff** — honor `auto_retry`; add retry/backoff settings to suite settings inheritance; record in `TestCaseResult.retry_count`; feed flake scoring.
3. **Tags/priority on tests** — model columns + dispatch filter + tag-based run selection + UI.
4. **Persist console + network logs as artifacts** — already captured; upload in `worker.ts uploadArtifacts`.
5. **Small assertion gaps** — `expect-title`, CSS-property assert (+ agent_reference catalog entry).

### P1 — differentiators & robustness
6. **Network mocking steps** — `mock-response` (route.fulfill), `block-request` (route.abort), latency injection.
7. **Accessibility step** — `check-accessibility` via `@axe-core/playwright`.
8. **Web-vitals capture** — LCP/CLS/TTFB/INP per `goto`, stored + charted.
9. **Test data generation** — faker interpolation (`{{fake.*}}`) + CSV/JSON dataset import.
10. **Local execution bridge** — `traceiq-worker` CLI + `GET /api/jobs/poll` (design already in `SCOPE_NOTES.md`).
11. **Opt-in runtime self-heal** — retry step with healed selector on unique match, flag result.

### P2 — enterprise readiness
12. **SSO (OIDC → SAML) + MFA (TOTP)** on the existing JWT/refresh infra.
13. **Observability endpoints** — Prometheus `/metrics`, queue/worker-health admin API, real readiness probe.
14. **Defect integration** — GitHub Issues first, then Jira.
15. **GraphQL helper** on `http-request`; OAuth2 token helper.
16. **HAR capture** as optional artifact.

### Phase MOB — native mobile app testing (§31; added 2026-07-25)

A new capability pillar on the executor keystone: `ExecutorType.MOBILE_APPIUM`. One architecture rule throughout — mobile jobs ride the existing dispatch/result contract (Redis stream in, `jobs:results` out), so aggregation, finalize, notifications, webhooks, and AI analysis are untouched.

- **MOB-1. Surface what exists (done already, expose it)** — mobile-web device emulation is shipped; make it visible in run UI + API docs.
- **MOB-2. Backend foundation** — `mobile_appium` executor value; `MobileAppBuild` registry (APK/AAB/IPA uploaded to MinIO under `app-builds/{project_id}/`, versioned); `TestRun.app_build_id`; `POST /api/runs` accepts `app_build_id`; dispatch routes mobile jobs to a dedicated `jobs:mobile:pending` stream (`mobile-workers` consumer group) so Playwright workers never claim them; presigned binary URL + capabilities in the job payload; native step-type catalog (`mobile-tap`, `mobile-swipe`, `mobile-type`, …) in `agent_reference.py`. **← implementation started on this branch.**
- **MOB-3. Android worker** — `execution-engine/src/mobile-worker.ts` claims from the mobile stream and drives Appium over the plain W3C WebDriver HTTP protocol (no heavy client dep). Self-hosted emulators via docker (`profiles: ["mobile"]` in compose); Appium server URL via `APPIUM_URL`. v1 mirrors local-worker v1 scope: single-test jobs, no artifact upload.
- **MOB-4. iOS via device cloud** — ✅ shipped 2026-07-26: `MOBILE_DEVICE_PROVIDER=browserstack|saucelabs|lambdatest` (`execution-engine/src/device-cloud.ts`) routes sessions to the cloud's WebDriver hub over the same protocol; binaries are auto-uploaded to the cloud's app storage (cached per build) and vendor capability blocks are injected. iOS = pick a cloud provider + upload an IPA. Never self-host macOS.
- **MOB-5. Port the AI differentiators** — ✅ shipped 2026-07-26: selector heal on Appium XML page source (`healMobileLocator` + runtime self-heal on unique match, same `SelectorHealProposal` pipeline as web), mobile-aware failure-analysis heuristics, and `mobile-expect-visual-match` (pixelmatch reuse, baselines keyed browser='mobile'). The Chrome recorder does not carry over (native recording is out of scope). **Phase MOB is feature-complete; remaining: e2e smoke against a real device/cloud account.**

### Rejected (do not build)
Multi-language code editor · SOAP/gRPC · database SQL steps (revisit on pull) · requirements-traceability matrices · plugin system · drag-drop flow builder · dedicated Discord channel.

---

## Beyond the checklist — product readiness

The checklist covers testing features; these SaaS-readiness gaps were verified separately (2026-07-17) and several are hard blockers for commercial use.

### Tier 1 — blockers before commercial launch
| Gap | Evidence |
|---|---|
| **Account lifecycle** — no password reset (the "Forgot?" link is a dead `href="#"`), no email verification, no self-serve account deletion (GDPR exposure) | `frontend/src/pages/Login.tsx:369`; `backend/app/api/auth.py` has only login/register/refresh/logout/me/permissions |
| **Billing & usage metering** — no Stripe/plans/run-quotas/seat-limits anywhere; the only limit in the product is the AI-generation daily cap | `Workspace.ai_generation_limit_daily` (`backend/app/models.py:104`) is the sole quota |
| **Data retention** — runs/results/artifacts grow unbounded; cleanup only marks stuck runs as ERROR; MinIO has no lifecycle policy and the `delete_objects` helper is never scheduled | `backend/app/tasks/cleanup_tasks.py`, `backend/app/core/storage.py` |
| **Tenant fairness** — single shared job stream + consumer group, only a global concurrency cap, rate limiting is IP-based on auth endpoints only; one busy workspace can starve all tenants | `execution-engine/src/core/job-queue.ts:135`, `backend/app/worker.py:495` (`PARALLEL_MAX_CONCURRENCY`), `backend/app/core/limiter.py` |
| **Known security issues** — unscoped `DELETE /api/runs?all=true`, warn-only webhook/finalize checks, `CORS ["*"]` default, live `/mock/bihar-election` stub | `CLAUDE.md` "Known issues"; `test_runs.py:323,399-427`, `main.py:56` |

### Tier 2 — high-value product additions
- ~~**Deployment-comparison UI**~~ — ✅ shipped 2026-07-26: "Compare Deployment" dialog on finished runs (`TestRunDetails.tsx`) → candidate run → `/runs/{id}/comparison` view (`ComparisonView.tsx`: verdict banner, regressed/recovered/unchanged tiles, per-test delta table with duration change; polls while the candidate runs).
- **Synthetic monitoring & alerting** — schedules + per-run notifications exist, but no failure-streak alerts, uptime dashboard, or SLA reporting; a small delta turns scheduled suites into a production-monitoring product (Checkly-adjacent revenue).
- **Test case versioning** — no revision history/diff/restore (only `AuditLog`, `models.py:588`). Matters doubly because AI agents edit tests: rollback is the safety net that makes auto-applying proposals acceptable.
- **Onboarding** — no sample-project seeding or first-run guide; with AI generation already built, "paste a URL → we generate your first suite" is the natural first-run flow.
- **Hosted API/MCP docs** — only default FastAPI `/docs`; an agent-first product needs a hosted, versioned API + MCP reference.

### Tier 3 — later / on demand
i18n (all frontend strings hardcoded English, no i18n library), AI chat assistant ("why did this fail?"), self-hosted/on-prem distribution, SOC 2 groundwork.

---

## Doc corrections found during this analysis
- `SCOPE_NOTES.md` listed statistical flake scoring and visual perceptual diff/baseline promotion as deferred — both are implemented (`result_aggregator.py`, `visual-diff.ts`, `POST /visual-baselines/promote`). Corrected in that file.
- Two overlapping failure-analysis engines and two heal paths exist (Python vs TS); the TS execution-engine appears canonical — the Python runner (`backend/app/runner/`) should be marked legacy or removed in a future cleanup.
