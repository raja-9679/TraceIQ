# TraceIQ MCP Server

Model Context Protocol server that exposes TraceIQ as a tool for AI coding
agents. Agents can call TraceIQ to create test suites, trigger regression
runs, and check results — the safety-net for AI-authored code.

Two transport modes:

| Mode | When to use |
|------|-------------|
| **HTTP** (recommended) | TraceIQ is on a server. Any IDE (Claude Code, Cursor, Copilot, Windsurf) connects via URL — no local install needed. |
| **stdio** | Local dev only. Claude Code spawns the process as a subprocess. |

---

## HTTP transport (recommended for SaaS)

### 1. Start the MCP server

The MCP server is included in `docker-compose.yml`. Start it with the rest
of TraceIQ:

```bash
cd infrastructure
docker compose up -d mcp-server
```

It listens on `http://your-traceiq-host:8088`.

### 2. Mint a workspace API key

From the TraceIQ UI or API:

```bash
curl -X POST https://your-traceiq-host/api/workspaces/{id}/api-keys \
  -H "Authorization: Bearer <your-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-agent","workspace_id":<id>}'
# Returns: { "secret": "tiq_xxxx..." }
```

### 3. Configure your IDE

**Claude Code** — `~/.claude/mcp_servers.json` or project `.mcp.json`:
```json
{
  "mcpServers": {
    "traceiq": {
      "type": "http",
      "url": "http://your-traceiq-host:8088/mcp",
      "headers": { "X-API-Key": "tiq_xxxxxxxxxxxxxxxxxxxxxxxx" }
    }
  }
}
```

**Cursor** — `.cursor/mcp.json` in your project root:
```json
{
  "mcpServers": {
    "traceiq": {
      "url": "http://your-traceiq-host:8088/mcp",
      "headers": { "X-API-Key": "tiq_xxxxxxxxxxxxxxxxxxxxxxxx" }
    }
  }
}
```

**VS Code + GitHub Copilot** — `.vscode/mcp.json` in your project root:
```json
{
  "servers": {
    "traceiq": {
      "type": "http",
      "url": "http://your-traceiq-host:8088/mcp",
      "headers": { "X-API-Key": "tiq_xxxxxxxxxxxxxxxxxxxxxxxx" }
    }
  }
}
```

**Windsurf** — `~/.codeium/windsurf/mcp_config.json`:
```json
{
  "mcpServers": {
    "traceiq": {
      "serverUrl": "http://your-traceiq-host:8088/mcp",
      "headers": { "X-API-Key": "tiq_xxxxxxxxxxxxxxxxxxxxxxxx" }
    }
  }
}
```

---

## stdio transport (local only)

### Install

```bash
cd integrations/mcp-server
pip install -e .
```

### Configure Claude Code

`~/.claude/mcp_servers.json`:
```json
{
  "mcpServers": {
    "traceiq": {
      "command": "traceiq-mcp",
      "env": {
        "TRACEIQ_BASE_URL": "http://localhost:8000",
        "TRACEIQ_API_KEY": "tiq_xxxxxxxxxxxxxxxxxxxxxxxx",
        "TRACEIQ_AGENT_ID": "claude-code"
      }
    }
  }
}
```

---

## Tools

| Tool | Purpose |
|------|---------|
| `list_workspaces` | List workspaces the API key can access |
| `create_project` | Create a new project in a workspace |
| `list_projects` | List all projects visible to this key |
| `list_suites` | List test suites in a project |
| `create_suite` | Create a new test suite |
| `get_suite` | Fetch a suite's details |
| `delete_suite` | Delete a suite (cascade-safe) |
| `list_cases` | List test cases |
| `get_case` | Fetch a single case |
| `propose_create_case` | Submit a new case for human review |
| `propose_update_case` | Submit a case update for human review |
| `propose_delete_case` | Submit a case deletion for human review |
| `bulk_propose_cases` | Submit multiple proposals in one call |
| `set_code_paths` | Set source file paths on a case |
| `bulk_set_code_paths` | Set code paths on many cases at once |
| `generate_case_proposal` | LLM-generate a case proposal |
| `list_case_proposals` | List pending proposals |
| `run_suite` | Trigger a regression run |
| `get_run` | Fetch a run's status and counts |
| `get_run_results` | Fetch per-case results |
| `wait_for_run` | Poll until a run reaches terminal state |
| `get_failure_analysis` | Fetch the AI failure analysis for a run |
| `get_artifact_url` | Resolve a presigned artifact URL |
| `discover_app_surface` | See what's currently tested in a project |
| `select_tests_for_diff` | Impact analysis: match changed files to test cases |
| `get_run_history` | Run history for a specific test case |
| `describe_step_types` | Step-type catalog with shapes and examples |
| `get_authoring_guide` | Full agent authoring guide |

---

## Verify

```bash
# stdio smoke test
TRACEIQ_BASE_URL=http://localhost:8000 \
TRACEIQ_API_KEY=tiq_xxx \
python -m traceiq_mcp.smoke_test

# HTTP health check
curl http://your-traceiq-host:8088/health
```
