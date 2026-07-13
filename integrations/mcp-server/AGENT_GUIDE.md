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

## 2. The standard session flow

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

That's the loop. The first three reads ground you; everything else cycles
between propose, run, diagnose, fix.

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
| Map a PR diff to relevant tests | `select_tests_for_diff` |
| Propose new coverage | `propose_create_case` (one) or `bulk_propose_cases` (many) |
| LLM-draft a case from a description | `generate_case_proposal` |
| Modify or remove existing coverage | `propose_update_case`, `propose_delete_case` |
| Backfill `code_paths` on existing cases | `bulk_set_code_paths` |
| Run a suite + watch | `run_suite`, `wait_for_run`, `get_run`, `get_run_results` |
| Diagnose a failure | `get_run`, `get_failure_analysis`, `get_artifact_url`, `get_run_history` |
| Structural changes | `create_suite`, `delete_suite` (always confirm with human first) |
| See your own pending work | `list_case_proposals` |

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
