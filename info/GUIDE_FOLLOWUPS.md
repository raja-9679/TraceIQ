# User-guide follow-ups (2026-10-21)

Found by the writers of the /docs/ user guide while checking every page against its code. Nothing here is fixed yet unless marked. Pages with a demo gap show their empty state, described from code; adding the data to backend/app/services/demo_workspace.py (enterprise branch) and re-running e2e/docs-capture/run.sh pages/<spec> would give them real-looking pictures.


## Demo gaps (demo_workspace.py)
- reports1/Delivery: deployments lack git_commit/commit_at (lead time —); no incidents (MTTR —). Add commits matching demo runs + 2-3 incidents within 24h of a deployment.
- reports1/Dependencies: no third-party network_events -> empty. Add network_events (fonts.googleapis.com, js.stripe.com w/ some 5xx on failing runs) or VendorHealthDaily rows.
- reports1/Journey coverage: no JourneyImport. Add one with / and /login + untested paths + a Sign-up journey.
- reports1/Flake trends: no step_evidence -> step type "unknown".
- reports1: some demo runs are timestamped later today than now (future).

## Product bugs
- reporting.open_findings() counts superseded rows (Portfolio open findings > Security register).
- vendor_health.summarise(): lift None when no passed result saw errors -> UI says "not enough data" when signal strongest.
- journey import misses GA4 headers ("Page path and screen class", "Screen page views").
- flaky threshold 0.33 (flake trends) vs 0.4 elsewhere (documented, not a bug per se).
- DeliveryReport "1 failed deploys" plural.
- DeliveryReport HowToSend says project-scoped key; workspace keys also accepted.

## From releases
Demo gaps: gate policy on web project (max_flaky_cases 0, require_critical_passed, max_evidence_age_days 7) so Blocking shows; an ExternalTestReport; an active quarantine; manual suite + 2 manual runs + ReleaseSignoff approve 2.4.0 + older reject; requirement descriptions w/ criteria (Gherkin + one vague) + linked CaseProposal + failing/uncovered requirement; traceability link to never-run/failing case; ~3 incidents (recurrence pair, escaped defect w/ regression case, matched case).
Bugs:
- QualityDashboard.tsx CHECKS lacks coverage/flaky_cases/critical_passed/evidence_age/manual_signoff/a11y/api-breaking (raw names).
- QualityDashboard security_high_findings text says "open ... across recent scans" — code counts any status attached to judged runs, never ZAP.
- Gate rules form lacks require_no_breaking_api_changes/api_baseline.
- ReleaseReadiness/Requirements/Incidents not redesigned; readiness intro wraps narrow at 1440; readiness shows raw check names.
- Automated case editor has no priority field (critical rules unusable for automated tests from UI).
- Traceability matches by name, readiness/requirements by id -> differing statuses.
- No UI to remove a requirement link (traceApi.removeLink unused).

