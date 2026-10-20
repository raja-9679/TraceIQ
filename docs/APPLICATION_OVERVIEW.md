# TraceIQ — Application Overview

A plain-language description of what TraceIQ is, what it can do, and what
every item in the sidebar is for.

> **Which sidebar?** The full product UI lives on the `feature/redesign-a`
> branch, where the sidebar is grouped into seven "places" (Today, Releases,
> Reports, Tests, Runs, Watch, Security) plus a **Setup** menu. That is the
> layout described below. The functional branch (`feature/enterprise-auth-ai`)
> still shows the older flat list of 35 links; the mapping is at the end of
> this document. Pages added in WP12–WP14 (reports, compliance, policies,
> agents, trust center, test packs, sizing…) only have screens on the
> redesign branch, though their APIs exist on both.

---

## 1. What TraceIQ is

TraceIQ is a **UI and API testing and automation platform** (SaaS or
self-hosted). Teams:

1. **Write tests** — record or build browser journeys step by step, import
   existing Playwright `.spec.ts` files, write manual test scripts, or let an
   AI agent propose tests.
2. **Organise them** into projects and nested suites, with settings (headers,
   auth, base URLs, secrets) inherited from parent to child.
3. **Run them** on a fleet of Playwright workers (Chromium, Firefox, WebKit,
   emulated devices, real Android/iOS apps through Appium), on demand, on a
   schedule, from CI, or as uptime monitors.
4. **Understand failures** — every run keeps step-by-step evidence
   (screenshots, console, network, DOM excerpts, video/traces if allowed), and
   AI failure analysis groups failures and suggests causes and selector
   repairs.
5. **Decide whether to ship** — quality gates, release readiness, sign-offs,
   requirements traceability, and reports for managers and auditors.

It is **multi-tenant**: Tenant → Workspace → Project → Suite → Test case, with
role-based access (viewer / editor / admin) at workspace, team and project
level. It is built to be usable in **regulated environments**: data-capture
policies and redaction, an append-only hash-chained audit log, SSO/SAML/LDAP/
SCIM, MFA, encryption with key rotation, data retention and erasure, and
compliance evidence packs.

### Building blocks

| Part | What it does |
|---|---|
| **Frontend** | React web app (the UI described below). |
| **Backend** | FastAPI service: the API, permissions, results, reports. |
| **Execution workers** | Node.js + Playwright processes that actually drive browsers and call APIs. Scale horizontally; can be pinned to regions. |
| **Mobile worker** | Runs native app tests through Appium against an emulator or device cloud. |
| **Local worker** | A worker a developer runs on their laptop so tests can hit `localhost` before deploying. |
| **Celery workers + beat** | Background jobs: aggregating results, notifications, schedules, scans, reports, retention clean-up. |
| **PostgreSQL / Redis / object store (RustFS)** | Data, job queues, and artifacts (screenshots, videos, traces). |
| **Integrations** | MCP server for AI coding agents, GitHub Action, CLI (`traceiq-cli`), outbound webhooks, Jira/Linear/GitHub/iTop trackers, Slack/Teams/email, PagerDuty/Opsgenie, SIEM. |

---

## 2. Core capabilities at a glance

**Test authoring**
- Visual test builder with ~70 step types: navigation (`goto`, tabs, frames),
  interaction (`click`, `fill`, `select-option`, `drag-and-drop`, `upload-file`,
  `hover`, `press-key`…), assertions (`expect-text`, `expect-visible`,
  `expect-url`, `expect-title`…), API steps (`http-request`, `graphql`,
  `json-path`, `wait-for-response`, `mock-response`, `block-request`),
  accessibility (`check-accessibility`), visual regression
  (`expect-visual-match`, full page or a single element), performance
  (`check-page-budget`, `load-test`), security (`expect-header`,
  `expect-cookie-flags`, `expect-session-dead`, `check-tls`), email/webhook
  waits (`wait-for-email`, `wait-for-webhook`), `totp` for 2FA logins, feed
  and AMP checks, and `mobile-*` steps for native apps.
- Templates: `{{env.X}}`, `{{secret.X}}` (encrypted project secrets),
  `{{fake.*}}` test data, `{{unique.NAME}}`, `{{run.id}}`, `{{flag.NAME}}`,
  `{{inbox.address}}`, `{{webhook.url}}`.
