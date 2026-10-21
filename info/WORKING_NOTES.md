# Working notes — how this project is worked on

Assistant session memory lives on one laptop only (`~/.claude/projects/…`).
This file carries the parts of it that matter on any machine: how the owner
wants work done, how the local stack is run, and the traps that cost time.
No credentials or internal addresses belong here — keep those in your own
password manager / local notes.

Read in this order when resuming: `info/HANDOFF.md` ("Resume here" at the
top) → `info/ROADMAP_EXECUTION_2026-10.md` (tracker) → this file → `CLAUDE.md`.

## How the owner wants work done

- **Act, don't ask.** When the path is clear, take it and state assumptions
  ("assuming X — say if you'd rather Y"). Still confirm destructive or
  outward-facing steps (deleting branches, force-push, wiping volumes,
  deleting pre-existing suites/cases).
- **Long runs are fine.** For a big list, keep going feature after feature,
  test in between, report at the end. Record product defaults in the
  tracker so they can be overruled later.
- **Check before designing.** This codebase is large and mature; grep for a
  feature before designing it, and scale ceremony to the ask (the SSO
  episode: OIDC + SAML + LDAP already existed).
- **Lead with visible impact.** After a deploy, say first whether the owner
  will SEE anything (e.g. "no UI change in this deploy"), then the checks.
- **Don't drop features — park them** in `SCOPE_NOTES.md` instead of
  removing them from scope.
- **Keep the user guide current** (owner request, 2026-10-21). Every feature
  added or changed updates its page under `frontend/public/docs/` (served at
  `/docs/`) with fresh screenshots/video — see CLAUDE.md "User guide". The
  owner reads the guide one sidebar item at a time and approves each before
  the next is written.
- **Commits:** plain messages, **no `Co-Authored-By` trailer** (explicit rule,
  overrides any tool reminder). End pushed commit subjects with `[skip ci]`.
- **GitHub Actions must not run on push.** Both workflows are
  `workflow_dispatch`-only; keep it that way. Never push `main` or tags or
  open PRs unasked.
- **Never `git pull` / merge / rebase onto `origin/*`** except the branches
  listed below: GitHub still holds pre-rewrite history with removed secrets
  (see HANDOFF "Read this before you touch git").
- **Redesign branch is design-only.** Functional work goes on
  `feature/enterprise-auth-ai` (own branch → merge), then is merged into
  `feature/redesign-a` and its UI ported to the redesign look. Visual rules:
  white panels/cards (no colour-filled or black cards), pastel only as small
  accents, dark pill buttons, labelled controls (no icon-only buttons),
  nothing "hanging"/floating. Design canvas:
  https://claude.ai/artifact/FA7LQkAxRYHKUmQh4u2X34.
- **Login/Signup:** keep the ORIGINAL pages; three redesigns were rejected.
  Don't redesign them unprompted; mock first if asked.
- **Agent-facing product rules:** TraceIQ never reads customer source code —
  the agent does that client-side and sends paths (Mode 1 = agent has source,
  Mode 2 = URL + token only; both must be supported). An agent may delete
  what it created in its own session; pre-existing suites/cases need the
  owner's permission. For API tests, probe the real response first, then
  write assertions.

## Branches (as of 2026-10-21)

| Local | GitHub | Notes |
|---|---|---|
| `feature/enterprise-auth-ai` | `feature/enterprise-auth-ai-2026-10` | functional mainline (rewritten history) |
| `feature/redesign-a` | `feature/redesign-a` | design branch, base merged in |
| `main` | `main` (OLD history) | do not merge; see HANDOFF C5 plan |

On a new laptop: clone, then
`git checkout -b feature/enterprise-auth-ai origin/feature/enterprise-auth-ai-2026-10`
and `git branch feature/redesign-a origin/feature/redesign-a`, then
`git worktree add ../TraceIQ-redesign feature/redesign-a` (the redesign branch
lives in a second folder, `TraceIQ-redesign`, which images are built from).
Full checklist: HANDOFF "Setting up a fresh laptop" → 0. Those two are safe to pull. Do not check out or merge the other origin
branches.

## Running it locally

The owner's stack runs the **community compose file with locally built
`:dev` images**, not `infrastructure/docker-compose.yml`.

```bash
# from the repo root
docker build -t ghcr.io/raja-9679/traceiq-backend:dev          -f backend/Dockerfile backend
docker build -t ghcr.io/raja-9679/traceiq-frontend:dev         -f frontend/Dockerfile frontend
docker build -t ghcr.io/raja-9679/traceiq-execution-worker:dev -f execution-engine/Dockerfile.worker execution-engine
# a stack directory holding a COPY of infrastructure/docker-compose.community.yml
# and a .env made by infrastructure/traceiq-setup.sh, with TRACEIQ_VERSION=dev
docker compose -f docker-compose.community.yml --env-file .env --profile inbox up -d
```

- Frontend http://localhost:8080 (adjust to your .env), backend health
  `curl localhost:<backend-port>/health/ready`. `/api/health` → 404 is correct.
- **The worker MUST be built from `Dockerfile.worker`** (`node dist/worker.js`);
  `Dockerfile` is the legacy engine and looks healthy while consuming nothing.
- The stack's compose file is a copy and drifts: `diff` it against
  `infrastructure/docker-compose.community.yml` before debugging "missing" settings.
- `--profile inbox` starts Mailpit (test inbox): Settings → Instance → Test inbox,
  provider `mailpit`, API URL `http://mailpit:8025`, any domain such as
  `inbox.traceiq.test`. Its UI is on localhost:8025.
