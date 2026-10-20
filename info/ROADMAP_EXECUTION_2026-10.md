# Roadmap execution — October 2026

Started 2026-10-15. Source: `info/FEATURE_ROADMAP_2026-10.md` (ten roles × run/test
lenses + 24 candidates). The owner asked for the whole list to be built in one
go and tested at the end. This file is the tracker: every item, its work
package (WP), branch and status. If a session is interrupted, resume here.

**Process.** Each WP is built on its own branch off `feature/enterprise-auth-ai`
(functional work never goes on `feature/redesign-a`), tested (unit + live-DB
where it touches the DB), then fast-forwarded/merged into the base. The base is
merged into `feature/redesign-a` after each batch and any UI conflicts are
ported to the redesign layout. Commits end with `[skip ci]`, no co-author
trailer. Migrations chain linearly; renumber `down_revision` on merge if two
WPs raced.

## Product decisions taken (owner may overrule)

| # | Question | Decision |
|---|---|---|
| 1 | Plugins / traceability (rejected in July) | Plugin SDK stays rejected (D19 not built). Traceability reopened only as part of tests-from-requirements (`RequirementLink` already exists). |
| 2 | Security testing suite buyer | Built, but opt-in: only allowlisted domains, mutating requests need the existing "authorized" attestation, deny passes only on explicit 401/403/404 or missing element. |
| 3 | In-app assistant or MCP only | Both. In-app assistant is read-only, RBAC-scoped to the caller, cites the records it used. |
| 4 | Cost in money or tokens | Both: tokens as today plus money from an admin-editable price table (per model, per worker-minute). |
| 5 | Product analytics source | Generic CSV/JSON journey import first; no vendor lock. |
| 6 | Old-worker support | Indefinite: every new worker field optional, backend copes with its absence. |

Items needing a real third-party account (Splunk/Elastic, PagerDuty/Opsgenie,
Jira/Linear, a cloud region) are built against the documented API and tested
with mocks/fakes; they are marked **mock-verified**.

## WP12 product defaults (2026-10-16, owner may overrule)

| # | Question | Decision |
|---|---|---|
| R1 | Digest recipients and channels | Reuse per-project notification rules: picked project members (access re-checked at send) + extra addresses; chat only to the project's own webhooks. Editing / send-now need editor. Fixes the existing report path, which posted to the instance webhook and emailed unchecked addresses. |
| R2 | Share links | Signed, expiring (default 7 days, max 90), revocable, token stored hashed, rate-limited, every view audited; redacted view (no artifacts, URLs or error text). |
| R3 | DORA source | Explicit deployment events (`POST` from CI with commit/env); change failure = deployment followed by an incident or failed post-deploy run within 24h; MTTR from incidents. |
| R4 | Cost rates | Admin-editable price table (per LLM model per 1M tokens, per worker-minute) in instance settings; worker minutes from result durations. |
| R5 | Manual testing | `manual` executor; tester records pass/fail/blocked per case per run; release sign-off honours `require_separate_approver`. |
| R6 | Journey analytics | Generic CSV/JSON page-view import (decision 5), matched against routes the cases visit. |
| R7 | SLOs | Per monitor: availability % and latency (p95 ms) targets over a rolling 30 days, error budget and burn rate. |

## WP13 product defaults (2026-10-16, owner may overrule)

| # | Question | Decision |
|---|---|---|
| C1 | Evidence pack integrity | Zip with a manifest listing every file's SHA-256, manifest signed with an HMAC key derived for this purpose (not the JWT key); verify endpoint. |
| C2 | Controls | SOC 2 (from docs/SOC2_CONTROLS.md) seeded as editable data; each control has an owner, evidence sources, a freshness SLA; exceptions need an approver other than the requester and expire (max 365 days). |
| C3 | Trust center | Per workspace, off by default, public page on an unguessable slug; posture shows aggregate status only (never finding details); documents only through expiring links an admin issues. |
| C4 | PoC "time saved" | Labelled estimate from editable assumptions (default 5 min per manual case execution). |
| C5 | Demo workspace | Separate workspace flagged demo, seeded with history, removable in one click, never contacts real sites (example.com / httpbin / local todolite). |
| C6 | Sizing calculator | Estimates with the formulas shown, derived from the documented per-worker and storage figures. |
| C7 | Integration test packs | Versioned JSON packs shipped in the repo (SSO, SCIM, webhooks); import creates a suite with variables to fill; import never contacts a target. |

