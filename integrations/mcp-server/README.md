# TraceIQ MCP Server

Model Context Protocol server that exposes TraceIQ as a tool for AI coding
agents. With this installed, agents like Claude Code can call TraceIQ to
verify their code changes against your existing test suites — the regression
safety-net for AI-authored code.

## Install

```bash
cd integrations/mcp-server
pip install -e .
```

## Configure

Create an API key from your TraceIQ workspace (Settings → API Keys), then
set environment variables:

```bash
export TRACEIQ_BASE_URL=https://your-traceiq-host
export TRACEIQ_API_KEY=tiq_xxxxxxxxxxxxxxxxxxxxxxxx
export TRACEIQ_AGENT_ID=claude-code   # optional; identifies the agent
```

## Wire into Claude Code

Add to `~/.claude/mcp_servers.json` (or your project's `.mcp.json`):

```json
{
  "mcpServers": {
    "traceiq": {
      "command": "traceiq-mcp",
      "env": {
        "TRACEIQ_BASE_URL": "https://your-traceiq-host",
        "TRACEIQ_API_KEY": "tiq_xxxxxxxxxxxxxxxxxxxxxxxx",
        "TRACEIQ_AGENT_ID": "claude-code"
      }
    }
  }
}
```

## Tools

| Tool | Purpose |
|---|---|
| `list_projects` | Enumerate projects the agent can see |
| `list_suites` | List test suites for a project |
| `run_suite` | Trigger a regression run, tagged with optional git_commit / git_branch / git_pr_url |
| `get_run` | Fetch a run's current status + summary counts |
| `get_run_results` | Fetch per-case results (test name, status, error, trace URL) |
| `get_artifact_url` | Resolve a presigned URL for a captured artifact (trace / video / screenshot) |
| `get_failure_analysis` | Fetch the structured AI failure analysis for a finalized run |
| `wait_for_run` | Poll until a run reaches a terminal state, then return final results |

## Verification

```bash
# Sanity-check by listing your projects:
python -m traceiq_mcp.smoke_test
```
