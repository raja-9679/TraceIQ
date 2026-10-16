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
| `mode` | no | `suite` | `suite` runs the whole suite. `targeted` runs only the cases impact analysis maps to the PR's changed files, with a strict fallback to the full suite (see *Targeted runs*). Needs `project-id`. |
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
| `selection-mode` | `suite`, `targeted`, or `fallback` (targeted mode that ran the full suite). |
| `cases-run` | Cases executed. |
| `cases-total` | Cases in the suite. |
| `fallback-reason` | Why targeted mode ran the full suite (empty otherwise). |

## Targeted runs

```yaml
- uses: ./integrations/github-action
  with:
    base-url: https://traceiq.example.com
    api-key: ${{ secrets.TRACEIQ_API_KEY }}
    suite-id: 12
    project-id: 3
    mode: targeted
```

On a pull request the action asks impact analysis (scoped to `suite-id`)
which cases the changed files touch, and runs only those. It runs the
**full suite** instead when any changed file has no mapped test, nothing
matched, every match is quarantined, impact analysis fails, the run is not
a pull request, or the targeted run is refused. The reason is in the PR
comment and the `fallback-reason` output.

A targeted run is never reported as complete coverage: the comment heads
with "Targeted run: N of M cases", quarantined matches are listed as
skipped, and the quality gate's `coverage` check reads
"targeted — N of M cases ran" (informational; it never fails the gate).
Keep `code_paths` on your cases current — that mapping is what decides
what runs.

## PR comment

On pull requests (with a `github-token`), the action posts a single summary
comment and keeps updating it on re-runs (idempotent via a hidden HTML
marker). The comment contains:

- **Overall verdict** — pass/fail with passed/failed counts and the commit.
- **Failing cases** — for each failing case, the step that failed (type and
  selector), the error (truncated), a screenshot link and a link to the run.
  Screenshot links are presigned by `GET /api/runs/{id}/failures`: they
  expire after an hour and the server only signs keys under that run's own
  artifact prefix. Use `traceiq replay <run>` to reproduce one locally.
- **Test selection** — when `project-id` is set, the result of TraceIQ's
  impact analysis on the PR diff: which test cases match the changed files
  and via which code paths; otherwise, the suite that ran.
- **Results** — a per-test-case pass/fail table (status, duration, error
  details) for each run, with links to the run in TraceIQ plus trace/video
  artifacts when available.
- **Failure analysis** — TraceIQ's AI failure analysis (summary and
  suggestions) when present on the run.

## Recording deployments (DORA)

TraceIQ's delivery report (DORA metrics) is built from explicit deployment
events — it never guesses deployments from runs. Post one from the job that
deploys, after the deploy step. The API key's user needs editor access on the
project; a project-scoped key must be scoped to that project. `external_id`
makes a re-run of the same CI job update the record instead of counting a
second deployment.

```yaml
      - name: Record deployment in TraceIQ
        if: always()   # a failed deploy is a change failure too
        env:
          TRACEIQ_URL: ${{ vars.TRACEIQ_URL }}
          TRACEIQ_API_KEY: ${{ secrets.TRACEIQ_API_KEY }}
          PROJECT_ID: ${{ vars.TRACEIQ_PROJECT_ID }}
        run: |
          STATUS=success; [ "${{ job.status }}" = "success" ] || STATUS=failed
          curl -fsS -X POST "$TRACEIQ_URL/api/projects/$PROJECT_ID/deployments" \
            -H "X-API-Key: $TRACEIQ_API_KEY" -H "Content-Type: application/json" \
            -d "{\"git_commit\": \"${{ github.sha }}\", \"git_branch\": \"${{ github.ref_name }}\",
                 \"git_repo\": \"${{ github.repository }}\", \"environment\": \"production\",
                 \"version\": \"${{ github.ref_name }}-${{ github.run_number }}\", \"status\": \"$STATUS\",
                 \"source\": \"github-actions\", \"external_id\": \"${{ github.run_id }}-${{ github.run_attempt }}\",
                 \"url\": \"${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}\"}"
```

Optional fields: `deployed_at` and `commit_at` (ISO 8601; `commit_at` makes
lead time exact — without it lead time starts at the first TraceIQ run with the
same `git_commit`), `environment_id` instead of the environment's name. Use the
same commit SHA form (full SHA) as your runs' `git_commit`.

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