## WP14 product defaults (2026-10-16, owner may overrule)

| # | Question | Decision |
|---|---|---|
| W1 | Assistant | Read-only tools only; every tool result filtered by the caller's own access (same checks as the endpoints); answers cite record ids, invented ids are dropped and uncited claims marked; text sent to the LLM is redacted; conversations kept per user 30 days and deletable; with the Null provider it explains AI is not configured. |
| W2 | AI opt-out | Workspace admins can switch off ALL LLM use for their workspace (heal, analysis, generation, assistant); default on (today's behaviour). Enforced server-side and on workers. |
| W3 | Agent budgets | Per agent session/task: max runs, LLM tokens, proposals, wall time; none by default, instance-wide default caps configurable; exceeding returns 429 with the reason and is logged. Per-agent kill switch. |
| W4 | Policy engine | One `evaluate(action, context)` entry point wrapping today's checks without changing outcomes (parity tests); every deny logged with the rule that fired. |
| W5 | Heal | Several candidates (LLM + deterministic role/text/test-id strategies), scored; accept rechecks uniqueness against the stored DOM snapshot and refuses if not unique; DOM redacted before any LLM call. |
| W6 | API fuzzing | Opt-in; allowlisted domains only; read-only (GET/HEAD) unless the scan carries the existing "authorized" attestation; rate-limited; a deny passes only on explicit 401/403/404; findings become SecurityFindings. Templates: ID swap/IDOR, mass assignment, missing auth, role escalation, negative/boundary payloads from the OpenAPI schema, replay. |
| W7 | Visual | Element baselines via a selector screenshot; related changes grouped across cases in one run; comment threads on reviews. |

## WP15 product defaults (proposed 2026-10-16, CONFIRMED by owner 2026-10-20 as written)

Inventory 2026-10-16 (before proposing): ALREADY BUILT and therefore not in
WP15 — `mock-response` (static stub via page.route) + `block-request` steps;
`check-accessibility` step (axe-core, severity threshold, report-only mode)
and Storybook a11y cases; MV3 Chrome recorder (`integrations/browser-recorder/`,
dev-mode install). Per-job browser contexts exist; arbitrary code only runs via
`raw_playwright`, which is off by default. CONFIRMED MISSING: RLS (isolation is
Python-only, one shared bucket), image signing/SBOM, any browser E2E of
TraceIQ in CI (CI only builds the frontend; 18 vitest files), Node worker
tracing, stored a11y results (violations live only in step evidence — no
register, waivers, trend or gate), HAR replay / sequenced mocks, generated API
client, shared vector files for the redaction and heal twins (only the WP14e
visual key has them).

| # | Question | Decision |
|---|---|---|
| H1 | Tenant isolation in the DB (15a) | Postgres row-level security as a SECOND wall behind the Python checks, never instead of them. Policies on workspace/project-owned tables keyed on per-transaction settings (`SET LOCAL traceiq.workspace_ids`/`project_ids`) the session dependency sets from the principal; Celery/beat/migrations use a separate BYPASSRLS role. Rollout behind an instance setting: `off` → `log` (policy violations counted, nothing refused) → `enforce`. Start with the high-value tables (runs, results, cases, suites, project secrets, findings, artifacts index); `tests/test_rls_plan.py` fails for a new tenant-owned table without a policy, like the purge plan. Live tenant-isolation test re-run with the Python checks bypassed must still deny. |
| H2 | Supply chain (15b) | Release workflow (still manual-dispatch) signs the three images with cosign keyless (GitHub OIDC) and attaches a CycloneDX SBOM per image; SELF_HOSTING.md shows `cosign verify`. FastAPI/Starlette major bump on its own commit with the full suite. |
| H3 | Worker observability (15b) | OpenTelemetry in the Node worker on the same `OTEL_EXPORTER_OTLP_ENDPOINT`, trace id propagated from dispatch so one run is one trace across backend → Celery → worker. No-op when unset. |
| H4 | Legacy CONTINUOUS engine (15b) | Plan + deprecation, not deletion: route CONTINUOUS jobs through the distributed worker (which already supports continuous sub-suite jobs), warn at boot when `server.ts` is used, remove one release later. Until then it is documented as not honouring ai_allowed / egress. |
| H5 | TraceIQ's own E2E (15c) | Playwright suite in `e2e/` against the compose stack, CI job (CI stays manual-dispatch): login, create suite + case, run, result visible, every nav destination loads with no page error and no 5xx. Real-service checks (SAML, PagerDuty/Opsgenie, Jira/Linear, Splunk/Elastic) are a documented manual checklist with owner-supplied sandbox accounts; results recorded in the tracker, credentials never committed. |
| H6 | Accessibility register (15d) | `check-accessibility` outcomes stored per run (rule id, impact, target selector redacted, count — never page text). Known-issue waivers per project (rule + selector, reason, owner, expiry ≤ 90 days, like flake quarantine). Trend page; opt-in gate check "no NEW violations at/above serious" (waived ones excluded). |
| H7 | Mocks (15d) | `mock-response` gains `sequence` (nth call → nth response) and a `har-replay` step (`routeFromHAR`, HAR uploaded to the object store under the project, redacted on upload, not-found = fall through or abort per `params.not_found`). HAR recording stays capture-level `full` only. |
| H8 | Developer (15e) | Frontend API types generated from the backend OpenAPI (`openapi-typescript`, types only, no runtime client), adopted file by file with a CI drift check. Shared JSON vector files consumed by BOTH twin test suites for redaction and heal ranking. VS Code extension and recorder fidelity stay in SCOPE_NOTES (not WP15). |

## WP15f / WP16 product defaults (2026-10-20, owner may overrule)

Source: owner asked for a review of an external (ChatGPT) product brief on
2026-10-16. Most of it was already built (WP0–14); these are the gaps the
code confirmed. The brief's six new doc files were NOT adopted (they duplicate
CLAUDE.md / info/ / docs/); its navigation demotions (trust center, PoC, DORA)
and an "Add Tests" consolidation remain owner questions, redesign branch only.

