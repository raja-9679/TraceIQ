# TraceIQ Platform Vision & Roadmap

**Status:** strategic north-star. Nothing in this document is built yet unless a
line says otherwise. Where a capability already exists, it is marked ✅ and
points at the doc that owns it.

**Relationship to other docs:**
- `ARCHITECTURE.md` — authoritative picture of the system as it exists today.
- `info/FEATURE_GAP_ANALYSIS.md` — prioritized roadmap for the *current* product
  against a modern-platform checklist (P0/P1/P2). Still authoritative for those items.
- `SCOPE_NOTES.md` — what shipped on the AI-agent branch and what is deferred.
- **This file** — the bigger repositioning (from "UI testing tool" to "unified
  quality platform") and the new capability pillars that follow from it. When an
  item here overlaps the gap analysis, this file defers to the gap analysis for
  priority and only adds the strategic framing.

---

## 1. The thesis

TraceIQ started as a **UI testing & automation** product (see `CLAUDE.md`). The
direction from here is broader: **one platform a team uses to keep a product
high-quality across every layer** — frontend, API, backend behaviour, load, and
security — tied to the same commit, PR, and AI analysis.

The bet is **not** to out-feature the best-in-class tool in each category. We
will not beat k6 at load, ZAP/Burp at security, or Postman at API ergonomics
head-to-head. The defensible wedge is **unification**:

> One place where a PR triggers UI + API + load + security checks, every result
> is correlated against the same `git_commit` / `git_pr_url`, and AI analysis
> reasons across all of them — then a single quality gate decides go/no-go.

No single-category tool tells that story. That is what we build toward.

---

## 2. The one architectural bet (keystone)

Everything below gets cheap or expensive based on a single refactor. Do this
first; treat it as the enabler for the whole roadmap.

**✅ Backend plumbing implemented (2026-07-22)** — `ExecutorType` enum;
`executor` column on `TestCase` (declares the kind) and `TestRun` (denormalised
at dispatch); `result_kind` + `result_payload` on `TestCaseResult` for
type-aware results; executor carried in the Redis-stream job payload. Stored as
plain strings so new executor types need no migration. Defaults to
`ui_playwright`, so the existing path is unchanged. Migration
`d1e2f3a4b5c6_executor_keystone`. The executor *workers* themselves (raw
Playwright, load, security, …) are the follow-on items below.

**Generalize the run around an executor type + a type-aware result model.**

Today a run assumes one shape: an interpreted browser journey producing
step-level `TestCaseResult` rows. That assumption is the ceiling. The refactor:

- **`executor` on the run/job** — e.g. `ui_playwright` (today), `raw_playwright`,
  `selenium`, `api`, `load`, `security`. Dispatch already flows through
  `JobDispatcher` → Redis Stream (`jobs:pending`) → a worker that claims via
  `XREADGROUP` → reports through `POST /webhook` + `/finalize`. That contract is
  the reusable backbone; a new test type becomes **a new worker image that claims
  from the stream and reports through the same contract.**
- **Type-aware result payload** — `TestCaseResult` (step pass/fail + trace) fits
  UI only. Load produces time-series (RPS, p50/p95/p99, error rate); security
  produces findings (severity, CWE/OWASP, evidence). Add result shapes per
  executor rather than forcing everything into steps.

Once this exists, dispatch, per-workspace concurrency caps, artifacts (MinIO),
notifications, RBAC, and git context all come **for free** for every new pillar.
The expensive part of each pillar is then only its result schema + its UI — not
plumbing.

---

## 3. Capability pillars — *what* we test

### P-1 · Frontend / UI — ✅ HAVE (core product)
The existing Playwright step engine, suites, distributed workers, visual diff,
accessibility, and network mocking. Owned by `ARCHITECTURE.md` + gap analysis.

### P-2 · API testing — expand from partial
`http-request` and `feed-check` steps already exist (gap analysis §7, PARTIAL).
Formalize into a first-class API surface: request builder, schema/contract
assertions, request chaining, environments/secrets. Natural home for the
"backend testing" ask (see §5).

### P-3 · Load / performance testing — NEW pillar
- **Approach:** wrap **k6** (or Locust) as a `load` executor. Do **not** reuse
  Playwright for load — one browser per virtual user is prohibitively expensive.
- **New surface:** a `LoadRunResult` time-series shape (RPS, latency percentiles,
  error-rate-over-time) + threshold pass/fail + charts. This is effectively a
  second product surface; scope it deliberately.
- **Reuses:** PARALLEL dispatch + the per-workspace concurrency cap conceptually,
  though load concurrency is really k6's job.
- **Effort:** medium-high (the entire result/reporting surface is new).

### P-4 · Security testing — NEW pillar (phased, guardrailed)
Black-box DAST against deployed apps. Phased smallest/safest first — the ordering
*is* the recommendation:

1. **Passive checks on data we already capture** (cheap, zero-risk, ship first):
   security headers (CSP/HSTS/etc.), cookie flags, TLS/cert, information
   disclosure, client-side leaks (secrets in JS, localStorage, runtime CSP
   violations). Can **piggyback on every functional run** for near-zero extra cost.
   **✅ implemented (2026-07-22)** — analyzer `app/services/passive_security.py`
   inspects each run's captured `network_events` (document responses) +
   `response_headers` for missing/weak headers, insecure cookies, info
   disclosure, and plaintext transport; findings are per-origin de-duplicated
   and persisted as `SecurityFinding` rows (the unified findings model). Runs
   best-effort at finalize (`app/tasks/security_tasks.py`, gated by
   `PASSIVE_SECURITY_SCAN_ENABLED`, default on) and on demand via
   `POST /api/runs/{id}/security-scan`; read via
   `GET /api/runs/{id}/security-findings`. Migration `f3a4b5c6d7e8`.
   Follow-ups: TLS/cert inspection (needs a probe, not just captured headers),
   JS-secret/localStorage client-side leak checks, and a findings UI.
2. **OWASP ZAP baseline scan** — **✅ implemented (2026-07-22)** (passive
   spider, safe for prod). **Differentiator delivered:** the Playwright
   `storageState` we already capture is turned into a ZAP session cookie
   (`cookie_header_from_storage_state`) → **authenticated** scans behind login.
   `SecurityScan` model + `POST /api/projects/{id}/security-scan` (async, ZAP
   task `app/tasks/zap_tasks.py` drives spider→passive and maps alerts to the
   shared `SecurityFinding` model, `scan_type="zap"`). Migration `d7e8f9a0b1c2`.
   **Verified end-to-end against a live ZAP daemon (2026-07-22).**
3. **Active scanning behind an authorization gate** — ✅ wired (ZAP active scan;
   nuclei still a follow-up). Gated by `SECURITY_ACTIVE_SCAN_ENABLED` (global) +
   per-project `allow_active_scan`. Can damage the target — see guardrails.
4. **API & authorization security:** BOLA/IDOR, missing-token access, privilege
   escalation, rate-limit enforcement. High-signal, low-noise; can dogfood our
   own multi-tenant RBAC.

**On Burp:** do **not** rebuild Burp. It is an interactive, human-in-the-loop
pentesting tool; TraceIQ is unattended automation — wrong product model. **ZAP is
the embeddable open-source equivalent of Burp Scanner**, and wrapping it (steps
2–3) gets ~90% of the automatable value. The only Burp concept worth borrowing is
**Repeater/Intruder as an automated "replay-and-fuzz a request" step** — a
nice-to-have, later. Intercepting proxy and Collaborator/OAST are out of scope.

**Guardrails (design requirements, not afterthoughts):**
- Explicit "I own / am authorized to scan this target" gate + **verified-domain
  allowlist** — only scan registered, verified targets.
- **Passive by default**; active scanning is opt-in per target.
- Rate limiting + an audit trail of who scanned what.
- Defensive posture only — testing apps the customer owns/is authorized to test,
  never an offensive tool pointed at arbitrary third parties.

Status of these guardrails (item 6): the authorization attestation
(`authorized=true`), verified-domain allowlist, passive-by-default, and
active-scan double opt-in (global `SECURITY_ACTIVE_SCAN_ENABLED` + per-project
`allow_active_scan`) are **implemented and unit-tested**. Audit trail + rate
limiting on scan creation remain follow-ups.

**Rollout (item 6):** ZAP runs as an optional `zap` service behind the
`security` compose profile (`docker compose --profile security up -d zap`) — a
normal `up` does not start it, and it publishes no host port (the worker reaches
it internally at `zap:8090`). Scans are refused until `ZAP_API_URL` is set.
**✅ verified end-to-end (2026-07-22)** against a live ZAP 2.17 daemon: a baseline
scan of an authorized target produced 6 real findings (missing CSP/anti-
clickjacking/SRI headers, etc.), correctly mapped to severities and persisted as
`scan_type="zap"` SecurityFindings. Note: ZAP alerts are session-global, so a
fresh ZAP context per scan is a follow-up for cleaner isolation.

### P-5 · "Backend" testing — clarify, don't sprawl
"Backend" splits into two things:
- **Richer API / service testing** — an extension of P-2 (contract, DB-state
  assertions via API, integration flows). In scope.
- **Ingesting the team's own unit/integration results** from their CI (we display
  + gate, we don't execute). Plausible later as a results-ingestion endpoint.
- **Direct database SQL steps** — the gap analysis (§8) **rejected** this for now
  ("revisit on pull"). Keep rejected unless demand appears.

---

## 4. Onboarding & interop — *getting everyone in*

Most teams already have Selenium and/or Playwright assets. The onboarding story
is how we lower the switching cost.

### Import existing Playwright scripts — **✅ implemented (2026-07-22)**
- New `raw_playwright` executor: runs an uploaded spec verbatim via the
  Playwright test runner (`playwright test --reporter=json`) instead of the step
  interpreter; the JSON report is parsed into results at spec/test granularity.
- **Backend** (validated): `TestCase.raw_script` (migration `c6d7e8f9a0b1`);
  `executor`/`raw_script` carried in the job payload;
  `POST /api/cases/import-playwright` creates a raw case from a script.
- **Worker** (implemented + type-checked + parser unit-tested):
  `execution-engine/src/raw-playwright-runner.ts` + a `raw_playwright` branch in
  `worker.ts`, reporting at spec/test granularity via `test_results`; added the
  `@playwright/test` dependency.
- **Trade-off:** raw scripts are opaque to step-level AI heal, the `TraceTimeline`
  step mapping, and per-step editing. Zero-friction import; you lose the
  structured features until/unless converted to native steps (that's the
  Selenium-converter direction below).
- **Security / rollout (required before real use):** executes arbitrary user
  code. **Gated by `RAW_PLAYWRIGHT_ENABLED=true`** (default off) and **requires a
  worker-image rebuild** — the current image lacks `@playwright/test` + browsers,
  so `Dockerfile.worker` must be rebuilt (`npm ci` + `npx playwright install`).
  Enable **only on a sandboxed, network-restricted worker** (no host mounts,
  egress limits). End-to-end execution was therefore not run here; the
  JSON-report parser is unit-tested and the backend/payload path is DB-validated.

### Selenium → native converter — NEW (the adoption lever)
**Decision: build the LLM-assisted converter. Do NOT build a "run Selenium
as-is" executor.** Reasoning (recorded so we don't relitigate):
- **Run-as-is is a trap.** The "everyone already uses it" cohort runs Selenium
  inside full Java/TestNG/Maven or pytest *projects* — to run them as-is we'd
  become a general-purpose polyglot CI runner, a huge and dangerous build. And it
  delivers the *least* value: teams that only want to run Selenium keep using
  their own CI; TraceIQ's value (AI heal, trace timeline, unified platform) only
  exists once tests are in native format. Highest cost, lowest value, weakest lock-in.
- **The converter does the actual job** — moving teams *off* Selenium and *onto*
  native, where the product lives.

**Shape of the converter:**
- LLM-assisted, **human-in-the-loop** — fits the existing `LLMProvider`
  abstraction and the "agent reads source client-side, TraceIQ never does" model:
  the agent/MCP reads the Selenium files locally, an LLM converts each script,
  TraceIQ imports the result, a human reviews the diff.
- **Target:** native JSON steps where the script maps cleanly (unlocks all
  features); fall back to a **raw Playwright spec** (the `raw_playwright`
  executor) where it doesn't. Don't force complex logic into the finite step
  vocabulary.
- **Honesty in positioning:** it is a *migration accelerator* (~60–80% clean, flag
  the rest for review), **not** a one-click guarantee. Selling it as "upload
  anything, done" will burn trust; a broken migration is a worse first impression
  than none.
- **Validate demand first** — confirm with 2–3 real Selenium shops that "migrate
  our suite" is the actual blocker before the full investment.

---

## 5. Value layer — *turning tests into quality outcomes* (per persona)

Pillars produce results; this layer turns results into decisions. Mapped to the
three personas the platform serves.

### For developers — shift-left, fast feedback
- **PR-inline results + merge status check** — **✅ backend + Action implemented
  (2026-07-22)**. Reuses the GitHub Action, git context, and impact analysis
  (`code_paths`). **CI-/VCS-agnostic and opt-in** per the requirement that git
  is optional: `GET /api/runs/{id}/report` returns a consolidated, git-optional
  report (results + security + quality-gate verdict + ready-to-paste markdown),
  keyed by `run_id` so it works with no git at all. Per-project `CiSettings`
  (`enabled` / `enforce_gate` / `post_pr_comment`, default disabled) via
  `GET/PUT /api/projects/{id}/ci-settings` control whether/how CI blocks —
  teams not using CI or git are unaffected. The GitHub Action now consults the
  server-side gate and honours these settings (git-optional: gate falls back to
  the latest run). Migration `b5c6d7e8f9a0`. Biggest dev-adoption lever.
  Follow-ups: GitLab CI / Jenkins templates over the same REST report; native
  commit-status API (not just PR comment).
- **AI failure → suggested fix** — extend existing failure analysis/heal from
  "why it broke" to "here's a proposed diff." Leverages the LLM abstraction + MCP.
- **Coverage / gap analysis** — "which changed files / journeys have no test?"
  builds on `impact-analysis` (partially shipped, `SCOPE_NOTES` Phase C/D).
- **Failure triage + de-duplication** — **✅ implemented (2026-07-22)**. Failing
  results are fingerprinted (`app/services/failure_signature.py`) into
  per-project `FailureCluster`s so one root cause is one triage item, not N.
  Clustered at finalize; triage states (open/investigating/resolved/ignored) +
  assignee; **one ticket per cluster** via `POST /api/failure-clusters/{id}/ticket`.
  UI: Triage page. Migration `f9a0b1c2d3e4`. Validated on real failures
  (9 results → 4 clusters with cross-run dedup).

### For testers / QA — authoring speed + confidence
- **Recorder + self-healing / semantic selectors** — **partially shipped**.
  *Self-healing:* reactive heal (suggests a fix) shipped earlier; **runtime
  self-heal** now added (2026-07-22) — on a selector failure the worker asks the
  LLM for a replacement and, when it *uniquely* matches the live DOM, retries the
  step so the run recovers instead of failing, recording the old→new suggestion
  for a durable fix. Opt-in via `RUNTIME_HEAL_ENABLED` (needs an LLM provider);
  `execution-engine/src/worker.ts::tryRuntimeHeal`. Needs the worker-image
  rebuild + LLM to run e2e. *Recorder:* the MV3 scaffold
  (`integrations/browser-recorder/`) already records goto/click/fill/press-key
  with stable selectors + `intent`; higher fidelity (more step types, assertion
  capture, drag/hover/iframe) is client-side work that needs manual Chrome
  testing — the remaining part of this item. Authoring cost is still the #1
  reason coverage stays low.
- **Flaky-test management** — flaky-tests page exists; make it actionable (flake
  score, auto-quarantine, retry policy, trend). Statistical flake scoring already
  shipped (`result_aggregator.py`).
- **Environments & secrets management** — reusable env configs + secret storage so
  one suite runs against dev/staging/prod. (Env management HAVE per gap analysis
  §16; secrets is the extension.)

### For product managers — visibility + decisions
- **Unified quality dashboard / scorecard** — **✅ backend implemented
  (2026-07-22)**. `GET /api/projects/{id}/quality?days=N` aggregates run
  health (pass-rate + daily trend), flakiness (flaky/quarantined counts),
  monitor uptime (up/down), and security findings (by severity) into one
  `QualitySnapshot`. This is the *visible payoff* of the unification bet.
  Follow-up: the React dashboard UI + per-commit/PR drill-down.
- **Release-readiness / quality gate** — **✅ backend implemented (2026-07-22)**.
  `GET /api/projects/{id}/quality-gate?git_commit=…` evaluates the runs for a
  commit/branch (or the latest run) against a per-project policy
  (`min_pass_rate`, `max_high/medium_severity_findings`, `require_monitors_up`)
  and returns a go/no-go with per-check detail. **Principal auth (JWT or API
  key)** so CI/agents can gate merges; **fails closed** when no finished run is
  found. Policy is stored on `Project.quality_gate_policy` (migration
  `a4b5c6d7e8f9`) and read/written via `GET/PUT .../quality-gate/policy`.
  This is the server side of item 4's PR-inline gating.
- **Defect integration** — **✅ implemented (2026-07-22)**. File a ticket from a
  run with its trace/video/screenshots attached. Provider abstraction
  (`app/services/issue_trackers.py`) for **Jira, iTop, and GitHub Issues**;
  workspace-scoped configs with Fernet-encrypted credentials
  (`/api/workspaces/{id}/issue-trackers`); `POST /api/runs/{id}/tickets` creates
  the ticket via a Celery task that pulls artifacts from MinIO and uploads them
  (GitHub has no attachment API, so it embeds signed links instead). UI: Issue
  Trackers config page + a "Create ticket" dialog on the run detail page.
  Migration `e8f9a0b1c2d3`. Orchestration validated with mocked providers/MinIO;
  live create needs a real Jira/iTop instance. Full *requirements-traceability
  matrices* remain **rejected** (gap analysis §23) — this is lightweight linking.

### Cross-cutting — highest value-to-effort
- **Synthetic monitoring** — **✅ backend implemented (2026-07-22)**. Run the
  *same* tests on a schedule against production and alert on failure-streaks.
  Schedules + notifications already exist; this was a small delta that serves all
  three personas at once (dev: prod broke; QA: continuous coverage; PM: uptime of
  critical journeys) and doubles the value of every test already written. Gap
  analysis Tier-2 ("Checkly-adjacent revenue"). **Best value-to-effort ratio on
  this page.**
  - A `TestSchedule` with `is_monitor=true` becomes a monitor: each scheduled run
    is recorded as a `MonitorCheck`, a consecutive-failure streak drives DOWN/
    RECOVERY alerts (Slack/Teams, deduped on state transitions via
    `last_alert_state`), and uptime/SLA is computed from the check log.
  - Alerting/eval: `app/tasks/monitor_tasks.py::evaluate_monitor_for_run`, hooked
    best-effort at run finalize. Status API: `GET /api/schedules/{id}/monitor`
    and `GET /api/schedules/monitors`. Migration `e2f3a4b5c6d7`.
  - Follow-ups: email alert channel (only Slack/Teams for now); an
    uptime/SLA dashboard UI; and a per-monitor prod-URL override so a monitor can
    target production without editing suite settings.

---

## 6. Recommended sequencing

Weighted by leverage × reuse-of-existing-infra × serves-multiple-personas.
Priorities for *existing-product* items still come from `info/FEATURE_GAP_ANALYSIS.md`;
this sequence is for the *new* strategic direction.

| # | Item | Why here | Effort |
|---|------|----------|--------|
| 0 | ✅ **Keystone refactor** (§2) — executor type + type-aware result model | Everything below depends on it | Medium |
| 1 | ✅ **Synthetic monitoring** (§5) | Cheapest, broadest, reuses schedules+notifications | Small |
| 2 | ✅ **Passive security checks** (P-4 step 1) | Near-free, zero-risk, proves the findings result-model/UI | Small–Med |
| 3 | ✅ **Unified quality dashboard + release gate** (§5) | Makes the whole multi-pillar bet legible & actionable | Medium |
| 4 | ✅ **PR-inline results / status check** (§5) | Biggest dev-adoption lever, mostly wiring | Small–Med |
| 5 | ✅ **Import existing Playwright scripts** (§4) | Closest to what exists; highest onboarding pull | Medium |
| 6 | ✅ **ZAP authenticated scan** (P-4 step 2) | The security differentiator | Medium |
| 7 | **Selenium converter** (§4) | Adoption lever; validate demand first | Medium–High |
| 8 | **Recorder + self-healing selectors** (§5) | Biggest coverage lever, but a real build | High |
| 9 | **Load testing** (P-3) | Valuable but effectively a second product surface | Medium–High |

---

## 7. Non-goals & guardrails

- **Don't chase feature count.** A quality product is judged on depth and polish,
  not breadth. Better to make the core loop — *author → run → analyze → gate →
  monitor* — excellent than to half-build ten pillars. Every pillar added is
  surface to maintain.
- **Don't build Burp** (interactive pentesting tool; wrong model). Wrap ZAP.
- **Don't build "run Selenium as-is"** (polyglot CI runner; highest cost, lowest
  value). Build the converter instead.
- **Security stays defensive & authorized** — verified-domain allowlist, passive
  by default, audit trail. No offensive tooling for arbitrary targets.
- **Respect prior rejections** (gap analysis §159): database SQL steps
  (revisit on pull), requirements-traceability matrices, plugin system,
  multi-language code editor, SOAP/gRPC, drag-drop flow builder.
- **Product-readiness blockers come first.** The Tier-1 commercial blockers in
  `info/FEATURE_GAP_ANALYSIS.md` (account lifecycle, billing/metering, data
  retention, tenant fairness, known security issues) gate commercial launch
  regardless of anything on this page.