- Data-driven runs (datasets), suite setup/teardown hooks, automatic clean-up
  of records a test created.
- Personas: stored logged-in identities so tests start already signed in, and
  so the same test can run as several users.
- Manual test cases (written instructions a person follows) with recorded
  outcomes and screenshots.
- Import Playwright specs, OpenAPI specs (generate API tests), Storybook
  stories (component visual tests), and ready-made test packs.

**Execution**
- Modes: *separate* (one job per case), *parallel*, *continuous* (one job per
  suite).
- Matrix runs: browsers × devices × viewports × locales × colour schemes ×
  personas.
- Targeted runs (only the cases a code change affects), retries, quarantine of
  flaky tests, regional workers, egress allowlists.

**Analysis**
- Per-step evidence, Playwright trace viewer, AI failure investigation with
  cited evidence, failure clustering, flaky-test scoring, self-healing
  selectors with ranked candidates, visual diffs grouped by similar change,
  "Ask TraceIQ" assistant (read-only chat over your runs and findings).

**Governance**
- Quality gates and release readiness verdicts, manual release sign-off,
  requirement and incident traceability, change proposals with separation of
  duties, policy engine, AI opt-out per workspace, AI-agent budgets and kill
  switch, compliance controls, evidence packs, audit log, access reviews.

---

## 3. The sidebar, item by item

At the top of the sidebar is the **project picker**: almost every page shows
data for the selected project. The **"Ask TraceIQ"** button in the header opens
the AI assistant drawer.

### 3.1 Today — *"What needs my attention right now?"*

| Item | What it does |
|---|---|
| **Overview** | The home dashboard for the selected project: run counts by status (passed, failed, error, running, queued), pass rate over the last 14 days, and the latest runs. A separate **Inbox** view (`/inbox`) lists actionable items — new failures, proposals to review, flaky tests, findings, monitors down — each with a one-click action (open run, mark investigating, apply repair, dismiss). |
| **Failure triage** | Failures grouped into clusters by signature (selector, timeout, assertion, network, navigation…). Shows how many times each group failed and lets you mark a group open / investigating / resolved / ignored, so ten broken tests with the same cause are handled once. |
| **Proposals** | The review queue for changes that AI agents (or Git sync) want to make to your tests — new cases, edits, moves, deletions, suite-setting changes — plus selector-heal proposals. Nothing touches a suite until a human accepts. Separation of duties can require that the proposer is not the approver. |
| **Flaky tests** | Tests whose results flip between pass and fail. Shows a flakiness score, lets you quarantine a test (with a reason, owner and expiry date — max 90 days), release it, or assign an owner. Expired quarantines run again rather than silently staying hidden. |
| **Visual review** | Screenshots that differ from their approved baseline, with the diff highlighted. Similar changes across many tests are grouped so you can accept an intended redesign in one click, or keep the baseline to flag a regression. Comment threads on each change. |

### 3.2 Releases — *"Can we ship this?"*

| Item | What it does |
|---|---|
| **Quality gate** | Define the rules a build must meet (pass rate, no critical failures, max flaky cases, evidence age, manual sign-off…) and see the project's current health and CI reporting. CI and the GitHub Action ask this gate for a pass/fail. Every verdict names the rule version that judged it, and a rule history lists every version, so an old decision can be re-checked. |
| **Release readiness** | For a given commit or branch: a verdict of *ready*, *not ready* or *insufficient data*, with the reasons — which gate checks failed, critical cases, active quarantines, linked requirements, and recent commits. |
| **Release sign-off** | A human approves or rejects a release, choosing the runs (automated and manual) it was tested with. Can require that the signer did not run the tests themselves. |
| **Requirements** | Import requirements/user stories from Jira or Linear, see acceptance criteria parsed out (with ambiguity warnings), generate proposed test cases from them, and see coverage: covered, failing, untested, not covered. |
| **Traceability** | Link test cases to requirements or tickets, and list tests that are not traced to anything. |
| **Incidents** | Production incidents received by webhook from PagerDuty, Opsgenie or any signed source. Detects recurring incidents, can propose a regression test for an incident, and tracks "escaped defects" — whether a test existed and why it didn't catch the problem. |

