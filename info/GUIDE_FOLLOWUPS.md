# User-guide follow-ups (2026-10-21)

Found by the writers of the /docs/ user guide while checking every page against its code. Nothing here is fixed yet unless marked. Pages with a demo gap show their empty state, described from code; adding the data to backend/app/services/demo_workspace.py (enterprise branch) and re-running e2e/docs-capture/run.sh pages/<spec> would give them real-looking pictures.


## Demo gaps (demo_workspace.py)
- reports1/Delivery: deployments lack git_commit/commit_at (lead time —); no incidents (MTTR —). Add commits matching demo runs + 2-3 incidents within 24h of a deployment. (fixed 29c28f1)
- reports1/Dependencies: no third-party network_events -> empty. Add network_events (fonts.googleapis.com, js.stripe.com w/ some 5xx on failing runs) or VendorHealthDaily rows. (fixed 29c28f1)
- reports1/Journey coverage: no JourneyImport. Add one with / and /login + untested paths + a Sign-up journey. (fixed 29c28f1)
- reports1/Flake trends: no step_evidence -> step type "unknown". (fixed 29c28f1)
- reports1: some demo runs are timestamped later today than now (future). (fixed 45d03f3)

## Product bugs
- reporting.open_findings() counts superseded rows (Portfolio open findings > Security register). (fixed 02d6543)
- vendor_health.summarise(): lift None when no passed result saw errors -> UI says "not enough data" when signal strongest. (fixed 387304f)
- journey import misses GA4 headers ("Page path and screen class", "Screen page views"). (fixed 25d5897)
- flaky threshold 0.33 (flake trends) vs 0.4 elsewhere (documented, not a bug per se).
- DeliveryReport "1 failed deploys" plural. (fixed 9d3ef90)
- DeliveryReport HowToSend says project-scoped key; workspace keys also accepted. (fixed 9d3ef90)

## From releases
Demo gaps: gate policy on web project (max_flaky_cases 0, require_critical_passed, max_evidence_age_days 7) so Blocking shows; an ExternalTestReport; an active quarantine; manual suite + 2 manual runs + ReleaseSignoff approve 2.4.0 + older reject; requirement descriptions w/ criteria (Gherkin + one vague) + linked CaseProposal + failing/uncovered requirement; traceability link to never-run/failing case; ~3 incidents (recurrence pair, escaped defect w/ regression case, matched case). (fixed 29c28f1)
Bugs:
- QualityDashboard.tsx CHECKS lacks coverage/flaky_cases/critical_passed/evidence_age/manual_signoff/a11y/api-breaking (raw names). (fixed 2938a73)
- QualityDashboard security_high_findings text says "open ... across recent scans" — code counts any status attached to judged runs, never ZAP. (fixed 2938a73)
- Gate rules form lacks require_no_breaking_api_changes/api_baseline. (fixed 2938a73)
- ReleaseReadiness/Requirements/Incidents not redesigned; readiness intro wraps narrow at 1440; readiness shows raw check names. (intro width fixed 8bfe753, check names fixed 2938a73; redesign still open)
- Automated case editor has no priority field (critical rules unusable for automated tests from UI). (fixed 6afa790)
- Traceability matches by name, readiness/requirements by id -> differing statuses.
- No UI to remove a requirement link (traceApi.removeLink unused). (fixed 550d409)

