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
| 14 | **AI & platform**: in-app assistant (E20), agent action log + per-task budgets (E21), heal candidate ranking + uniqueness recheck (A5), API authz/business-rule fuzzing + abuse templates (C10, C13), policy engine single evaluation point (E22), visual change grouping/comments/element baselines (D17) | hold (owner) | - |
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

