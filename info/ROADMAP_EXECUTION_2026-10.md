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

## Status legend
`done` merged into base · `wip` in progress · `todo` · `skip` (decision above)

## Work packages

| WP | Scope (roadmap ids / role items) | Status | Branch |
|---|---|---|---|
| 0 | Targeted runs (B7), flake owners/expiry/attempts (A3 part, PM "owners on quarantined flakes"), TOTP step | done | (merged) |
| 1 | **Release readiness**: gate checks for flaky cases, critical-priority coverage on the commit, evidence age; readiness view with per-check explanations and linked requirements (B8, PM/CPO/CTO) | done | feat/wp1 |
| 2 | **Test inbox + webhook capture**: email inbox step (receive mail, extract link/code), webhook capture step (full-stack) | done | feat/wp2 |
| 3 | **Per-step evidence → failure investigation v2**: per-step console/network/DOM/screenshot key, ranked hypotheses linked to evidence, observed vs likely cause, history + git correlation (A2); fail-on-JS-errors option (frontend) | done | feat/wp3 |
| 4 | **Identity matrix + access control**: AccessMatrix, per-persona fan-out, allow/deny verdicts → SecurityFinding, ID-swap replay (C11, AppSec access matrix); security assertion steps (headers, cookie flags, blocked-after-N, session-dead-after-logout) | done (UI in wp4b) | feat/wp4 |
| 5 | **Run-scoped test data + hooks**: `{{run.id}}`/worker-index vars, seeded fakes, suite setup/teardown that run after failures, cleanup lifecycle (D16, D18, full-stack hooks) | wip | feat/wp5 |
| 6 | **Inbound integrations**: Jira/Linear ticket + acceptance-criteria import → case proposals with source links (A1, PM); incident webhook → regression proposal, recurrence, escaped-defect tracking (A6, CPO) | done | feat/wp6 |
| 7 | **Security operations**: audit/security-event streaming to syslog/Splunk HEC/Elastic, security event alerts, quarterly access-review report, SARIF export, findings workflow (owner/deadline/accept-risk/reopen), retest-on-fix, leak detection findings (C12) | wip | feat/wp7 |
| 8 | **Fleet & infra**: worker heartbeat + fleet page, DB-driven worker config, region-labelled workers + multi-region monitors, PagerDuty/Opsgenie + maintenance windows, KEDA ScaledObject, restore-drill script, IP allowlists on API keys/SCIM, optional mTLS, worker outbound allowlist, post-deploy trigger recipes, load-test + Prometheus overlay | wip | feat/wp8 |
| 9 | **Estate & perimeter scanning**: domain estate score, third-party script inventory + change alerts (PCI 6.4.3/11.6.1), defacement monitoring, SPF/DKIM/DMARC + subdomain takeover + cert calendar, WAF validation, origin bypass, TLS grade + port exposure | todo | |
| 10 | **Frontend quality**: screen-size sweep, broken-link crawler, Lighthouse/SEO budgets, locale/RTL/theme matrix, selector-health report, component (Storybook) targets | todo | |
| 11 | **Developer workflow**: reproduce-locally bundle + CLI replay (D15), git export of cases, PR comment with failing step + screenshot, API response-schema validation | todo | |
| 12 | **Reporting**: weekly digest, manual cases + tester sign-off, shareable read-only report, journey coverage map (A4, analytics import), business journey board, DORA, exec summary, portfolio dashboard, SLO reports, third-party dependency health (B9), cost intelligence in money (E23), A/B + flag coverage, cross-site benchmarking, flake trend/patterns (A3 rest) | todo | |
| 13 | **Solution & compliance**: compliance evidence pack, control model with owners/freshness/exceptions (C14), sizing calculator + seeded demo workspace, one suite vs N URLs side by side, integration test packs (SSO/SCIM/webhooks), PoC report, trust center (E24) | todo | |
| 14 | **AI & platform**: in-app assistant (E20), agent action log + per-task budgets (E21), heal candidate ranking + uniqueness recheck (A5), API authz/business-rule fuzzing + abuse templates (C10, C13), policy engine single evaluation point (E22), visual change grouping/comments/element baselines (D17) | todo | |
| — | Plugin / custom-step SDK (D19) | skip | decision 1 |

## Log
- 2026-10-15: tracker created; WP0 already merged. WP1, 2, 3, 6, 7 started in parallel (agents, own worktrees).
- 2026-10-15: WP1, WP3, WP2, WP6 merged (migrations chained c3→c2→c6). Security review fixes: bounded regex (RE2 backend, vm timeout worker), webhook query redaction, Mailpit SMTP localhost, tracker creds need workspace membership. WP5, WP8 started.