## From today/runs/watch
Demo gaps: pending CaseProposals (update w/ rationale+agent_id+confidence, create, delete) + pending SelectorHealProposal w/ candidates; visual baselines+comparisons (needs PNGs in object store); one failed run w/ step_evidence + ai_analysis v2; monitor checks dated in the future (build_demo_plan makes all of today's hours); flaky sample_count 60 vs real cap 20. (proposals, heal proposal, step_evidence + ai_analysis v2 fixed 29c28f1; future checks fixed 45d03f3; visual baselines and flaky sample_count still open)
Bugs:
- No UI to create a monitor (ScheduleModal lacks is_monitor/alert fields) but Monitors "New monitor" links to /schedules. ** user-visible gap (fixed ffd2392)
- Proposals policy card squashed at 1440/overlaps at 1280 (Proposals.tsx:78-79). (fixed 98e358d)
- FlakyTests subtitle counts stable tests as flaky (FlakyTests.tsx:178). (fixed 9d3ef90)
- TestMatrix delete-all dialog says "across the system"; actually editor projects only. (fixed 8098dfb)
- Proposals empty-state "No waiting for review test proposals"; update_suite_settings titled "Case #null". (fixed 98e358d)
- VERIFY: accepting DELETE proposal uses session.delete not cascade_delete (agent_ownership.py:1112) -> likely 500 on cases with history. (fixed 0c2d4b7)
- Pinned baselines tab shows Accept/Keep/Delete to viewers; monitor status-page buttons to everyone (backend refuses). (fixed ffd2392)
- Runs search is case-sensitive (LIKE) — consider ILIKE. (fixed d7403ea)

## From setup2
Demo gaps: webhook + deliveries; disabled jira tracker config; ~30d LLMUsageEvent (2 providers, 4 features, some failures) + UsageRecord; a few PolicyDecisionLog denials; AgentSession(s) for claude-code w/ runs/proposals; plans are install-level (not demo). (fixed 29c28f1; plans stay install-level)
SECURITY-ish bugs (priority):
- billing.assign_plan: any ws admin can switch to any (paid) plan even with Stripe; unaudited; checks deprecated UserWorkspace.role. (fixed 091acfe, eb1e39c)
- issue_trackers: base_url posted with no net_guard (SSRF) at save and send. (fixed 091acfe)
- POST /runs/{id}/tickets: no tracker-workspace == run-workspace check (same hole as triage, fixed there in 812790e). (fixed 091acfe)
- tracker configs any-member writable, not audited (webhooks were tightened to admin). (fixed 091acfe)
Other:
- Billing "AI generations today" always 0 (metric never recorded; reads monthly). (fixed 091acfe)
- Policies whole-instance scope error message for non-instance-admin. (fixed 6d0582f, 8098dfb)
- LLMUsage FEATURE_LABELS missing assistant/requirement_generation/incident_regression. (fixed 4a7ae19)
- Agents list includes schedule:<id> and demo-seed. (fixed 15fc633)
- Billing shows empty "Plans" heading with no plans. (fixed 4a7ae19)

## From reports2
Demo gaps: BusinessJourney "Sign in and manage todos"; flag catalogue checkout_v2 + ~10 runs w/ flags; BenchmarkGroup w/ 2 targets; LLMUsageEvent; HypothesisFeedback >=5; a targeted run + full baseline; accepted/rejected/auto-applied proposals. (fixed 29c28f1)
Bugs: CostTrend empty chart "peak 1 tokens/day"; unpriced chart hides browser-minutes; Costs note shows "TestCaseResult.duration_ms"; Value report shows "(WP16c)", "POST /runs with case_ids", "selection_total"; Flag coverage subtitle quotes API body; Costs/PoC/Value default to workspaces[0] not sidebar project's workspace. (fixed 8098dfb, c160d2b)

## From setup1
Demo gaps: 2-3 fake members, a team w/ project grant, a pending invitation; revoked+expired demo keys; OpenAPI spec (2 versions) on Public API. (fixed 29c28f1)
Bugs (priority first):
- NO ONE CAN DELETE A WORKSPACE: workspaces.py:250 checks workspace:delete_workspace, roles grant workspace:delete. (fixed 38600c5)
- remove_user_from_workspace leaves UserProjectAccess rows (removed people keep project access); no audit. (fixed 38600c5)
- Project-scoped API keys not enforced on test_cases/test_suites endpoints (read other projects' tests unless RLS enforce). (fixed 1f6805a)
- add_user_to_team_by_email adds users outside the workspace. (fixed 38600c5)
- Invitation token only in POST response; no email; "Copy invitation link" never appears. (link + Copy shown on create, fixed 6e5a811; still no email is sent)
- ApiKey role field stored, never read. (fixed 1f6805a)
- My settings Theme/Timezone/Date format have no effect; MFA state not shown; Disable MFA enabled with no code. (fixed 208ad73, 66cab98, 11ff5a3)
- WorkspacePage role pill compares 'admin' vs "Workspace Admin"; status hardcoded "active"; Users "All workspaces" shows "Workspace -"; Tenant Admin pill. (fixed 6e5a811, 66cab98)
- Tenant admin Assign never changes an existing role; no UI to change a workspace role. (fixed 38600c5, 6e5a811)
- TraceCorrelationSection points to old "Settings → Instance (Admin)" location. (fixed 8098dfb)
- ProjectSettings notice says all changes need admin; OpenAPI needs editor. (fixed 8098dfb)
- Sizing calculator: no input for artifact_sizes_kb. (fixed 308f0d2)
- OpenApiSpecSection label overlaps Choose file. (fixed 8bfe753)

## From security
Demo gaps: 2+ zap scans w/ mixed-status findings; AccessMatrix + run w/ pass/granted/inconclusive (needs security enabled + allowlist); members/keys + 2 review campaigns; estate w/ domains/results; monitored page w/ scripts; SOC2 controls + evidence + exception; an evidence pack; trust center (disabled) w/ status page/doc/draft post. (fixed 29c28f1)
Bugs:
- access_matrix.apply_findings bypasses finding_lifecycle (old statuses resolved/acknowledged; no SLA; no reopen); MatrixFindings STATUS_TONE old values. (fixed 5b11a8a, 4a7ae19)
- login audit row has no workspace_id -> never in workspace Audit log, yet UI suggests "login". (UI no longer suggests it, 4a7ae19; the login audit row still has no workspace_id)
- Access reviews "Whole instance" refusal shows instance-settings message (shared get_current_instance_admin detail) — same for Policies. (fixed 6d0582f, 8098dfb)
- _queue_retest wording wrong for matrix/estate findings. (fixed 5b11a8a)
- AccessReviews/TrustCenter default to first workspace. (fixed 8098dfb)
- Estate "Remove host" no confirmation. (fixed 308f0d2)
- AccessMatrix/Estate/ScriptInventory not redesigned (own project select).

## From tests
Demo gaps: owners/backup owners/last_reviewed_at on some cases; an active nightly schedule in the web project; MobileAppBuild rows (+ objects); SiteCrawl + run + issues; ~30d PageBudgetSample; failed results with step_evidence (selector health); AccessibilityCheck/Violation rows + a waiver; a nested suite + suite audit rows; a cluster resolved in-window (MTTR); an exclusive environment + HAR. (fixed 29c28f1, except MobileAppBuild rows — still open)
Bugs:
- Continuous mode UI text says "same browser session"; worker sends one job per test. (fixed 8098dfb)
- Schedules header "Times are in UTC" but times shown in viewer's zone. (fixed ffd2392)
- Resume (is_active=true) doesn't recompute next_run_at; NULL next_run_at never runs. (fixed 3b4ebc8)
- VERIFY: deleting a monitor with checks likely FK-fails (monitorcheck.schedule_id, plain session.delete). (fixed 0c2d4b7)
- Schedule buttons shown to editors (POST /schedules needs project admin); Pause/Edit/Delete not role-gated. (fixed ffd2392)
- SuiteDetails list view always opens /cases/:id/edit (manual cases need the manual editor). (fixed ffd2392)
- Sub-module Run button not gated. (fixed ffd2392)
- Analytics Flakiest empty text says "in this window" (not windowed). (fixed 8098dfb)
- Sidebar "Suites" not highlighted on /suites/:id; FrontendQuality not redesigned. (sidebar fixed 8bfe753; FrontendQuality redesign still open)
- Demo monitor checks dated in the future (all four hours of today). (fixed 45d03f3)

## 2026-10-22 (guide recapture, branch docs/guide-recapture)
All guide pictures retaken (pictures only; the walkthrough MP4s were NOT re-recorded — no ffmpeg — so the videos of pages that used to be empty still show the old empty states, and the edited record() blocks have not been run).

Still open from above: flaky threshold 0.33 vs 0.4 (documented); Traceability matches by name, readiness/requirements by id; ReleaseReadiness/Requirements/Incidents, AccessMatrix/Estate/ScriptInventory and FrontendQuality not redesigned; login audit row has no workspace_id; invitations still send no email; demo has no visual baselines/comparisons and no MobileAppBuild rows (Visual review, App builds stay empty); the "Case #N" title of an update proposal that doesn't rename the test.

New, seen in the screenshots:
- **Suites page can't be captured (tests-suites.spec.ts fails).** The seed put sub-module "Todo editing (demo)" under "Web smoke (demo)", and SuiteDetails.tsx (~468) only lists a suite's own tests when it has no sub-modules, so Web smoke's own tests (incl. "Add a todo") can't be reached in the UI ("9 tests · 1 module", Tests tab says 1). Fix the seed or show both lists. The guide's Suites pictures are left at the old capture.
- "Permission Denied" toast on load for non-instance-admins on Billing, Access reviews, Policies: useIsInstanceAdmin probes GET /admin/instance-admins and the 403 hits the global toast in lib/api.ts (specs now wait it out).
- Executive summary readiness reasons show raw check ids (external_tests:, flaky_cases:, critical_passed:) — 2938a73 missed this page. Costs "By feature" shows raw keys assistant / requirement_generation — 4a7ae19 only fixed AI usage.
- Per-day bar charts (Costs trend, Executive summary, AI usage "Tokens per day") leave the right 25–40% empty while the end date sits at the far right; AI usage chart has a lone "0" label. Page budgets trend bars (BudgetsPanel MetricBars) shrink to nothing. Dependencies "Errors per day" line is a fixed 480 px.
- Release readiness critical-cases table: "Last result" older than "Last passed" (Homepage loads), Commit "–". Quality gate vs readiness disagree on External CI and Critical tests passed for the same newest run/commit. With the seeded gate policy no demo commit can ever be "Ready to release".
- Requirements → Import says "No Jira or Linear connection yet" when a disabled one exists.
- Value report "CI time saved" reads "0 min" (fmtMinutes rounds sub-minute savings down). Manual runs reported as browser "manual" (Flake trends, PoC report); "default (no environment)" label wraps leaving a lone ")".
- Findings register: an inline-script finding whose title is a 64-char hash doesn't wrap and pushes Due/Status off the right at 1440 px. Recent denials table squeezed in its half-width card (Policies).
- Redeliver on a demo webhook really tries to send (outbound_webhook_tasks._send_follow_up has no demo check).
- Demo data: claude-code session last seen 10/18 but its proposals are 10/22, list totals ≠ session totals (Agents); seeded audit rows carry the seeding time, so sign_off/verified_fixed/approve events dated weeks earlier appear as today; control History "No history."; access-matrix swap row has an empty "Placeholder in URL" (Save would likely be refused); Benchmarks warns 2 tests open a full address yet seeded times differ per site.
- Layout nits: Users/Tenant admin long demo emails run under the role/Pending label; Sizing calculator formula table clipped on the right at 1440; Quality gate "Evidence age" value collides with its label; crawl issue "Not checked (private address)" vs detail "external host not checked"; trust-center public link shows the capture network host http://frontend/.