### 3.3 Reports — *"How are we doing?"* (for leads, PMs, auditors)

| Item | What it does |
|---|---|
| **Executive summary** | Quality at a glance for non-testers: pass-rate trend, release readiness and what to watch. Printable, and shareable through an expiring public link that shows only safe summary fields. |
| **Portfolio** | Every project you can access in the workspace, side by side. |
| **Delivery (DORA)** | Deployment frequency, lead time for changes, change failure rate and time to restore, built from deployments your CI reports to TraceIQ. |
| **SLOs** | Availability and latency of each monitor against its target, error budget remaining, and burn rate. |
| **Flake trends** | How flakiness changes over time and where it concentrates — browser, time of day, environment, failing step type. |
| **Dependencies** | Health of third-party services your app calls during tests (CDNs, analytics, payment providers…): error rates, latency, and how often their errors coincide with test failures. |
| **Journey coverage** | Import a page-view export from any analytics tool; see what share of real traffic your tests actually visit, the highest-traffic pages with no test, and tests on pages nobody uses. |
| **Business journeys** | Group test cases into the journeys the business cares about (sign-up, checkout…) and see each journey's health, trend, linked requirements and traffic. |
| **Flag coverage** | Which feature-flag values your runs exercised, and which values were never tested. |
| **Benchmarks** | Run one suite against several sites or deployments and compare pass rates, durations and page metrics side by side. |
| **Costs** | AI tokens and browser minutes converted to money with your instance's price table, per project, with monthly budgets and 80% / 100% alerts. |
| **PoC report** | A proof-of-concept report for a date range: tests run, results, and an *estimated* time/money saved versus manual testing. Shareable via a frozen public link. |

### 3.4 Tests — *"What do we test, and how?"*

| Item | What it does |
|---|---|
| **Suites** | The test library. Suites nest into modules and sub-modules; each holds test cases and settings (headers, query params, auth, retries, execution mode, setup/teardown steps) that children inherit. From a suite you open the **test builder** to create or edit cases, write **manual tests**, run the suite (choosing browsers, devices, screen sizes, locales, personas, data seed), export/import it, and view its audit log. A "Sample Project" can be seeded with three runnable demo tests. |
| **Schedules** | Run suites automatically on a cron schedule (with time zones, browsers, devices and regions), shown as cards or a list; pause or delete them. |
| **Environments** | Deployment targets for a project (dev, staging, prod…) with base URLs, variables and encrypted secrets, so the same tests can run anywhere. |
| **App builds** | Upload native mobile binaries (APK, AAB, IPA). Mobile test runs are pinned to a chosen build. |
| **Frontend quality** | Site crawler (broken links, errors), page budgets (Core Web Vitals, page weight, SEO facts), selector health (which selectors break or get healed most), and Storybook import for component visual tests. |
| **Import Playwright** | Bring an existing `.spec.ts` in as a test case; it runs as written through the Playwright runner. |
| **Test packs** | Ready-made suites for common integrations (SSO, SCIM provisioning, outbound webhooks). Import one, fill its variables and secrets, then run it against your own system. |
| **Analytics** | Per-test effectiveness: which tests fail most, which are slow, which are flaky, and how fast failures get fixed. Also hosts the scheduled email/chat **digest** settings. |

### 3.5 Runs — *"What happened?"*

| Item | What it does |
|---|---|
| **Runs** | Every execution, newest first, with status, trigger (manual, schedule, CI, agent), browser and git details; filter by Passed / Failed / Active / Cancelled / Timed out; select several to clean up. A run ends Passed, Failed or Error (verdicts), or **Cancelled** / **Timed out** (over, but never counted as a pass or a failure anywhere). Opening a run shows **Cancel run** while it is queued or running, step-by-step results (each non-passing test says why: test failure, setup, infrastructure, time limit or not executed), screenshots, console and network logs, the Playwright trace timeline, video (if the capture level allows it), the AI failure analysis (re-runnable with a chosen AI provider), screen-size/locale **sweep** results, a comparison against another run, a downloadable **repro bundle** (zip with a standalone Playwright spec, secrets replaced by placeholders), one-click ticket filing, "Re-run with this seed" and a **Share** link. |

