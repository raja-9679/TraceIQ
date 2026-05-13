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
          browser: chromium
```

The action picks up the PR commit SHA, branch, and PR URL automatically
and forwards them to TraceIQ so the run is searchable by git context.

## Build (for action authors)

```bash
cd integrations/github-action
npm install
npm run build       # produces dist/index.js
git add dist/ action.yml package.json src/
```

GitHub requires actions to ship their compiled JS in `dist/`. The build
step is intentionally not committed in this scaffold — wire it into your
release tooling.
