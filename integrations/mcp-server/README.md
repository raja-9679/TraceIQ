# TraceIQ MCP Server

Model Context Protocol server that exposes TraceIQ as a tool for AI coding
agents. Agents can call TraceIQ to create test suites, trigger regression
runs, and check results — the safety-net for AI-authored code.

> **Building an app with Claude Code?** See [CLAUDE_CODE_SETUP.md](./CLAUDE_CODE_SETUP.md)
> for copy-paste setup and the CLAUDE.md prompt that makes the agent author
> and run TraceIQ tests for every feature it builds.

> **Connecting to a hosted TraceIQ?** See [SAAS_SETUP.md](./SAAS_SETUP.md) —
> the one-token auth model and per-agent config examples (Claude Code,
> Cursor, VS Code, Windsurf). Auth accepts `X-API-Key: tiq_...` or
> `Authorization: Bearer tiq_...`.

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

## Tools (50, all with typed output schemas)

Every tool declares a Pydantic output model, so MCP clients receive
`outputSchema` on `tools/list` and validated `structuredContent` on every
call (with a JSON text fallback for older clients).

### Core catalogue

| Tool | Purpose |
|------|---------|
| `list_workspaces` | List workspaces the API key can access |
| `create_project` | Create a new project in a workspace |
| `list_projects` | List all projects visible to this key |
| `list_suites` / `get_suite` | List / fetch test suites |
| `create_suite` / `delete_suite` | Create / delete a suite (cascade-safe) |
| `list_cases` | List cases (slim rows: tags, priority, code_paths, last_validated_commit) |
| `get_case` | Fetch a single case with full steps |

### Runs

| Tool | Purpose |
|------|---------|
| `run_suite` | Trigger a run — git context, `tags` filter, `environment_id`, `local_worker_id` (dev-machine worker), `app_build_id` (mobile) |
| `get_run` / `wait_for_run` | Fetch / poll until terminal status |
| `get_run_results` | Per-case results (incl. `test_case_id`) |
| `get_failure_analysis` | Stored AI failure analysis + failed results |
| `analyze_run` | (Re-)run AI analysis, optionally with a chosen LLM `provider_id` (async) |
| `get_artifact_url` | Presigned URL for trace/video/screenshot |

### Impact analysis & discovery

| Tool | Purpose |
|------|---------|
| `select_tests_for_diff` | Changed files → cases to **run** vs **review** (`suggested_action` + `reasons`, per-pattern match detail, last result, `last_validated_commit`) + uncovered files |
| `discover_app_surface` | What's currently tested in a project |
| `crawl_app_surface` | Mode-2: crawl a live app you have no source for |
| `get_run_history` | Case history (exact `matched_by: id` linking) |
| `set_code_paths` / `bulk_set_code_paths` | Maintain the case↔code mapping |

### Authoring (human-review queue)

| Tool | Purpose |
|------|---------|
| `propose_create_case` / `propose_update_case` / `propose_delete_case` | Queue changes; high-confidence create/update may auto-apply per workspace policy |
| `bulk_propose_cases` | Many proposals, per-item results |
| `generate_case_proposal` | Server-side LLM drafts a case |
| `list_case_proposals` | Your pending work queue |
| `describe_step_types` / `get_authoring_guide` | The authoring reference |

### Quality & results

| Tool | Purpose |
|------|---------|
| `get_quality_snapshot` | Project health: pass rate, trend, flakes, monitors, security counts |
| `evaluate_quality_gate` | Go/no-go for a commit/branch against project policy |
| `get_run_report` | Consolidated per-run report + PR-ready markdown |
| `get_test_effectiveness` | Per-test signal metrics (failure rate, clusters surfaced) |
| `list_failure_clusters` / `get_failure_cluster` | Deduped root causes |
| `list_flakes` | Flake scores + quarantine state |
| `list_heal_proposals` | Worker-suggested selector fixes (read-only) |
| `create_comparison_run` / `get_comparison` | Same suite vs a different deployment |
| `ingest_junit_report` / `list_external_results` | Correlate unit-test results on the same commit |

### Security & mobile

| Tool | Purpose |
|------|---------|
| `run_security_scan` / `get_run_security_findings` | Passive scan of a run's captured traffic |
| `start_project_security_scan` | ZAP scan (requires `authorized=true` + allowlisted host; async) |
| `list_security_scans` / `get_security_scan` / `get_security_scan_diff` | Scan history, findings, new-vs-resolved diff |
| `list_app_builds` / `get_app_build` | Mobile binaries to pin via `run_suite(app_build_id=…)` |

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