| # | Question | Decision |
|---|---|---|
| L1 | Run cancel (15f) | `POST /api/runs/{id}/cancel` (editor; API keys under the same rules + agent kill switch). Pending jobs are skipped on claim via a `run:cancelled:<id>` Redis key; a running case stops before its next step, then suite teardown + cleanup still run (WP5 contract). Cancelled runs send no failure notifications, pages or outbound "failed" webhooks; they get their own `run.cancelled` event. Audited. |
| L2 | Terminal states (15f) | `TestStatus` gains `CANCELLED` and `TIMED_OUT` (native enum, uppercase labels; `ALTER TYPE … ADD VALUE` outside a transaction). Results gain `error_kind` = `test`/`setup`/`infrastructure`/`timeout`/`cancelled`/`not_executed`; absent (old worker, decision 6) = derived from the existing error text. Gate/readiness count cancelled and timed-out results as missing data, never pass or fail; flake scoring ignores infrastructure/cancelled. |
| L3 | Webhook deliveries (15f) | `webhookdelivery` row per attempt (event, run, attempt, status code, duration, error truncated + redacted; response body never stored), 30-day sweep. Every payload carries `X-TraceIQ-Delivery` (stable id for receiver dedupe). `POST …/webhooks/{id}/deliveries/{d}/redeliver` (workspace admin) re-sends the original payload, freshly signed. |
| L4 | Gate policy versions (15f) | Every gate-policy PUT writes an immutable `gatepolicyversion` row (canonical JSON + SHA-256). Each gate evaluation / readiness verdict stored or returned with the version id and the run ids it used; `GET …/quality-gate?policy_version=` re-evaluates a past decision. |
| V1 | Trace correlation (16a) | Opt-in per project. Worker adds W3C `traceparent` only to requests to the project's own hosts (environment base URL ∪ allowlist — never third parties); span ids recorded in step evidence. Backend reads traces from a configured Jaeger/Tempo query API (instance setting, net_guard). No telemetry → shown as "no telemetry", never inferred. Failure investigation may cite `s3.trace` refs. |
| V2 | OpenAPI breaking-change diff (16b) | Keep spec history per project (today one doc). Diff classifies breaking (removed operation/response field/status, new required request field, type change, enum narrowed) vs non-breaking, with fixtures. Optional gate check "no breaking change vs baseline spec"; JSON + SARIF export. |
| V3 | Environment locks (16c) | Opt-in `exclusive` per environment. Dispatch takes a Redis lease (TTL + heartbeat); other runs on it DEFER (like the concurrency cap), never fail. Holder visible in the UI; force-release = editor, audited. |
| V4 | Validated repairs (16d) | Accepting a heal or case-edit proposal can first run a validation run: the affected case with the change applied to that run only (overlay, suite untouched). Proposal states `validated`/`validation_failed`. Workspace option "require validation before accept" (default off); `suite.protected` forces it and disables auto-apply there. |
| V5 | Test ownership (16e) | TestCase `owner_user_id`, `backup_owner_user_id`, `last_reviewed_at`; review interval inherited from the suite chain; "needs review" list; owner optionally notified on first failure. |
| V6 | Pilot value metrics (16f) | `/reports/value`: time to diagnosis (first failure → triage status / ticket / fix), AI classification accuracy from 👍/👎 on hypotheses (reviewed sample, sample size shown), CI time saved by targeted runs (labelled estimate vs the last full run), proposal accept/reject/revert rates. Every number labelled measured or estimated. |

