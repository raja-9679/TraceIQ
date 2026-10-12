# Feature roadmap — October 2026

**Date:** 2026-10-12 · **Branch:** `feature/enterprise-auth-ai`
**Shareable page:** https://claude.ai/artifact/LCuvPCFBWW8H1i74CeiS6j (private until shared)

This combines two analyses:

1. **Ten roles × two lenses.** For each role (senior full-stack, senior frontend,
   project manager, CPO, CTO, solution manager, infra manager, network security,
   application security, cyber security): what they need to **run TraceIQ** inside
   the company, and what they need to **test their own websites and apps** with it.
2. **24 extra candidates** (AI testing intelligence, reliability, advanced
   security, developer experience, AI-native platform), each checked against the
   code by searching by concept and synonym, not just by feature name.

It builds on `info/FEATURE_GAP_ANALYSIS.md` (July 2026). Two candidates collide
with that document's "do not build" list — see *Open questions*.

Status words: **Exists** (shipped), **Partial** (building blocks exist),
**Missing** (searched for, not found). "Missing" is never a claim that nothing
exists under another name — the search scope is recorded per item below.

---

## 1. Fix first — done 2026-10-12

Checking the 24 candidates turned up features that looked shipped but were
broken underneath. All are fixed; tests in `backend/tests/test_fix_first.py`,
`backend/tests/test_celery_routing.py`, `execution-engine/src/core/evidence.test.ts`.
Verified live on the local stack (real runs through the worker pipeline).