## From today/runs/watch
Demo gaps: pending CaseProposals (update w/ rationale+agent_id+confidence, create, delete) + pending SelectorHealProposal w/ candidates; visual baselines+comparisons (needs PNGs in object store); one failed run w/ step_evidence + ai_analysis v2; monitor checks dated in the future (build_demo_plan makes all of today's hours); flaky sample_count 60 vs real cap 20.
Bugs:
- No UI to create a monitor (ScheduleModal lacks is_monitor/alert fields) but Monitors "New monitor" links to /schedules. ** user-visible gap
- Proposals policy card squashed at 1440/overlaps at 1280 (Proposals.tsx:78-79).
- FlakyTests subtitle counts stable tests as flaky (FlakyTests.tsx:178).
- TestMatrix delete-all dialog says "across the system"; actually editor projects only.
- Proposals empty-state "No waiting for review test proposals"; update_suite_settings titled "Case #null".
- VERIFY: accepting DELETE proposal uses session.delete not cascade_delete (agent_ownership.py:1112) -> likely 500 on cases with history.
- Pinned baselines tab shows Accept/Keep/Delete to viewers; monitor status-page buttons to everyone (backend refuses).
- Runs search is case-sensitive (LIKE) — consider ILIKE.

## From setup2
Demo gaps: webhook + deliveries; disabled jira tracker config; ~30d LLMUsageEvent (2 providers, 4 features, some failures) + UsageRecord; a few PolicyDecisionLog denials; AgentSession(s) for claude-code w/ runs/proposals; plans are install-level (not demo).
SECURITY-ish bugs (priority):
- billing.assign_plan: any ws admin can switch to any (paid) plan even with Stripe; unaudited; checks deprecated UserWorkspace.role.
- issue_trackers: base_url posted with no net_guard (SSRF) at save and send.
- POST /runs/{id}/tickets: no tracker-workspace == run-workspace check (same hole as triage, fixed there in 812790e).
- tracker configs any-member writable, not audited (webhooks were tightened to admin).
Other:
- Billing "AI generations today" always 0 (metric never recorded; reads monthly).
- Policies whole-instance scope error message for non-instance-admin.
- LLMUsage FEATURE_LABELS missing assistant/requirement_generation/incident_regression.
- Agents list includes schedule:<id> and demo-seed.
- Billing shows empty "Plans" heading with no plans.

## From reports2
Demo gaps: BusinessJourney "Sign in and manage todos"; flag catalogue checkout_v2 + ~10 runs w/ flags; BenchmarkGroup w/ 2 targets; LLMUsageEvent; HypothesisFeedback >=5; a targeted run + full baseline; accepted/rejected/auto-applied proposals.
Bugs: CostTrend empty chart "peak 1 tokens/day"; unpriced chart hides browser-minutes; Costs note shows "TestCaseResult.duration_ms"; Value report shows "(WP16c)", "POST /runs with case_ids", "selection_total"; Flag coverage subtitle quotes API body; Costs/PoC/Value default to workspaces[0] not sidebar project's workspace.

## From setup1
Demo gaps: 2-3 fake members, a team w/ project grant, a pending invitation; revoked+expired demo keys; OpenAPI spec (2 versions) on Public API.
Bugs (priority first):
- NO ONE CAN DELETE A WORKSPACE: workspaces.py:250 checks workspace:delete_workspace, roles grant workspace:delete.
- remove_user_from_workspace leaves UserProjectAccess rows (removed people keep project access); no audit.
- Project-scoped API keys not enforced on test_cases/test_suites endpoints (read other projects' tests unless RLS enforce).
- add_user_to_team_by_email adds users outside the workspace.
- Invitation token only in POST response; no email; "Copy invitation link" never appears.
- ApiKey role field stored, never read.
- My settings Theme/Timezone/Date format have no effect; MFA state not shown; Disable MFA enabled with no code.
- WorkspacePage role pill compares 'admin' vs "Workspace Admin"; status hardcoded "active"; Users "All workspaces" shows "Workspace -"; Tenant Admin pill.
- Tenant admin Assign never changes an existing role; no UI to change a workspace role.
- TraceCorrelationSection points to old "Settings → Instance (Admin)" location.
- ProjectSettings notice says all changes need admin; OpenAPI needs editor.
- Sizing calculator: no input for artifact_sizes_kb.
- OpenApiSpecSection label overlaps Choose file.

## From security
Demo gaps: 2+ zap scans w/ mixed-status findings; AccessMatrix + run w/ pass/granted/inconclusive (needs security enabled + allowlist); members/keys + 2 review campaigns; estate w/ domains/results; monitored page w/ scripts; SOC2 controls + evidence + exception; an evidence pack; trust center (disabled) w/ status page/doc/draft post.
Bugs:
- access_matrix.apply_findings bypasses finding_lifecycle (old statuses resolved/acknowledged; no SLA; no reopen); MatrixFindings STATUS_TONE old values.
- login audit row has no workspace_id -> never in workspace Audit log, yet UI suggests "login".
- Access reviews "Whole instance" refusal shows instance-settings message (shared get_current_instance_admin detail) — same for Policies.
- _queue_retest wording wrong for matrix/estate findings.
- AccessReviews/TrustCenter default to first workspace.
- Estate "Remove host" no confirmation.
- AccessMatrix/Estate/ScriptInventory not redesigned (own project select).

## From tests
Demo gaps: owners/backup owners/last_reviewed_at on some cases; an active nightly schedule in the web project; MobileAppBuild rows (+ objects); SiteCrawl + run + issues; ~30d PageBudgetSample; failed results with step_evidence (selector health); AccessibilityCheck/Violation rows + a waiver; a nested suite + suite audit rows; a cluster resolved in-window (MTTR); an exclusive environment + HAR.
Bugs:
- Continuous mode UI text says "same browser session"; worker sends one job per test.
- Schedules header "Times are in UTC" but times shown in viewer's zone.
- Resume (is_active=true) doesn't recompute next_run_at; NULL next_run_at never runs.
- VERIFY: deleting a monitor with checks likely FK-fails (monitorcheck.schedule_id, plain session.delete).
- Schedule buttons shown to editors (POST /schedules needs project admin); Pause/Edit/Delete not role-gated.
- SuiteDetails list view always opens /cases/:id/edit (manual cases need the manual editor).
- Sub-module Run button not gated.
- Analytics Flakiest empty text says "in this window" (not windowed).
- Sidebar "Suites" not highlighted on /suites/:id; FrontendQuality not redesigned.
- Demo monitor checks dated in the future (all four hours of today).
