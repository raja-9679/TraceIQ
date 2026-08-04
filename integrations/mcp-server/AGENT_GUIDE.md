# TraceIQ — Agent Authoring Guide

> **Read this first, every session.** It explains how TraceIQ thinks about
> tests, what the MCP tools expect from you, the conventions that make tests
> robust, and the small set of pitfalls that have bitten every agent that
> skipped this doc. Examples are anchored to the Sarvajna v2 News Intelligence
> dashboard so they're concrete.

---

## 1. Mental model — who owns what

You (the agent) **own the test suite**:
- You read the application source (you have file tools).
- You decide what to cover and how to organize it.
- You author test cases via the MCP tools — `propose_create_case`, `bulk_propose_cases`, etc.

TraceIQ **catalogues + dispatches**:
- Stores your cases.
- Runs them in distributed Playwright workers.
- Surfaces results, traces, and structured failure data back to you.
- Never reads your source code — you do that locally and ship `code_paths` strings.

The **human** **reviews**:
- Every change you propose goes to the `CaseProposal` queue.
- Humans accept/reject via `POST /api/case-proposals/{id}/{accept,reject}`.
- API-key callers (i.e., you) **cannot accept your own proposals.** That's a hard rule.

This three-way split is the contract. Don't try to bypass it.

---

## 2. The standard session flows

Every tool returns **typed structured content** (validated against its
published `outputSchema`) — parse the structured result, not the text blob.

### Flow A — building coverage (first session on a project)

```
1. get_authoring_guide        ← this doc, read once
2. describe_step_types        ← step-type reference
3. discover_app_surface       ← what already exists in TraceIQ for this project
4. (read source code locally — Read/Glob/Grep)
5. (for each gap)
     bulk_propose_cases       ← submit many proposals in one call
6. (human reviews + accepts in the inbox)
7. run_suite + wait_for_run   ← validate the new coverage
8. (if a case fails)
     get_run + get_run_results ← read structured failure
     propose_update_case      ← fix the case, queue for review
```

### Flow B — you changed application code (every later session)

```
1. (derive the changed-file list — git diff --name-only)
2. select_tests_for_diff      ← which cases to RUN vs REVIEW (see §3.5)
3. (for suggested_action=review cases)
     get_case + get_run_history → propose_update_case if the case is stale
4. run_suite(git_commit=..., git_branch=...)   ← ALWAYS pass git context
     • testing a dev server on localhost? start the local worker
       (`npm run worker:local` in execution-engine/) and pass its id as
       local_worker_id — the run's jobs route to your machine
5. wait_for_run → get_run_report / evaluate_quality_gate
6. (failures) get_failure_analysis / analyze_run, list_failure_clusters,
     get_artifact_url (trace.zip)
7. (fix app code or propose case updates; re-run the matched subset)
8. (coverage moved?) set_code_paths / bulk_set_code_paths so the mapping
     stays true — this is what makes step 2 work next time
```

Passing `git_commit` in step 4 is not optional bookkeeping: when a case
passes on a run that carries a commit, TraceIQ stamps
`last_validated_commit` on the case — the durable record that "this test
was true of that code".

---

## 3. Code path discipline (Mode 1 is the WHOLE point)

If you have source-code access, **tag every case with `code_paths`**. This
single field powers impact analysis (`select_tests_for_diff`) — without it,
your tests never get picked up for a relevant PR.

### What `code_paths` accepts

Each entry is either a **bare path prefix** or an **fnmatch glob**:

| Pattern | Matches |
|---|---|
| `backend/app/api/articles.py` | Exactly that one file |
| `backend/app/api/` | Anything under the directory (bare prefix) |
| `backend/app/api/**/*.py` | Glob — every `.py` under any depth |
| `frontend/src/Articles/**` | Glob — every file under the Articles folder |

### How to choose them (Sarvajna example)

For a Sarvajna case that drives `GET /api/articles` on the dashboard:

