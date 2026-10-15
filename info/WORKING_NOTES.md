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

## Branches (as of 2026-10-15)

| Local | GitHub | Notes |
|---|---|---|
| `feature/enterprise-auth-ai` | `feature/enterprise-auth-ai-2026-10` | functional mainline (rewritten history) |
| `feature/redesign-a` | `feature/redesign-a` | design branch, base merged in |
| `main` | `main` (OLD history) | do not merge; see HANDOFF C5 plan |

On a new laptop: clone, then
`git checkout -b feature/enterprise-auth-ai origin/feature/enterprise-auth-ai-2026-10`
and `git checkout -b feature/redesign-a origin/feature/redesign-a`.
Those two are safe to pull. Do not check out or merge the other origin
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
- First user: register, then grant instance admin
  (`UPDATE users SET is_instance_admin = true WHERE email = '…'`) for the
  admin pages (worker fleet, SIEM, instance settings).
- The LLM backend in the office is a shared Ollama server on the LAN; from
  home it won't be reachable — set an Anthropic/OpenAI provider in
  Settings → AI providers, or use the Null provider (heuristics only).

## Testing

- Backend unit: `cd backend && ./run-tests.sh -q`. It borrows the image
  `traceiq-backend-test:local`; build it FROM the `:dev` backend image:
  `printf 'FROM ghcr.io/raja-9679/traceiq-backend:dev\nRUN pip install --no-cache-dir pytest==8.3.4 pytest-asyncio==0.25.2 pytest-cov==6.0.0\n' | docker build -t traceiq-backend-test:local -f - backend`
  (`:latest` from ghcr 403s). Baseline: 4 errors in `tests/e2e/*` (need a
  live server) are expected.
- Real Postgres: `./run-tests-live.sh tests/integration -q` (needs the stack's
  postgres container running); migrations: `./run-tests-live.sh --migrations`.
  Set `TRACEIQ_SCRATCH_DB=<name>` when running more than one at a time.
- Engine: `cd execution-engine && npm test && npx tsc --noEmit -p .`
- Frontend: `cd frontend && npx tsc --noEmit -p tsconfig.app.json && npm test`
- `backend/.env` must stay mode 644 or `run-tests.sh` fails on `.env`.
- `frontend/dist` may end up root-owned (docker build); `sudo rm -rf frontend/dist`
  if `npm run build` fails on it, or build with `--outDir` elsewhere.
- zsh trap: never name a shell variable `path` — it is tied to `PATH`.

## Traps from the 2026-10 build

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