## Status legend
`done` merged into base · `wip` in progress · `todo` · `skip` (decision above)

## Work packages

| WP | Scope (roadmap ids / role items) | Status | Branch |
|---|---|---|---|
| 0 | Targeted runs (B7), flake owners/expiry/attempts (A3 part, PM "owners on quarantined flakes"), TOTP step | done | (merged) |
| 1 | **Release readiness**: gate checks for flaky cases, critical-priority coverage on the commit, evidence age; readiness view with per-check explanations and linked requirements (B8, PM/CPO/CTO) | done | feat/wp1 |
| 2 | **Test inbox + webhook capture**: email inbox step (receive mail, extract link/code), webhook capture step (full-stack) | done | feat/wp2 |
| 3 | **Per-step evidence → failure investigation v2**: per-step console/network/DOM/screenshot key, ranked hypotheses linked to evidence, observed vs likely cause, history + git correlation (A2); fail-on-JS-errors option (frontend) | done | feat/wp3 |
| 4 | **Identity matrix + access control**: AccessMatrix, per-persona fan-out, allow/deny verdicts → SecurityFinding, ID-swap replay (C11, AppSec access matrix); security assertion steps (headers, cookie flags, blocked-after-N, session-dead-after-logout) | done | feat/wp4 + feat/wp4b |
| 5 | **Run-scoped test data + hooks**: `{{run.id}}`/worker-index vars, seeded fakes, suite setup/teardown that run after failures, cleanup lifecycle (D16, D18, full-stack hooks) | done | feat/wp5 |
| 6 | **Inbound integrations**: Jira/Linear ticket + acceptance-criteria import → case proposals with source links (A1, PM); incident webhook → regression proposal, recurrence, escaped-defect tracking (A6, CPO) | done | feat/wp6 |
| 7 | **Security operations**: audit/security-event streaming to syslog/Splunk HEC/Elastic, security event alerts, quarterly access-review report, SARIF export, findings workflow (owner/deadline/accept-risk/reopen), retest-on-fix, leak detection findings (C12) | done | feat/wp7 |
| 8 | **Fleet & infra**: worker heartbeat + fleet page, DB-driven worker config, region-labelled workers + multi-region monitors, PagerDuty/Opsgenie + maintenance windows, KEDA ScaledObject, restore-drill script, IP allowlists on API keys/SCIM, optional mTLS, worker outbound allowlist, post-deploy trigger recipes, load-test + Prometheus overlay | done | feat/wp8 |
| 9 | **Estate & perimeter scanning**: domain estate score, third-party script inventory + change alerts (PCI 6.4.3/11.6.1), defacement monitoring, SPF/DKIM/DMARC + subdomain takeover + cert calendar, WAF validation, origin bypass, TLS grade + port exposure | done (passive; active parked) | feat/wp9 |
| 10 | **Frontend quality**: screen-size sweep, broken-link crawler, Lighthouse/SEO budgets, locale/RTL/theme matrix, selector-health report, component (Storybook) targets | done | feat/wp10 |
| 11 | **Developer workflow**: reproduce-locally bundle + CLI replay (D15), git export of cases, PR comment with failing step + screenshot, API response-schema validation | done | feat/wp11 |
| 12 | **Reporting**: weekly digest, manual cases + tester sign-off, shareable read-only report, journey coverage map (A4, analytics import), business journey board, DORA, exec summary, portfolio dashboard, SLO reports, third-party dependency health (B9), cost intelligence in money (E23), A/B + flag coverage, cross-site benchmarking, flake trend/patterns (A3 rest) | done | feat/wp12a..d |
| 13 | **Solution & compliance**: compliance evidence pack, control model with owners/freshness/exceptions (C14), sizing calculator + seeded demo workspace, one suite vs N URLs side by side, integration test packs (SSO/SCIM/webhooks), PoC report, trust center (E24) | done ("one suite vs N URLs" delivered by WP12c benchmarks) | feat/wp13a..c |
| 14 | **AI & platform**: in-app assistant (E20), agent action log + per-task budgets (E21), heal candidate ranking + uniqueness recheck (A5), API authz/business-rule fuzzing + abuse templates (C10, C13), policy engine single evaluation point (E22), visual change grouping/comments/element baselines (D17) | done (2026-10-16; C10/C13 parked — see SCOPE_NOTES) | feat/wp14a,b,c,e |
| 15 | **Hardening + quality**: database-level tenant isolation (Postgres RLS), supply chain (signed images + SBOM, FastAPI/Starlette major bump, worker OpenTelemetry, legacy CONTINUOUS engine retirement plan), TraceIQ's own browser E2E suite in CI + real-sandbox verification of SAML/paging/trackers/SIEM, accessibility register (stored violations, known-issue waivers, trend, gate check), HAR record/replay + sequenced mocks, generated typed API client, shared twin test vectors | todo (proposed 2026-10-16; owner to confirm H1–H8) | feat/wp15a..e |
| 15f | **Run lifecycle**: cancel, CANCELLED/TIMED_OUT + error kinds, webhook delivery log + redelivery, versioned gate policies (L1–L4) | todo | feat/wp15f |
| 16 | **Differentiation**: trace correlation with the app under test, OpenAPI breaking-change diff, environment locks, validated AI repairs, test ownership, pilot value metrics (V1–V6) | todo | feat/wp16a..f |
| — | Plugin / custom-step SDK (D19) | skip | decision 1 |