### 3.6 Watch — *"Is production up?"*

| Item | What it does |
|---|---|
| **Monitors** | Tests run on a short interval as synthetic uptime checks, optionally from several regions. Shows healthy / needs attention / paused / no checks yet, sends down and recovery alerts, and can publish a **public status page**. |
| **On-call** | Where monitor alerts escalate: paging channels (PagerDuty, Opsgenie), maintenance windows that suppress pages, the log of recent paging decisions, and the worker egress allowlist. |

### 3.7 Security — *"Is it safe and compliant?"*

| Item | What it does |
|---|---|
| **Scans & findings** | Start security scans (OWASP ZAP and passive checks over test traffic, plus leak detection of secrets/keys in responses) and work the **findings register**: open → in progress → fixed (awaiting retest) → verified fixed, or accepted risk / false positive, with owners, SLA due dates and SARIF export. |
| **Access matrix** | Define who should be able to reach what: rows are test cases, API requests or pages; columns are personas (users with different roles or tenants) plus anonymous; each cell is allow or deny. Running it proves access control and tenant isolation, and any "deny" that was actually allowed becomes a high-severity finding. |
| **Access reviews** | Periodic user-access reviews: a snapshot of who has which access, each item kept or revoked by a reviewer, signed off, then revocations applied. Exports as CSV/HTML for auditors. |
| **Estate** | Passive posture checks over your domains: DNS (SPF/DMARC etc.), TLS certificates and expiry, security headers, certificate-transparency discovery of sub-domains. Each domain gets a score with an itemised breakdown; cert expiry alerts at 30/14/7 days. |
| **Script inventory** | For sensitive pages (e.g. payment pages, PCI DSS 4.0 6.4.3 / 11.6.1): records every script the page loads with its hash and security headers, alerts when a script changes, and requires a written justification to approve each script. |
| **Controls** | Compliance controls (a SOC 2 starter set can be loaded). Each control's status — passing, failing, stale, not assessed, excepted — is computed live from TraceIQ's own records (access reviews, audit chain, data policies, findings, restore drills, MFA, SSO, key expiry) plus manual evidence. Time-boxed exceptions need a second approver. |
| **Evidence packs** | One signed zip for an auditor: audit log and chain check, access reviews, controls and exceptions, data policies, security findings (SARIF) and restore drills. A verifier checks that nothing in a pack was altered. |
| **Audit log** | Who did what in the workspace, newest first. Rows are append-only and hash-chained; **Verify chain** proves no row was changed. Exportable. |
| **Trust center** | An optional public page for customers and prospects: uptime from chosen status pages, incident posts, an opt-in aggregate security posture, and documents shared through per-recipient expiring links. Off by default. |

### 3.8 Setup menu — account, project, workspace and admin

| Item | What it does |
|---|---|
| **My settings** | Your own preferences: profile, display (theme, time format), two-factor sign-in (authenticator app, recovery codes), email notification defaults and per-project overrides, default browsers/devices for runs you start, recording preferences, your API keys and how to connect an IDE through MCP. |
| **Project settings** | Settings shared by the whole project: data capture level and retention (none / minimal / standard / full; what gets redacted or masked), notification recipients and chat webhook, members and policies, and the project's OpenAPI spec. |
| **Workspace** | Manage the workspace: members and their roles, teams, projects, pending invites, and the danger zone (delete workspace with a dry run). |
| **Users** | Members across the workspaces you manage: role, status, last active, invites. |
| **Tenant admin** | *(Tenant admins only.)* All users in your tenant and which workspaces each can access; activate or deactivate accounts. |
| **Worker fleet** | *(Instance admins.)* Live execution workers, their heartbeats, job streams and queue lag, dead-letter queue (replay/discard failed jobs), and version skew between workers and the backend. |
| **Sizing calculator** | Estimate how many workers, how much control-plane capacity and storage a deployment needs; every number shows its formula and assumption, with ready-to-use Docker Compose and Helm values. |
| **Instance settings** | *(Instance admins.)* Server-wide configuration for every workspace: email/SMTP, AI providers, SSO (OIDC, SAML, LDAP), federated provisioning, SCIM, MFA requirement, object storage, network policy, retention, SIEM streaming and security alerts, worker config and regions, costs, test inbox, and more. Stored in the database and editable at runtime. |
| **API keys** | Workspace keys for CI pipelines, AI agents and the MCP server; can be scoped to one project, restricted to IP ranges, and given an expiry. The full key is shown once. |
| **Webhooks** | *(Workspace admins.)* Outbound webhooks: TraceIQ POSTs an HMAC-signed JSON payload to your URL on run events (passed, failed, cancelled, timed out). Each webhook has a delivery log — every attempt, its HTTP code and error, automatic retries after 1, 5 and 30 minutes — and a Redeliver button. |
| **Issue trackers** | Connect Jira, iTop or GitHub (and Linear for requirement import) so a failing run can be filed as a ticket with its trace, video and screenshots in one click. |
| **AI usage** | Every AI call TraceIQ makes (failure analysis, selector healing, test generation, assistant) broken down by provider, model, feature and project, with token counts and latency. |
| **Policies** | One place for every check that can refuse an action — plan quotas, capture and network limits, AI use, agent budgets, proposal review, scan authorization — plus the log of refused actions (kept 90 days) and the workspace **AI on/off** switch. |
| **Agents** | AI agents that call TraceIQ (identified by `X-Agent-Id`): each session's activity and usage against its budget (runs, tokens, proposals, minutes), and a kill switch to block an agent. |
| **Billing** | Your workspace plan, metered usage for the month, and available tiers. Quotas are enforced when runs are created. |