- Rebuild the worker image whenever step types change (workers hard-fail
  unknown steps). Rebuild the backend image when `requirements.txt` changes
  (google-re2 and dnspython were added on 2026-10-15).
- First user: just register. The Tenant Admin of the FIRST tenant is treated
  as the instance admin (`get_current_instance_admin`), so Settings →
  Instance (admin), worker fleet, SIEM etc. work with no SQL. The separate
  `is_instance_admin` flag is only needed as the break-glass password login
  when `PASSWORD_LOGIN_DISABLED` is on (grant it from the instance-admins
  page, not SQL).
- Videos and Playwright traces need capture level `full`: both the instance
  ceiling (`MAX_CAPTURE_LEVEL`, default `standard`) and the project's data
  policy (Quality page → Data policy, default `standard`). Workers always
  record video and discard it at upload when the level is lower — the worker
  log says `video suppressed by capture level '…'`.
- The LLM backend in the office is a shared Ollama server on the LAN; from
  home it won't be reachable — set an Anthropic/OpenAI provider in
  Settings → AI providers, or use the Null provider (heuristics only).

## Testing

- Backend unit: `cd backend && ./run-tests.sh -q`. The test image builds
  itself on first use from THIS checkout's `backend/Dockerfile` +
  `requirements.txt`, tagged `traceiq-backend-test:req-<hash of
  requirements.txt>` (`backend/scripts/test-image.sh`) — the first run on a
  new laptop takes a few minutes, and a dependency change rebuilds it
  automatically. No manual image step, no GHCR pull (anonymous pulls 403).
  `TRACEIQ_TEST_IMAGE=<tag>` still overrides. Baseline: NO failures and no
  errors (`tests/e2e` skips unless `TRACEIQ_E2E_API_URL` points at a running
  API).
- Real Postgres: `./run-tests-live.sh -q` (whole suite incl. integration, ~40
  min) or `./run-tests-live.sh tests/integration/<file>.py -q`. Needs a
  running Postgres container named `traceiq-postgres-1` (the local stack's; or
  set `TRACEIQ_PG_CONTAINER`); it creates and drops its own scratch database.
  Migrations: `./run-tests-live.sh --migrations`. Set
  `TRACEIQ_SCRATCH_DB=<name>` when running more than one at a time.
- RLS: `TRACEIQ_RLS_MODE=enforce ./run-tests-live.sh -q` runs everything with
  row-level security enforcing; refusal asserts use
  `tests/integration/denied.py:DENIED` (403, or 404 under enforce).
- TraceIQ's own browser suite: `e2e/run-e2e.sh --build` — builds `:e2e`
  images, starts an isolated `traceiq-e2e` compose project with no host
  ports, runs Playwright from a container on its network (~3 min after the
  build), tears down. Pins: `@playwright/test` and the
  `mcr.microsoft.com/playwright:v1.61.1-noble` runner image move together.
- Generated API types (redesign branch): `cd frontend && npm run gen:api`
  after a backend API change; CI fails on drift. Needs `openapi-typescript`
  from `npm ci` (it is a pinned devDependency).
- Engine: `cd execution-engine && npm test && npx tsc --noEmit -p .`
- Frontend: `cd frontend && npx tsc --noEmit -p tsconfig.app.json && npm test`
- `backend/.env` must stay mode 644 or `run-tests.sh` fails on `.env`.
- `frontend/dist` may end up root-owned (docker build); `sudo rm -rf frontend/dist`
  if `npm run build` fails on it, or build with `--outDir` elsewhere.
- zsh trap: never name a shell variable `path` — it is tied to `PATH`.

## Traps from the 2026-10 build

- Conflict "keep both" resolutions: the `=======` separator must be matched
  as a WHOLE line — a `# ====…` divider in models.py ends with the same
  characters and splits the file in the wrong place. After any models.py
  merge, compare every class against both parents (an AST class-by-class
  diff caught two dropped HarArchive fields that the unit suite did not).
- A new table also needs an RLS decision (`app/services/rls.py` +
  `tests/test_rls_plan.py`), and any aggregate over runs must exclude
  validation runs (`app/services/run_scope.py`).
- Live tests mount `backend/` — don't edit backend files while a long live run
  is in progress (it imports the tree at start, and a second run picks up
  half-made edits).

- Merging branches that both add redaction twin tests easily loses a closing
  `});` (TS) or `)` where two test blocks meet — always run `tsc`/pytest
  right after resolving.
- Migrations from parallel branches all start from the same head; re-chain
  `down_revision` (and the `Revises:` docstring line) linearly at merge, then
  run the migration check.
- Regexes from users/test authors: backend uses RE2 (`import re2`), worker uses
  `boundedRegexTest` (vm timeout). No syntax filter prevents catastrophic
  backtracking.
- Shared credentials (tracker connections, workspace secrets) need workspace
  membership, not just project access.
- GitHub push protection flags the fake Stripe-style keys in the redaction
  corpora; the owner allowed them once (2026-10-15). If it blocks again, the
  rejection prints an unblock URL — that is the owner's call.

## Other references

- Roadmap status page: https://claude.ai/artifact/LCuvPCFBWW8H1i74CeiS6j (private)
- Demo apps for exercising TraceIQ: TodoLite + `todo-mobile/` (its README
  has the Appium testability rules); Sarvajna (internal, LDAP auth — get a
  fresh token from its UI; never store it in the repo).
