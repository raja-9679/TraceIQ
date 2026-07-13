# TraceIQ GitHub Action

Gate a pull request on TraceIQ regression results. When an AI coding agent
opens a PR, this action runs the configured TraceIQ test suite against the
PR's commit, posts the result back to the PR as a comment, and fails the
check (blocking merge) if regressions are detected.

## Setup

1. Generate an API key in TraceIQ (workspace → Settings → API Keys).
2. Add it as a GitHub repo secret named `TRACEIQ_API_KEY`.
3. Drop this workflow into `.github/workflows/traceiq.yml`:

```yaml
name: TraceIQ regression
on:
  pull_request:
    branches: [main]

jobs:
  regression:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write   # for posting the summary comment
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: traceiq/github-action@v1
        with:
          base-url: https://traceiq.example.com
          api-key: ${{ secrets.TRACEIQ_API_KEY }}
          suite-id: 42
          project-id: 7                          # enables impact analysis in the PR comment
          browser: chromium
          fail-on: failures                      # or "none" for report-only
          github-token: ${{ secrets.GITHUB_TOKEN }}  # optional; defaults to the workflow token
```

The action picks up the PR commit SHA, branch, and PR URL automatically and
forwards them to TraceIQ so the run is searchable by git context.

## Inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `base-url` | yes | — | TraceIQ base URL, e.g. `https://traceiq.example.com`. |
| `api-key` | yes | — | TraceIQ workspace API key (`tiq_...`). Use a repo secret. |
| `suite-id` | yes | — | TraceIQ test suite ID to run. |
| `case-id` | no | — | Run only this test case. |
| `project-id` | no | — | TraceIQ project ID. When set on a PR, the action runs impact analysis on the PR's changed files and the comment reports which test cases the diff touches, and why. |
| `github-token` | no | `${{ github.token }}` | Token used to read the PR diff and post/update the summary comment. Needs `pull-requests: write`. |
| `browser` | no | `chromium` | Comma-separated browsers. |
| `agent-id` | no | `github-action` | Identifier for the agent/system triggering the run. |
| `triggered-by` | no | `ci` | One of `human\|schedule\|api_agent\|ci\|webhook`. |
| `poll-interval-seconds` | no | `10` | How often to poll for run completion. |
| `timeout-seconds` | no | `1800` | Maximum time to wait for the run to finish. |
| `fail-on` | no | `failures` | Quality gate: `failures` fails the step when any run ends in failed/error (or times out); `none` never fails the step (report-only). |
| `post-pr-comment` | no | `true` | Post/update the PR summary comment. |

## Outputs

| Output | Description |
| --- | --- |
| `run-id` | TraceIQ run ID(s) that were created (comma-separated). |
| `status` | `passed` or `failed` (overall gate result). |
| `passed` | Total passed test cases across runs. |
| `failed` | Total failed test cases across runs. |

## PR comment

On pull requests (with a `github-token`), the action posts a single summary
comment and keeps updating it on re-runs (idempotent via a hidden HTML
marker). The comment contains:

- **Overall verdict** — pass/fail with passed/failed counts and the commit.
- **Test selection** — when `project-id` is set, the result of TraceIQ's
  impact analysis on the PR diff: which test cases match the changed files
  and via which code paths; otherwise, the suite that ran.
- **Results** — a per-test-case pass/fail table (status, duration, error
  details) for each run, with links to the run in TraceIQ plus trace/video
  artifacts when available.
- **Failure analysis** — TraceIQ's AI failure analysis (summary and
  suggestions) when present on the run.

## Build (for action authors)

The action is self-contained: `dist/index.js` is the compiled bundle
(via `@vercel/ncc`) that `action.yml` points at, and it is committed so the
action runs on a bare runner without `npm install`. After changing
`src/index.js`:

```bash
cd integrations/github-action
npm install
npm run build       # regenerates dist/
git add dist/ action.yml package.json src/
```