## Log
- 2026-10-15: tracker created; WP0 already merged. WP1, 2, 3, 6, 7 started in parallel (agents, own worktrees).
- 2026-10-15: WP1, WP3, WP2, WP6 merged (migrations chained c3→c2→c6). Security review fixes: bounded regex (RE2 backend, vm timeout worker), webhook query redaction, Mailpit SMTP localhost, tracker creds need workspace membership. WP5, WP8 started.
- 2026-10-15: WP4 (UI follow-up in wp4b) and WP7 merged; migration head c7a2b3c4d5e7. Review fixes: leak mask, alert webhooks via net_guard. WP9, WP10 started.
- 2026-10-15: WP9 agent was stopped by a safety check while writing active probing (ports, WAF strings, origin bypass, TLS cipher enumeration). Those parts were removed and parked in SCOPE_NOTES.md (feat/wp9) for the owner's decision; the passive half is being completed. WP4b merged.
- 2026-10-15 13:15: owner put WP12-14 ON HOLD — do not start them. Deploying merged WP1-4,6,7 to the local stack for the owner to check.
- 2026-10-15 15:45: WP5, 8, 9 (passive), 10, 11 merged with all review fixes; base head migration caa2b3c4d5eb; redesign merged (57a4ddf); local stack redeployed with everything. WP12-14 on hold.
- 2026-10-16: owner restarted WP12 (reporting). Inventory first, then four sub-packages built in parallel worktrees off feature/redesign-a (backend commits cherry-picked to the base, UI to redesign). Also shipped today outside the WPs: RustFS object store, schedule timezones, per-project notifications, settings split, suite retry control, video upload fix.
- 2026-10-16: WP12 merged. 12a digest fix (per-project recipients/webhooks, HTML, trend) + share links (hashed, expiring, token only in the URL fragment + POST body) + executive summary + portfolio; 12b DORA (deployment events from CI), monitor SLOs, flake trends/patterns, dependency health; 12c journey coverage import, business journeys, flag coverage + {{flag.NAME}}, cross-site benchmarks (comparison target_url now actually applied); 12d manual cases + tester screen + release sign-off (four-eyes), cost in money (empty price table until an admin sets it) + budget alerts. Migration chain ce12b3c4d5f1 → ce22b3c4d5f2 → ce32b3c4d5f3 → ce42b3c4d5f4. Sidebar "Reports" place. Integration fixes: JourneyConfig fields lost in a merge (caught by the migration check), trend chart stopped at yesterday.
- 2026-10-16: owner started WP13 (solution & compliance). Inventory first; "one suite vs N URLs side by side" counted as done (WP12c Benchmarks).
- 2026-10-16: WP13 merged. 13a controls (SOC 2 starter set on demand, automated evidence sources, freshness, four-eyes exceptions), signed evidence packs (HMAC manifest + verify), restore-drill results recorded (instance admin only), audit-log page; 13b trust center (off by default, opt-in aggregate posture incl. restore drill + controls count wired at merge, incident posts, gated documents via expiring links, request-access form) and PoC report (labelled time-saved estimate, shareable); 13c sizing calculator (formulas shown), one-click demo workspace (seeded history, no network, notifications blocked, one per user), SSO/SCIM/webhook test packs (validated against the step catalogue; secrets to the secrets store). Migrations cf12b3c4d5f5 → cf22b3c4d5f6 → cf32b3c4d5f7. Known cosmetic: sizing-calculator labels wrap in a narrow column.
- 2026-10-16: fixed test_siem time bomb (suite now 0 failures besides 4 e2e), sizing calculator layout. WP14 queued for a fresh session.
- 2026-10-16: WP14 started. Inventory found unredacted DOM sent to the LLM by runtime heal and no per-workspace AI opt-out; both fixed inside WP14 (c, b).
- 2026-10-16: WP14d (API authz / business-rule fuzzing + abuse templates, C10/C13) parked for the owner's decision, like estate active probing; WP14a, b, c, e in progress.
- 2026-10-16: WP14 merged (d parked). 14a read-only assistant with cited answers (honours the policy engine: workspace AI opt-out, token quota); 14b policy engine + Policies page, workspace AI opt-out carried to workers, agent action log, budgets and kill switch (Agents page); 14c ranked heal candidates, DOM redaction before any LLM call, uniqueness recheck on accept; 14e element baselines, stored diffs, change grouping, review comments. Migrations d012b3c4d5f8 → d022b3c4d5f9 → d032b3c4d5fa → d052b3c4d5fc. Known gaps: legacy server.ts ignores ai_allowed; kill switch not on every write for user-token callers; agents without a session header are not budgeted.
- 2026-10-16: WP15 proposed (hardening + quality) after an inventory; mock-response, check-accessibility and the Chrome recorder already exist, so WP15 builds on them instead. Not started — owner to confirm H1–H8.
- 2026-10-20: owner confirmed WP15 H1–H8 as proposed and accepted the brief-review gaps as WP15f (L1–L4) + WP16 (V1–V6). Nothing started yet.
