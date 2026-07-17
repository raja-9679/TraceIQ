# Connecting your AI agent to TraceIQ (hosted / SaaS)

This guide is for TraceIQ **users** connecting an AI coding agent (Claude Code,
Cursor, VS Code Copilot, Windsurf, …) to a hosted TraceIQ deployment over MCP.
For running TraceIQ locally with Docker, see `CLAUDE_CODE_SETUP.md`. For the
full MCP tool reference, see `AGENT_GUIDE.md`.

Throughout this guide, replace `https://mcp.traceiq.io/mcp` with your
deployment's actual MCP endpoint.

---

## The model: one token, nothing else

You do **not** configure an org ID, workspace ID, or project ID in your agent.
The API key alone carries all scope:

- A key is minted **inside a workspace**, so it can only ever see that
  workspace's data.
- A key can optionally be **narrowed to a single project** at creation time.
- A key carries an **RBAC role** (viewer / editor / admin), so what the agent
  may do is decided when the key is minted, not in client config.
- Keys can be given an **expiry** and **revoked** at any time — revoking the
  key instantly cuts off the agent.

The agent discovers what it can access by calling `list_workspaces` /
`list_projects`; it only sees what the key permits.

**Recommendation:** mint one key per developer *and* per app (e.g.
`claude-code-storefront-raja`), scoped to that app's project, with the
editor role. Blast radius stays small and revocation is surgical.

## 1. Mint an API key

In the TraceIQ UI: **Workspace → API Keys → Create key** — choose the scope
(whole workspace or one project), a role, and an optional expiry.

Or via the API:

```bash
curl -X POST https://api.traceiq.io/api/workspaces/<WORKSPACE_ID>/api-keys \
  -H "Authorization: Bearer <your JWT>" -H "Content-Type: application/json" \
  -d '{"workspace_id": <WORKSPACE_ID>, "project_id": <PROJECT_ID or null>, "name": "claude-code-myapp"}'
```

The response contains a `secret` starting with `tiq_` — **it is shown exactly
once**. Store it like a password (env var, secret manager); never commit it.

## 2. Authentication — two accepted forms

Every request to the MCP endpoint must carry the key. Both of these work;
use whichever your agent supports:

```
X-API-Key: tiq_YOUR_KEY
Authorization: Bearer tiq_YOUR_KEY
```

---

## 3. Configure your agent

### Claude Code

One command, run inside your app's repo:

```bash
claude mcp add traceiq --transport http https://mcp.traceiq.io/mcp \
  --header "X-API-Key: tiq_YOUR_KEY"
```

Or commit a `.mcp.json` to the repo and keep the secret in an env var
(Claude Code expands `${...}`):

```json
{
  "mcpServers": {
    "traceiq": {
      "type": "http",
      "url": "https://mcp.traceiq.io/mcp",
      "headers": { "X-API-Key": "${TRACEIQ_API_KEY}" }
    }
  }
}
```

Then export `TRACEIQ_API_KEY=tiq_...` in your shell profile. Verify with
`/mcp` inside Claude Code — you should see ~30 `traceiq` tools.

Tip: add the "Regression testing via TraceIQ" section from
`CLAUDE_CODE_SETUP.md` to your app's `CLAUDE.md` so the agent writes and runs
TraceIQ tests as part of its normal dev loop.

### Cursor

`~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (per project):

```json
{
  "mcpServers": {
    "traceiq": {
      "url": "https://mcp.traceiq.io/mcp",
      "headers": { "X-API-Key": "tiq_YOUR_KEY" }
    }
  }
}
```

### VS Code (GitHub Copilot agent mode)

`.vscode/mcp.json` — the `inputs` block makes VS Code prompt for the key
once and store it secretly, so nothing sensitive is committed:

```json
{
  "inputs": [
    {
      "id": "traceiq-key",
      "type": "promptString",
      "password": true,
      "description": "TraceIQ API key (tiq_...)"
    }
  ],
  "servers": {
    "traceiq": {
      "type": "http",
      "url": "https://mcp.traceiq.io/mcp",
      "headers": { "X-API-Key": "${input:traceiq-key}" }
    }
  }
}
```

### Windsurf

`~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "traceiq": {
      "serverUrl": "https://mcp.traceiq.io/mcp",
      "headers": { "X-API-Key": "tiq_YOUR_KEY" }
    }
  }
}
```

### Any stdio-only MCP client

If your client cannot speak HTTP MCP, run the bundled stdio server locally —
it talks to the hosted REST API:

```json
{
  "mcpServers": {
    "traceiq": {
      "command": "uvx",
      "args": ["traceiq-mcp"],
      "env": {
        "TRACEIQ_BASE_URL": "https://api.traceiq.io",
        "TRACEIQ_API_KEY": "tiq_YOUR_KEY"
      }
    }
  }
}
```

(Until `traceiq-mcp` is published to PyPI, install from source:
`pip install -e integrations/mcp-server` and use
`"command": "python", "args": ["-m", "traceiq_mcp.server"]`.)

---

## 4. First-run smoke test

Ask your agent:

> Using the traceiq tools, list my projects, then call get_authoring_guide
> and describe_step_types.

If `list_projects` returns your project(s), auth and scoping are working.

## Security notes

- Agent-created test cases go through the **proposal queue** by default —
  API-key callers cannot directly accept/reject proposals; a human reviews
  them in TraceIQ.
- Rotate keys periodically; set `expires_in_days` at mint time for
  short-lived CI keys.
- The MCP server is stateless and never stores your key — it forwards it to
  the TraceIQ backend on each tool call, where RBAC is enforced.

## Known limitations

- **claude.ai web connectors** expect OAuth for remote MCP servers, so this
  header-key setup covers Claude Code, Cursor, VS Code, and Windsurf — not
  the claude.ai chat connector UI. OAuth 2.1 support is on the roadmap.
