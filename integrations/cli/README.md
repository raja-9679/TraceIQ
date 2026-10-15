# traceiq-cli

Command line for TraceIQ: trigger and gate runs from any CI or terminal, replay
a failure on your machine, and keep test cases in git.

Pure Python standard library (3.9+), no runtime dependencies:

```bash
pipx install ./integrations/cli        # or: pip install ./integrations/cli
traceiq login --url https://traceiq.example.com      # prompts for an API key
```

The key is stored in `~/.config/traceiq/config.json` (Linux; `~/Library/Application
Support/traceiq/` on macOS, `%APPDATA%\traceiq\` on Windows) with mode 0600.
`TRACEIQ_URL` / `TRACEIQ_API_KEY` override it, so CI never needs `login`.
Use a project-scoped key in CI.

## Commands

| Command | What it does |
| --- | --- |
| `traceiq run --suite 12 [--cases 3,4] [--commit SHA] [--wait]` | Trigger a run. With `--wait`, exit with the project's quality-gate verdict (falls back to run status when no gate is reachable; `--no-gate` forces run status). |
| `traceiq status <run>` | Show a run and its failing results. |
| `traceiq replay <run> [--result ID]` | Download the reproduce-locally bundle (default: first failing result), unpack it to `./traceiq-repro/`, and run `repro.spec.ts` with your local Playwright if `npx playwright` is available; otherwise print the steps. Secrets are never in the bundle — export the `TRACEIQ_SECRET_*` variables its README lists. |
| `traceiq pull <suite> [--dir cases/]` | Write the suite tree as one JSON file per case (sorted keys, stable layout). Refuses to overwrite local edits without `--force`. |
| `traceiq push [cases/] [--dry-run] [-m "why"]` | Send edits back. Creates, updates, moves (file moved to another suite's directory or `suite_id` changed) and deletions (file removed) become **proposals** for human review — the same queue and auto-apply policy as agent writes. A case changed on the server since your pull is reported as a conflict, not overwritten. |
| `traceiq version` | Client and server versions; warns when the server's API major differs. |

New case: add `some-name.json` with at least `{"name": ..., "steps": [...]}`
in the directory of the suite it belongs to.

## Exit codes

`0` passed/ok · `1` failed tests, failed gate, or sync conflicts · `2` usage ·
`3` timed out · `4` run still in progress (`status`) · `5` API/network error.

## Tests

```bash
python -m pytest integrations/cli/tests
```