```jsonc
"code_paths": [
  "sarvajna_v2/dashboard-service/app/api/articles.py",   // the handler
  "sarvajna_v2/dashboard-service/app/models/article.py"   // the data model it uses
]
```

For a UI smoke test that loads `/v2/articles`:

```jsonc
"code_paths": [
  "sarvajna_v2/dashboard-ui/src/Articles/**",
  "sarvajna_v2/dashboard-service/app/api/articles.py"     // any UI test exercises the API too
]
```

Rule of thumb: list every file the case actually touches, both **frontend
and backend**. A UI test of the articles page exercises the FE component AND
the API handler — both belong in `code_paths`.

### Backfilling existing cases

If you walk a codebase and need to retro-tag many existing cases at once,
use **`bulk_set_code_paths`** with a `{case_id: [paths]}` map. One call,
hundreds of updates.

### 3.5 Reading a `select_tests_for_diff` result (v2)

Each matched case tells you *why* it matched and *what to do*:

| Field | Meaning |
|---|---|
| `matched` | exact `(file, pattern)` pairs — which code_path caught which changed file |
| `suggested_action` | `run` — just re-run. `review` — the case likely needs EDITING first; see `reasons` (last result failed, quarantined flaky, or AI-authored & never reviewed). `run_then_review` — run it, but a bare prefix matched ≥5 changed files, so the mapping is probably too coarse — re-derive `code_paths` afterwards. |
| `last_result` | the case's most recent recorded result (status, run, commit) |
| `last_validated_commit` / `last_validated_at` | last commit at which the case PASSED a git-tagged run — if your edit predates it, the case has never seen this code |
| `flake` | flake score + quarantine state |

`unmatched_files` lists changed files **no case covers** — each one is a
candidate for a new proposal. `suggested_action` is a deterministic server
heuristic, not an oracle: you know the intent of your change, so override it
when you know better (e.g. you deliberately changed the behavior a passing
case asserts — that case needs `review` no matter what the server says).

---

## 4. Step authoring — the truth (with pitfalls)

Call `describe_step_types` for the full catalogue. The five gotchas that
caught me when authoring against Sarvajna are spelled out here so you
don't repeat them.

### Event-driven steps use an arm-before-trigger pattern

Steps run strictly sequentially, so anything that must *overlap* a
browser event carries its own trigger:

- `handle-dialog` — place it **before** the click that fires the
  alert/confirm/prompt; it arms a one-shot handler for the next dialog.
- `download-file` / `wait-for-response` / `switch-tab` — pass
  `params.trigger_selector`; the step arms its wait first, then clicks
  the trigger itself. A separate `click` step before the wait step will
  race and usually lose the event.
- `upload-file` — prefer `params.files: [{name, content_base64}]`;
  inline fixtures travel with the test case and need nothing
  pre-provisioned on the worker.

### Auth sessions — stop scripting login into every case

Mark exactly one case per project as the **auth setup case**
(`is_auth_setup: true` on the case). Its steps perform the real login.
When it passes, the worker captures the browser's storageState
(cookies + localStorage) and TraceIQ stores it for the project. Every
later run injects that state, so cases start **already logged in** —
do not begin cases with login steps.

- A case that must exercise the real login flow sets
  `use_auth_session: false` and starts from a clean browser.
- The stored session expires after `max_age_minutes` (default 720).
  Re-run the auth-setup case to refresh it; check freshness at
  `GET /api/projects/{id}/auth-session` (metadata only — the raw state
  is never exposed).
- If tests suddenly fail on auth-walled pages, the stored session
  likely expired mid-window: re-run the auth-setup case first, then
  re-run the failures.

### Environments and secrets — never hardcode URLs or credentials

Projects define named environments (`GET/POST
/api/projects/{id}/environments`): a `base_url` plus non-sensitive
`variables`. Sensitive values go into write-only secrets (`PUT
/api/projects/{id}/secrets` with `{key, value}`; reads return key names
only).

In steps:

- `{"type": "goto", "value": "/dashboard"}` — relative URLs resolve
  against the environment's `base_url`, so one suite runs on dev,
  staging, and prod unchanged.
