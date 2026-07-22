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
2. **OWASP ZAP baseline scan** as a `security` executor (passive spider, safe for
   prod). **Differentiator:** feed the Playwright `storageState` we already
   capture → **authenticated** scans that reach the real app behind login, which
   standalone DAST tools handle poorly.
3. **Active scanning behind an authorization gate:** `nuclei` (CVE/misconfig
   templates) + ZAP active scan (injection probing). Can damage the target and
   has legal exposure — must be gated (see guardrails).
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

### Import existing Playwright scripts — NEW, do first of this group
- New `raw_playwright` executor: run uploaded `.spec.ts` via
  `npx playwright test` instead of the step interpreter; parse the JSON reporter
  output into results at spec/test granularity.
- **Trade-off:** raw scripts are opaque to step-level AI heal, the `TraceTimeline`
  step mapping, and per-step editing. You gain zero-friction import; you lose the
  structured features until/unless converted to native steps.
- **Security:** this is arbitrary code execution in workers — sandbox the worker
  (locked-down container, no host mounts, egress limits) before letting tenants
  upload code.

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
- **PR-inline results + merge status check** — reuses the GitHub Action, git
  context, and impact analysis (`code_paths`). Post which tests ran + pass/fail +
  trace link on the PR and gate merge. Biggest dev-adoption lever, mostly wiring.
- **AI failure → suggested fix** — extend existing failure analysis/heal from
  "why it broke" to "here's a proposed diff." Leverages the LLM abstraction + MCP.
- **Coverage / gap analysis** — "which changed files / journeys have no test?"
  builds on `impact-analysis` (partially shipped, `SCOPE_NOTES` Phase C/D).

### For testers / QA — authoring speed + confidence
- **Recorder + self-healing / semantic selectors** — deferred in `SCOPE_NOTES`.
  Honest truth: **authoring cost is the #1 reason coverage stays low.** Highest-
  value tester investment, but a real build.
- **Flaky-test management** — flaky-tests page exists; make it actionable (flake
  score, auto-quarantine, retry policy, trend). Statistical flake scoring already
  shipped (`result_aggregator.py`).
- **Environments & secrets management** — reusable env configs + secret storage so
  one suite runs against dev/staging/prod. (Env management HAVE per gap analysis
  §16; secrets is the extension.)

### For product managers — visibility + decisions
- **Unified quality dashboard / scorecard** — pass-rate trends, flakiness,
  critical-journey coverage, and (as pillars land) API/load/security/a11y in one
  place, per release/PR. This is the *visible payoff* of the unification bet.
- **Release-readiness / quality gate** — policy engine: block release if critical
  journeys fail, pass rate < X, new flakes, or a security regression appeared.
  Turns raw results into go/no-go. (Deployment-comparison backend already exists;
  UI is a Tier-2 gap.)
- **Defect integration** — auto-file a bug (with trace) on failure; link tests to
  tickets. Already **P2** in the gap analysis (GitHub Issues → Jira). Note: full
  *requirements-traceability matrices* were **rejected** (gap analysis §23) — keep
  this to lightweight linking, not a matrix product.

### Cross-cutting — highest value-to-effort
- **Synthetic monitoring** — run the *same* tests on a schedule against production
  and alert on failure-streaks. Schedules + notifications already exist; this is a
  small delta that serves all three personas at once (dev: prod broke; QA:
  continuous coverage; PM: uptime of critical journeys) and doubles the value of
  every test already written. Gap analysis Tier-2 ("Checkly-adjacent revenue").
  **Best value-to-effort ratio on this page.**

---

## 6. Recommended sequencing

Weighted by leverage × reuse-of-existing-infra × serves-multiple-personas.
Priorities for *existing-product* items still come from `info/FEATURE_GAP_ANALYSIS.md`;
this sequence is for the *new* strategic direction.

| # | Item | Why here | Effort |
|---|------|----------|--------|
| 0 | **Keystone refactor** (§2) — executor type + type-aware result model | Everything below depends on it | Medium |
| 1 | **Synthetic monitoring** (§5) | Cheapest, broadest, reuses schedules+notifications | Small |
| 2 | **Passive security checks** (P-4 step 1) | Near-free, zero-risk, proves the findings result-model/UI | Small–Med |
| 3 | **Unified quality dashboard + release gate** (§5) | Makes the whole multi-pillar bet legible & actionable | Medium |
| 4 | **PR-inline results / status check** (§5) | Biggest dev-adoption lever, mostly wiring | Small–Med |
| 5 | **Import existing Playwright scripts** (§4) | Closest to what exists; highest onboarding pull | Medium |
| 6 | **ZAP authenticated scan** (P-4 step 2) | The security differentiator | Medium |
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