| # | Was broken | Fix | Verified live |
|---|---|---|---|
| 0 | **Seven Celery tasks never ran on any deployment.** No `task_default_queue`, and every deployment's workers consume only `main-queue`/`aggregator-queue`. Unrouted tasks went to the implicit `celery` queue: run notifications (email/Slack/Teams), outbound CI webhooks, **account emails (password reset, verification)**, AI failure analysis, proactive heal, persona refresh, tautology scan. Found while verifying #1. | `task_default_queue = "main-queue"` in `celery_app.py`; `test_celery_routing.py` fails if any task lands on a queue no worker consumes (and, in CI, checks the deployment files still consume exactly those queues). | 102 stranded tasks were on the local `celery` list; after the fix, refresh and analysis ran within seconds. |
| 1 | **Personas never reached the worker.** `TestRun.persona_id` existed but could not be set via the API and was never read by the dispatcher; refresh POSTed to a `/persona-refresh` endpoint on the legacy engine that was never implemented (and the legacy engine isn't in any shipped deployment). | `POST /api/runs` body `persona_id` (validated: same workspace, same project if project-scoped). Dispatcher injects the persona's session (replacing the project session) and merges its auth headers. Refresh is now a `job_type=persona_refresh` job on `jobs:pending`, mirroring discovery jobs; result parked in Redis (180 s TTL, deleted on read) and stored encrypted. | Same case **passed** as the persona and **failed** without it; refresh ran through the worker and stamped `last_refreshed_at`. |
| 2 | **Proactive selector heal could never fire.** It read a per-step DOM from `run.execution_log`, which the distributed path never writes, and only for single-case runs. | Worker records a compact body DOM per step (scripts/styles/svg stripped, input values dropped, 8 kB) **only** when the dispatcher sets `capture_step_dom` — which it does only with `PROACTIVE_HEAL_ENABLED` and capture level `full` (a DOM snapshot can't be redacted any more than a trace). Stored per result in `result_payload.step_dom` (no run-row race); heal reads that, works for suite runs, dedupes pending proposals. | Unit-tested; not exercised live (needs `full` capture + LLM). |
| 3 | **AI generation faked a human review.** `mode=direct` stamped `last_human_reviewed_at=now`, hiding fresh AI cases from tautology detection and impact analysis's "needs review" signal. (The dialog using `direct` is fine: a human generating a case for themselves and reviewing it in the builder is a legitimate flow; forcing the proposal queue would strand solo users under `REQUIRE_SEPARATE_APPROVER`.) | No pre-stamp; a **human** save of an AI-authored case in `PUT /cases/{id}` stamps it. API-key saves never count as review. | Unit-tested. |
| 4 | **Failure analysis was blind to evidence already captured.** Console logs went only to a MinIO artifact; `network_failures` was a parameter nobody passed. | Worker sends redacted, capped `console_errors` per test (re-redacted on ingestion); analysis gets them plus this test's HTTP ≥ 400 responses from `run.network_events`. Also fixed a race: analysis was queued *inside* the aggregator transaction and could read the run before its network events were committed — now queued `after_commit`. | Fresh failing run's report carried the injected console error and the 404. |
| 5 | **`TestCaseResult.is_flaky` / `confidence` were never written.** | `flake_verdict()` in the flake scorer: flaky = passed only after a retry, or case flake score ≥ 0.4; confidence (that a failure is real) = 1 − flake score; passes carry none. Retries are never hidden. | Values present on live results. |
| 6 | **Visual review lost decisions.** "Keep baseline" was local UI state; promote overwrote the baseline object in place (no history, no rollback, approver not recorded). **Plus a cross-tenant hole:** `POST /visual-baselines` accepted any bare object key, presigned it back to the caller (read another tenant's artifacts) and workspace purge used baseline keys as delete *prefixes* (a row holding `runs/` would delete every tenant's run artifacts). | Versioned baseline keys; accept / keep / rollback recorded in the hash-chained audit trail; `GET …/history`, `POST …/keep`, `POST …/rollback` (only to images this baseline actually used, only inside its own folder). Bare keys confined to `baselines/{case_id}/`; purge only ever deletes `baselines/<case id>/` prefixes. UI: Keep persists, decision history with "Restore previous". | 400 on a foreign key, keep recorded with user + run, history returned, bad rollback refused. |
| 7 | **Heal accept could weaken assertions**, never checked for stale proposals, mutated the JSON column in place (may not persist), and **reject had no access check** (any user, any proposal id). | Assertion steps (`expect-*`, `assert`, `mobile-expect-*`, `count-children`, `verify-nth-child`, `check-accessibility`) refuse with 409; stale selector or missing step → 409; non-pending → 409; new list + `flag_modified`; reject checks editor access. | 409 on an `expect-visible` step over HTTP; proposal stayed pending. |

**Deploy notes.** Backend, frontend and worker images all changed (the worker
for persona refresh, console evidence and step DOM). Old workers keep working —
every new field is optional — but send no evidence. The 102 tasks already
stranded on a deployment's `celery` Redis list are **not** replayed by the fix
(they include weeks-old notifications and CI webhooks); inspect and `DEL celery`
deliberately.

### Found while cleaning up — still open

- **Project delete 500s** for any project that has test cases
  (`testcase_project_id_fkey`); `workspace_service.delete_project` doesn't
  cascade. The workspace purge (`services/purge.py`) does.
- **Suite delete 500s** once any of its cases has been edited
  (`testcaserevision_test_case_id_fkey`) — i.e. almost always.
- **Run delete 500s** for any run that produced a heal proposal
  (`selectorhealproposal_source_run_id_fkey`).

All three are the same shape: per-entity deletes predate tables added later.
`PURGE_PLAN` solved it for workspaces; per-entity deletes need the same.

---

## 2. Roadmap

Sizes: S = days, M = 1–3 weeks, L = more than a month.

### Quick wins
1. **Test email inbox + TOTP steps** (S) — unblocks real signup/MFA journeys.
2. **Targeted runs from impact analysis** (S–M) — impact analysis already picks
   cases, but `POST /runs` takes a suite plus at most one case, and the GitHub
   Action always runs the full suite.
3. **Flake owners, expiry, per-attempt retries** (S) — fields on `FlakeRecord`.
4. **Release readiness view** (M) — extend `QualityGatePolicy` with flakiness,
   critical-priority coverage and evidence age; keep per-check explanations.

### Foundations (each unlocks several features)
1. **Per-step evidence capture** (M) — console/network/DOM/screenshot per step.
   Unlocks failure investigation v2, safe self-heal, leak detection, replay
   bundle. (Fix-first #2 and #4 are its first slice.)
2. **Identity matrix runs** (M) — same cases under N personas with expected
   allow/deny. Unlocks access-control, tenant-isolation, business-logic and
   authz-fuzz testing. (Fix-first #1 is its prerequisite.)
3. **Run-scoped test data** (M) — `{{run.id}}`/worker-index variables, seeded
   fakes, suite setup/teardown hooks that run after failures.
4. **Inbound integrations** (M) — pull Jira/Linear tickets and incidents in;
   issue trackers are outbound-only today.

### Strategic bets (validate demand first)
- Security testing suite: access-control matrix, business-logic templates, authz
  fuzzing from OpenAPI, third-party script inventory (PCI DSS 4.0 6.4.3/11.6.1).
- Requirements → tests and incidents → regression tests.
- In-app assistant (the MCP server already covers this for agent users).
- Policy engine, cost intelligence, trust center, estate scanning, multi-region.

### Sequence
```
fix persona wiring ──► identity matrix runs ──► access-control / tenant isolation / authz fuzz / business logic
per-step DOM + console + network ──► evidence capture ──► failure investigation / self-heal / leak detection / replay
run-scoped test data ──► tenant isolation, business logic
inbound Jira / incidents ──► tests from requirements, incident → regression test
targeted runs + flake owners + failure investigation ──► release readiness
```

---

## 3. Top three implementation plans (outlines for review)

**1 · Per-step evidence → failure investigation.** Worker writes console,
failed requests, DOM excerpt and screenshot key per step (redacted, gated by
capture level). `FailureReport` v2: ranked hypotheses each linked to evidence
items, a separate `observed` list, prior-failure history from `FailureCluster`
and the run's `git_commit`. UI: per-step evidence timeline; "Observed" kept
apart from "Likely cause". Old workers send nothing; analysis must cope.

**2 · Identity matrix → access-control testing.** `AccessMatrix` per project:
rows = cases/requests, columns = personas, cells allow|deny + expected
status/element; optional "swap owner" replay with another persona's resource id.
Fan out one job per (case, persona). A deny passes only on explicit 401/403/404
or a missing element — never a timeout. Failures become `SecurityFinding`
(`access_control`). Allowlisted domains only; mutating requests need the
existing active-scan "authorized" attestation. Use TraceIQ's own isolation
tests as the fixture app.

**3 · Targeted runs → release readiness.** `POST /api/runs` accepts
`case_ids[]`; GitHub Action `mode: targeted` with full-suite fallback on
unmatched files or too many cases without `code_paths`. Gate checks for max
flaky cases, critical-priority cases passed on this commit, evidence age.
Readiness view with linked `RequirementLink`/Jira items and missing data
labelled. A targeted run never reports "all passed" when cases were skipped.

---

## 4. Gap matrix — 24 extra candidates

| # | Feature | Status | What exists | Main gaps | Size |
|---|---|---|---|---|---|
| A1 | AI test generation from requirements | Partial | `/cases/generate` (plain English), `/cases/from-openapi`, CaseProposal queue, `RequirementLink` traceability | No Jira/Linear import, no acceptance-criteria parsing, no auto-link to source, no ambiguity detection | M |
| A2 | AI failure investigation | Partial | `failure_analysis.py` heuristics + LLM `FailureReport`, `FailureCluster` triage; console + network evidence (fix-first #4) | No DOM/trace/screenshot analysis, single unranked hypothesis, no evidence links, no git/history correlation | L |
| A3 | Flaky-test intelligence | Partial | `FlakeRecord` alternation score, auto-quarantine/release, `auto_retry`, flakiness analytics; per-result flags (fix-first #5) | Per-case only; no trend, owner, expiry, pattern detection; attempts not shown | M |
| A4 | Journey discovery & coverage gaps | Partial | `discovery.ts` crawler, `/apps/{id}/surface`, impact analysis, priority/tags | No crawl-vs-covered diff, no journey model, no analytics import | M–L |
| A5 | Controlled self-healing | Partial | `SelectorHealProposal`, runtime heal (unique match), revisions + restore; guards + working proactive path (fix-first #2, #7) | LLM-only candidates (no role/name ranking), no recheck of uniqueness on accept, rationale-only evidence | M |
| A6 | Production → test feedback loop | Missing | Outbound Jira/GitHub/iTop tickets from runs and clusters | No inbound incidents, incident↔release link, regression proposal, recurrence tracking | L |
| B7 | Change-impact analysis | Partial | `/runs/impact-analysis` with reasons + `suggested_action`, `code_paths`, MCP `select_tests_for_diff` | Can't run a selected set; Action always runs full suite; no fallback rule | S–M |
| B8 | Release risk scoring | Partial | `QualityGatePolicy` checks with actual/threshold/detail, quality snapshot, test effectiveness, Action gate | Pass/fail only; no change size, critical coverage, defect history or flakiness in the gate | M–L |
| B9 | Third-party dependency health | Partial | Monitors with failure streaks + alerts, status pages, http/oauth2/tls steps | No vendor entity, latency trend, maintenance windows, correlation with app failures | M–L |
| C10 | Business-logic security testing | Missing | Generic http-request, extract-value, interpolation, `SecurityFinding` | No scenario model, abuse templates, replay / expect-denied steps | L |
| C11 | Multi-tenant isolation testing | Partial | Personas (now wired, fix-first #1), comparison runs; TraceIQ's own isolation tests as know-how | No N-identity runs, allow/deny matrix or ID swapping | L |
| C12 | Secrets / sensitive-data leak detection | Missing | Redaction detectors (JWT, PAN/Luhn, Aadhaar/Verhoeff, key denylist) in both languages | Nothing raises findings from storage/bodies/console/downloads; no cloud-key or entropy detectors | M |
| C13 | API authz & business-rule fuzzing | Partial | OpenAPI parser (happy path only), ZAP imports OpenAPI, scope attestation | No ID/role mutation, boundary payloads, state transitions, seeds; nuclei unimplemented | L |
| C14 | Compliance control testing | Partial | `docs/SOC2_CONTROLS.md` (static), audit export + chain verify, `RequirementLink` | No Control model, owners, freshness, exceptions, evidence-pack export | M–L |
| D15 | CLI & local replay | Partial | `worker:local` polling worker, per-object artifact URLs, suite export, Action exit codes | No CLI binary, replay command/bundle, version check | M |
| D16 | Environment provisioning | Partial | `ProjectEnvironment`, `{{env.X}}` / `{{secret.X}}` | No setup/teardown hooks, seeding, readiness, drift, external secret store | L |
| D17 | Visual baseline governance | Partial | Promote, masks/tolerance, VisualReview, pixelmatch; versions + audited decisions + rollback (fix-first #6) | No grouping of related changes, comments, component/element baselines (capture is full-page) | M |
| D18 | Test data & synthetic identities | Partial | Datasets (`{{data.X}}`), `{{fake.*}}`, extract-value, personas | No seeds, run/worker namespacing, cleanup lifecycle, identity profiles | M |
| D19 | Plugin / custom-step SDK | Missing (**rejected in July**) | Fixed step catalog + `run-script` (page-scoped JS, gated Python) | Everything | L |
| E20 | Natural-language assistant | Missing | MCP server is the external equivalent | No chat endpoint/UI, RBAC-scoped tool calls, citations | M–L |
| E21 | AI agent execution with approval gates | Partial | Proposal queue, auto-apply threshold, separation of duties, kill switch, caps, agent provenance | No in-app agent loop, per-task budgets, agent action log | M |
| E22 | Customer policy engine | Partial | `DataPolicy`, net_guard, allowed_domains, scan allowlists, instance policies, `max_concurrent_runs` | Scattered checks, no single evaluation point; no AI-model / secret-use / export policy | L |
| E23 | Usage, performance & cost intelligence | Partial | `LLMUsageEvent` by workspace/project/run/model, `UsageRecord`, plan limits, `/metrics` | No money, no worker CPU/memory/browser time, no queue wait, no budget alerts | M |
| E24 | Customer-facing trust center | Partial | Public status pages (uptime only), audit export + verify | No incident comms, gated compliance docs, posture page, signed evidence links | M |

Search scope: keyword and concept sweeps of `backend/app`, `execution-engine/src`,
`frontend/src`, `integrations/`, the live `/api/step-types` catalog, `docs/`,
`info/`, `SCOPE_NOTES.md`, `PLATFORM_VISION.md`.

---

## 5. Role summary

Full per-role detail is on the shareable page. The cross-role picks:

| Feature | Lens | Who |
|---|---|---|
| Test email inbox + TOTP steps | test | full-stack, PM, solution |
| Access-control matrix across personas | test | AppSec, full-stack, CTO |
| Third-party script inventory + change alerts | test | cyber, AppSec |
| Release readiness page | run | PM, CPO, CTO |
| Security alerts + SIEM streaming + PagerDuty | both | cyber, infra, network |
| Worker fleet page + DB-driven worker config | run | infra, network, AppSec |
| Estate scanning (many domains, one score) | test | cyber, network, CTO |
| Multi-region monitors | test | infra, network, CPO |

---

## 6. Open questions for the product owner

1. **Two candidates were rejected in July** (`FEATURE_GAP_ANALYSIS.md`):
   plugin system and requirements-traceability matrices. A `RequirementLink`
   traceability API shipped since anyway. Keep the plugin rejection? Reopen
   traceability as part of "tests from requirements"?
2. **First buyer for the security testing suite?** Testing customers' apps for
   access control and business logic carries authorization and safety scope;
   worth it with a design partner.
3. **In-app assistant, or MCP only?**
4. **Cost in money or tokens?** Needs a per-model price table and a worker-time
   rate — who owns those numbers?
5. **Product analytics source** for journey discovery (Amplitude, Mixpanel, GA)?
6. **Worker image cadence** — how long must the backend tolerate workers that
   send no evidence?