- `{{env.KEY}}` interpolates an environment variable,
  `{{secret.KEY}}` a secret — in step values, headers, params, bodies.
- `run_suite` accepts `environment_id`; omitted, the project's default
  environment applies.

Author credentials as `{{secret.ADMIN_PASSWORD}}`, never as literals —
step JSON is stored, diffed, and shown in proposals.

### Mode 2 — testing an app you have no source for

`discover_app_surface` (Mode 1) summarizes what's *already tested* from
existing cases. `crawl_app_surface` (Mode 2) is different: give it a
live `base_url` and it BFS-crawls the same-origin app — with no source
access — and returns the interactable surface (forms + inputs, buttons,
internal links) per page. Runs authenticated when the project has a
stored auth session. Use it to propose smoke tests for a deployed
third-party or staging app:

1. `crawl_app_surface(project_id, base_url, max_pages)` → surface map.
2. For each meaningful form/flow, `propose_create_case` with steps that
   fill the discovered inputs and assert on the resulting page.

Budget with `max_pages` (default 10, hard cap 50). The crawl skips
assets and logout links.

### Data-driven tests — one case, many rows

Set `dataset` on a case to a JSON array of row objects:

```jsonc
"dataset": [
  {"query": "election results", "expect": "Bihar"},
  {"query": "cricket score", "expect": "India"}
]
```

At dispatch the case expands into one execution per row (results appear
as `Case name [row 1]`, `[row 2]`, …). Steps reference row values as
`{{data.query}}` in any value/header/param/body. Generating edge-case
rows is usually cheaper and more maintainable than generating N nearly
identical cases.

### Pitfall 1 — `feed-check` is a fetch+assert, NOT a pure assertion

❌ This pattern **does not work**:

```jsonc
[
  {"type": "http-request", "value": "http://app/api/overview"},
  {"type": "feed-check", "params": {"assertions": [...]}}   // ← reads nothing from the prior step
]
```

`feed-check` issues **its own** HTTP request. It doesn't see the previous
step's response.

✅ Put assertions **inside the `http-request` step**:

```jsonc
[{
  "type": "http-request",
  "value": "http://172.17.0.1:8090/api/overview",
  "params": {
    "method": "GET",
    "headers": {"Authorization": "Bearer ${TOKEN}"},
    "assertions": [
      {"type": "status", "value": 200},
      {"type": "json-path", "path": "postgres.articles", "operator": "equals", "value": 13891}
    ]
  }
}]
```

### Pitfall 2 — `json-path` does NOT use `$.` prefix

❌ `{"path": "$.status", ...}` — the runner walks the path by dot-splitting; the `$` is treated as a field name and won't match.

✅ `{"path": "status", "operator": "equals", "value": "healthy"}`

### Pitfall 3 — `fill` doesn't work on `<select>`

❌ `{"type": "fill", "selector": "#priority", "value": "high"}` on a `<select>` blows up with `Element is not an <input>, <textarea> or [contenteditable]`.

✅ Use **`select-option`**:

```jsonc
{"type": "select-option", "selector": "#priority", "value": "high"}
```

### Pitfall 4 — `expect-url` needs glob characters

Playwright's `page.waitForURL` does **exact match** for plain strings.

❌ `{"type": "expect-url", "value": "/v2"}` waits forever; URL is `http://host/v2`, not `/v2`.

✅ `{"type": "expect-url", "value": "**/v2**"}` — globs match a substring.

### Pitfall 5 — `wait-for-selector` blocks on in-flight navigations

If the page is mid-redirect, `wait-for-selector` stalls until timeout.
Common on auth-driven SPAs that 401→redirect-to-login after the first
API call.

❌ Anonymous-load test of a redirecting SPA:

```jsonc
[
  {"type": "goto", "value": "http://app/v2"},
  {"type": "wait-for-selector", "selector": "#root"}   // ← stalls during redirect
]
```

