# Using TraceIQ from Claude Code (agent-authored tests)

This guide is for developers building a web application with Claude Code (or
Cursor) who want the agent to create and run TraceIQ regression tests as it
builds features. Copy-paste setup + prompt below.

## 1. Prerequisites (once per TraceIQ deployment)

- TraceIQ stack running, including the `mcp-server` container:
  ```bash
  cd infrastructure
  docker compose --env-file env.local -f docker-compose.yml up -d mcp-server
  ```
  The MCP server listens on **http://localhost:8088/mcp**.

- A TraceIQ API key (`tiq_...`). Mint one in the UI (Workspace → API Keys) or:
  ```bash
  curl -X POST http://localhost:8000/api/workspaces/<WORKSPACE_ID>/api-keys \
    -H "Authorization: Bearer <your JWT>" -H "Content-Type: application/json" \
    -d '{"workspace_id": <WORKSPACE_ID>, "name": "claude-code-<project>"}'
  ```
  The `secret` field is shown only once — save it. One key per developer or
  per project is recommended so keys can be revoked individually.

## 2. Register the MCP server in your app's repo (once per project)

```bash
claude mcp add traceiq --transport http http://localhost:8088/mcp \
  --header "X-API-Key: tiq_YOUR_KEY_HERE"
```

## 3. Add this to your app's CLAUDE.md

```markdown
## Regression testing via TraceIQ (MCP)

You have the `traceiq` MCP server — a UI/API test platform that runs Playwright
tests in its own workers. Use it to create and run regression tests for every
feature you build in this app.

### Workflow — after completing each feature
1. First time only: call `get_authoring_guide` and `describe_step_types` to
   learn the step schema. Call `list_projects` / `create_project` and
   `create_suite` to set up one project for this app with suites per area
   (e.g. "Auth", "Dashboard").
2. Read the source code of the feature you just built (you have the repo —
   TraceIQ never sees the code) and identify the user journeys and API
   endpoints worth protecting.
3. **Probe before asserting.** For API/feed tests, curl the endpoint yourself
   first and assert on fields that actually exist in the real response. For UI
   tests, take selectors from the JSX/templates you wrote — prefer stable
   `data-testid` attributes (add them to the code if missing) over classes.
4. Create cases with `propose_create_case` (creates a review proposal) by
   default; use direct creation only when I explicitly ask. Set `code_paths`
   to the source files each case covers so diff-based test selection works.
5. Run with `run_suite`, poll `get_run`, and on failures call
   `get_run_results` / `get_failure_analysis`. Decide: is it a bug in my app
   (fix the app) or a bad test (fix the test)? Iterate until green.
6. When you change existing behavior, use `select_tests_for_diff` with the
   changed files to run only the affected cases, and `propose_update_case`
   to keep tests in sync with intentional changes.

### Critical: base URL
TraceIQ's workers run in Docker. `localhost` in a test step points at the
worker container, NOT this app. Always use
`http://host.docker.internal:<port>` as the app URL in goto/http-request
steps (the dev server must listen on 0.0.0.0, not 127.0.0.1).

### Conventions
- One journey per case, 3–10 steps, ending in an `expect-*` assertion that
  proves the journey succeeded.
- Steps are JSON: `{id, type, selector, value}`. Get valid types from
  `describe_step_types` — never invent step types.
- Give cases descriptive names ("Login with valid credentials shows
  dashboard"), and a `rationale` on proposals explaining what regression
  the case guards against.
```

## Notes

- **Dev server binding:** the app under test must be reachable from Docker —
  run Vite with `--host`, Next.js with `HOST=0.0.0.0`, etc.
- **Proposals flow:** agent-created cases land in TraceIQ → Proposals for
  human review by default (API-key callers cannot create directly). Approve
  or reject them there.
- **Backend port:** examples above assume the backend on :8000; adjust if
  your deployment maps it elsewhere.
- See `AGENT_GUIDE.md` in this directory for the full MCP tool reference.