### 3.9 Public pages (no login)

| Page | What it does |
|---|---|
| `/status/:slug` | Public status page built from monitors. |
| `/share/:token` | A shared run report, project/workspace summary or PoC report (safe fields only; links expire and can be revoked). |
| `/trust/:slug` | The public trust center. |
| `/login`, `/signup`, password reset, email verification | Account access, including SSO buttons and MFA. |

---

## 4. Integrations outside the UI

| Integration | What it does |
|---|---|
| **REST API** | Everything the UI does, authenticated by JWT or `X-API-Key`. Start runs with git context (`git_commit`, `git_branch`, PR URL), report deployments, read gates. |
| **MCP server** (`integrations/mcp-server/`) | 52 typed tools so AI coding agents (Claude Code, IDEs) can list suites, run tests, read failures, run impact analysis and propose test changes. |
| **GitHub Action** (`integrations/github-action/`) | Gates pull requests on TraceIQ results; can run only the affected tests and posts a sticky PR comment with failing cases. |
| **CLI** (`integrations/cli/`) | `traceiq login / run --wait / status / replay / pull / push` — run tests from a terminal or CI and sync test cases with a Git repo. |
| **Local worker** | `npm run worker:local` runs tests against a developer's `localhost`. |
| **Notifications** | Email, Slack, Teams, PagerDuty, Opsgenie, outbound webhooks, SIEM (syslog, Splunk HEC, Elastic, webhook). |
| **Identity** | OIDC SSO, SAML 2.0, LDAP, SCIM 2.0 provisioning, TOTP MFA. |

---

## 5. Older flat sidebar (functional branch) → new grouping

| Old flat item | Where it lives now |
|---|---|
| Dashboard | Today → Overview |
| Quality | Releases → Quality gate |
| Release Readiness | Releases → Release readiness |
| Test Suites | Tests → Suites |
| Test Runs | Runs |
| Monitors / On-call | Watch → Monitors / On-call |
| Security, Access Reviews, Access Matrix, Estate, Script Inventory | Security → same names |
| Triage | Today → Failure triage |
| Analytics | Tests → Analytics |
| AI Usage | Setup → AI usage |
| Requirements, Traceability, Incidents | Releases → same names |
| Import Playwright | Tests → Import Playwright |
| Schedules | Tests → Schedules |
| Proposals, Visual Review, Flaky Tests | Today → same names |
| Frontend Quality, Environments, App Builds | Tests → same names |
| API Keys, Webhooks, Issue Trackers, Users, Tenant Admin, Worker Fleet, Workspace, Billing, Settings | Setup menu |

Pages that exist only in the new sidebar: Release sign-off, all of Reports,
Test packs, Controls, Evidence packs, Audit log, Trust center, Project
settings, Instance settings (previously a tab inside Settings), Sizing
calculator, Policies and Agents.