✅ Use `wait-timeout` + a navigation-insensitive assertion (e.g., page title,
which lives in `<head>` and survives redirects):

```jsonc
[
  {"type": "goto", "value": "http://app/v2"},
  {"type": "wait-timeout", "value": "3000"},
  {"type": "assert", "selector": "title",
   "params": {"source": "text", "operator": "contains"}, "value": "Sarvajna"}
]
```

---

## 5. Assertion patterns — http-request reference

The three assertion types live inside `step.params.assertions`:

### Status

```jsonc
{"type": "status", "value": 200}
```

### json-path

Walk a dotted path; assert with `equals` or `contains`.

```jsonc
{"type": "json-path", "path": "postgres.articles", "operator": "equals", "value": 13891}
{"type": "json-path", "path": "mode", "operator": "contains", "value": "ldap"}
```

Limitations: no `gt` / `lt` operators on `json-path`. If you need bounds
("at least 1 article"), use `json-schema` instead.

### json-schema

The whole response body validated against a JSON Schema. Best for "shape" or "bounds" checks.

```jsonc
{
  "type": "json-schema",
  "value": "{\"type\":\"object\",\"required\":[\"total\",\"items\"],\"properties\":{\"total\":{\"type\":\"integer\",\"minimum\":1},\"items\":{\"type\":\"array\"}}}"
}
```

Asserting "at least one entry" on a top-level array:

```jsonc
{"type": "json-schema", "value": "{\"type\":\"array\",\"minItems\":1}"}
```

`additionalProperties: true` is applied automatically — your schema doesn't
have to list every field; just the ones you care about. Non-required fields
may also be `null`.

---

## 6. Suite organization conventions

### One project per app

`Sarvajna v2`, `Sarvajna v1`, `TodoLite` — one TraceIQ project per
deployable. Inside the project, suites organize by feature area.

### Suite naming for an API-heavy app

By OpenAPI tag, falling back to the first path segment:

- `Sarvajna · Articles` — `/api/articles*`
- `Sarvajna · Events`
- `Sarvajna · Entities`
- `Sarvajna · Graph`
- `Sarvajna · Pipeline`

### Suite naming for a UI app

By page area:

- `TodoLite · Auth flow`
- `TodoLite · Todo CRUD`
- `TodoLite · API auth`

### Sub-suites: only when you mean isolation

A sub-suite is justified when its cases need to **share a browser session**
(e.g. "Admin moderation" sub-suite where the agent logs in once and runs
ten admin actions in the same browser). Avoid nesting just for
organization — flat is fine and easier to manage.

### Execution mode (pick one per suite)

| Mode | Behavior | Use when |
|---|---|---|
| `SEPARATE` | One job per case; sub-suites share a browser internally | You've grouped cases into sub-suites by session-affinity |
| `CONTINUOUS` | One job per case; fresh browser per case | Default; per-case isolation |
| `PARALLEL` | One job per case, with parallelism hint | AI-agent rapid feedback; many runs, low latency |

Sarvajna API tests use `SEPARATE` (each endpoint is its own isolated job).
TodoLite CRUD tests use `CONTINUOUS` (each case starts clean).

---

## 7. Auth — Personas, headers, cookies

### API tests (Bearer token)

Embed the auth header in the `http-request` step:

```jsonc
{
  "type": "http-request",
  "value": "http://app/api/overview",
  "params": {
    "method": "GET",
    "headers": {"Authorization": "Bearer ${SARVAJNA_TOKEN}"},
    "assertions": [...]
  }
}
```

Today the token is a literal string in the case. **Future improvement**:
reference a workspace-stored credential by name (Phase E follow-up,
`register_app_credential`).

### UI tests (cookie or localStorage)

Sarvajna's SPA reads its token from `localStorage.user = {token, name, role}`.
Inject it with `run-script` BEFORE navigating to the protected route:

```jsonc
[
  {"type": "goto", "value": "http://app/v2"},
  {"type": "run-script", "params": {
    "language": "javascript",
    "body": "localStorage.setItem('user', JSON.stringify({token: '${TOKEN}', name: 'Alice', role: 'admin'})); return 'ok';"
  }},
  {"type": "goto", "value": "http://app/v2/articles"},
  {"type": "wait-timeout", "value": "2000"},
  {"type": "expect-url", "value": "**/v2/articles**"}
]
```

For cookie-based auth (Sarvajna's other variants, or your app), `run-script`
can do `document.cookie = "..."` similarly.

### Personas (Phase B feature)

For long-lived authenticated runs, create a `Persona` once and attach it to
each run via `persona_id`. The worker hydrates the persona's `storageState`
(cookies + localStorage) into the browser context before the first step.
Best for suites where many tests need the same login.

---

## 8. Worker → app reachability

The execution-worker runs in TraceIQ's docker network. From inside the
worker, reach the app via the docker bridge gateway:

- Sarvajna API: `http://172.17.0.1:8090/...`
- Sarvajna UI: `http://172.17.0.1:8091/...`
- A containerized app on the SAME network: use the service hostname (e.g., `http://todolite:8080/`)

When in doubt, ask the user which URL pattern works in their deployment.

---

## 9. Delete policy — what's yours vs. theirs

You can identify what you created this session by tracking the
`agent_session_id` you sent on the create request (Phase E). The MCP
client mints a fresh session id per construction; check `client.agent_session_id`.

**Hard rules:**

| Action | What you should do |
|---|---|
| Delete a case YOU created in this session (matching `agent_session_id` AND `created_by_agent_id`) | Propose-delete freely; tag the proposal with rationale "self-created this session" |
| Delete a case YOU created in a PREVIOUS session | **Ask the human before proposing** — they may have customized it |
| Delete a case the HUMAN created | **Always ask the human** before proposing |
| Delete a suite (regardless of who made it) | **Always confirm with the human**, even if you created it. Suites contain cases; deleting one removes everything beneath. Cascade is wide. |

The reviewer queue is the safety net, but the agent-level discipline above
prevents floods of "delete X" proposals from showing up in the inbox.

---

## 10. End-to-end example: adding coverage for a new feature

A developer just shipped: Sarvajna learned to accept a `priority` field on `POST /api/articles`. The agent's job:

**Step 1. See what's already there.**

```python
surface = await mcp.discover_app_surface(project_id=4)
# Returns: 4 suites, 12 cases, code_paths_covered=[...].
# Articles suite exists; no case asserts on the new priority field.
```

**Step 2. Read source to derive code_paths and behavior.**

Agent does locally:
```bash
# (in agent's working dir)
$ grep -rn "priority" sarvajna_v2/dashboard-service/app/api/articles.py
# learns: priority is optional, defaults to "medium", validated against {low, medium, high}
```

**Step 3. Compose proposals (one for the API change, one for the UI badge).**

```python
proposals = [
  {
    "project_id": 4,
    "test_suite_id": 106,  # Articles suite
    "action": "create",
    "payload": {
      "name": "POST /api/articles accepts priority=high",
      "code_paths": [
        "sarvajna_v2/dashboard-service/app/api/articles.py",
        "sarvajna_v2/dashboard-service/app/models/article.py"
      ],
      "steps": [{
        "type": "http-request",
        "value": "http://172.17.0.1:8090/api/articles",
        "params": {
          "method": "POST",
          "headers": {"Authorization": "Bearer ${TOKEN}"},
          "body": {"headline": "Test", "priority": "high"},
          "assertions": [
            {"type": "status", "value": 201},
            {"type": "json-path", "path": "priority", "operator": "equals", "value": "high"}
          ]
        }
      }]
    },
    "rationale": "New `priority` field on POST /api/articles. Verified by grep of articles.py; valid values are low|medium|high.",
    "ai_confidence": 0.85
  },
  # ... a second proposal for the UI badge ...
]
result = await mcp.bulk_propose_cases(project_id=4, proposals=proposals)
```

**Step 4. Tell the human.**

The agent doesn't auto-accept. It comments on the PR:
> "Added 2 CaseProposals (#88, #89) for the new priority field. Accept at
> /api/case-proposals/88/accept. After accept, runs will be gated."

**Step 5. After acceptance, run + verify.**

```python
run = await mcp.run_suite(suite_id=106)
final = await mcp.wait_for_run(run_id=run.id, timeout_seconds=300)
# final.status == "passed" — done.
```

---

## 11. Cheat sheet — tools by intent

| Intent | Tool(s) |
|---|---|
| Learn what exists in TraceIQ for this project | `discover_app_surface`, `list_suites`, `list_cases`, `get_suite`, `get_case` |
| Learn what step types are valid | `describe_step_types` |
| Learn TraceIQ conventions | `get_authoring_guide` (this doc) |
| Map a PR diff to relevant tests | `select_tests_for_diff` (run vs review — §3.5) |
| Propose new coverage | `propose_create_case` (one) or `bulk_propose_cases` (many) |
| LLM-draft a case from a description | `generate_case_proposal` |
| Modify or remove existing coverage | `propose_update_case`, `propose_delete_case` |
| Backfill `code_paths` on existing cases | `set_code_paths` (one), `bulk_set_code_paths` (many) |
| Run a suite + watch | `run_suite` (git context! tags, environment_id), `wait_for_run`, `get_run`, `get_run_results` |
| Test a dev server on localhost | `run_suite(local_worker_id=…)` + `npm run worker:local` on the dev machine |
| Diagnose a failure | `get_failure_analysis`, `analyze_run` (re-run AI analysis, pick `provider_id`), `get_artifact_url`, `get_run_history`, `list_failure_clusters` / `get_failure_cluster` |
| Judge the change / gate a merge | `get_run_report` (PR-ready markdown), `evaluate_quality_gate`, `get_quality_snapshot` |
| Find weak or noisy tests | `get_test_effectiveness`, `list_flakes`, `list_heal_proposals` |
| Compare two deployments | `create_comparison_run`, `get_comparison` |
| Correlate unit tests on the same commit | `ingest_junit_report`, `list_external_results` |
| Security posture | `run_security_scan` / `get_run_security_findings` (passive, per run); `start_project_security_scan` / `get_security_scan` / `get_security_scan_diff` (ZAP, needs `authorized=true` + allowlisted host) |
| Mobile runs | `list_app_builds` / `get_app_build` → `run_suite(app_build_id=…)` |
| Structural changes | `create_suite`, `delete_suite` (always confirm with human first) |
| See your own pending work | `list_case_proposals` |
| Crawl an app you have no source for (Mode 2) | `crawl_app_surface` |

---

## 12. Anti-patterns (please don't)

- **Don't author by guessing.** Always probe the runner via `describe_step_types`, probe the response shape via a real HTTP call, and probe the existing coverage via `discover_app_surface`. The five pitfalls above all came from skipping a probe step.
- **Don't try to auto-accept proposals.** API keys can't. Even if you could, you'd defeat the safety contract. The human reviews.
- **Don't propose deletes for things you didn't create.** Ask the human first.
- **Don't tag cases with empty `code_paths`.** A case with no paths never benefits from impact analysis and effectively never gets selected by `select_tests_for_diff`. Better to spend ten minutes deriving the paths than ship a coverage gap.
- **Don't generate cases for mutating endpoints (POST/PATCH/DELETE) against shared environments.** Either skip them or explicitly opt-in via the `include_mutating: true` flag on `from-openapi`.

---

## 13. When you're stuck

- Failed test? `get_run` → read `error_message` per `TestCaseResult`. Cluster failures by selector to see if they share a root cause.
- Failed proposal? `list_case_proposals(status="rejected")` to see what the reviewer said in `decision_note`.
- Don't understand a step? `describe_step_types` again; the examples in this guide are not exhaustive.
- The guide is wrong? It's hand-maintained. File a `propose_update_case` against TraceIQ's own AGENT_GUIDE coverage (this is allowed; the doc is versioned with the MCP server).
