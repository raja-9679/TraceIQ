# Community distribution plan — free edition without exposing source

**Status:** proposal — for review (drafted 2026-07-26).
**Goal:** offer TraceIQ free to the community while keeping the source
closed — the backend above all.

**Ground truth:** anything we distribute can eventually be
reverse-engineered; obfuscation raises the cost, hosting is the only
absolute protection.

---

## Option 1 — Hosted free tier (zero exposure) ← recommended start

We run a community instance; users sign up. Source never leaves our
servers.

- Platform is already built for it: multi-tenant workspaces, plan quotas
  (`billing`), per-workspace concurrency caps, rate limiting.
- Users can still test their own machines via the existing **local worker
  bridge** (`worker:local`, API-key polling) — no tunnel, backend stays
  private.
- Cost = infra bill; effort = ops (a `community` plan tier with tight
  quotas), not code.

## Option 2 — Prebuilt Docker images, private repo

Publish compiled images (Docker Hub / GHCR) + a
`docker-compose.community.yml` that pulls them (no `build:` contexts).
Plain images still contain readable `.py` files, so add a protection
layer in the image build:

| Layer | Protection | Effort |
|---|---|---|
| **PyArmor** (bytecode obfuscation + runtime guards) | Good — the pragmatic choice | Low-medium (Dockerfile stage) — needs a paid license (free trial caps file size below models.py) |
| Nuitka / Cython (compile to native) | Strong | High — FastAPI/SQLModel/Celery dynamic imports are fiddly |
| `.pyc`-only distribution | Weak-to-moderate (no working decompiler for 3.11+; disassembly still possible) | Trivial |

Execution engine: esbuild bundle + minify `dist/`. Frontend: already
minified bundles — nothing to do.

**Status 2026-07-29 — implemented:** `.pyc`-only strip is live in
`backend/Dockerfile` and the AIO image (`ARG STRIP_SOURCE=1`; alembic and
scripts stay source). Worker/engine `dist/` is esbuild-minified.
`LICENSE-COMMUNITY.md` added (draft).

**Cython experiment result (2026-07-29): NOT VIABLE for this codebase.**
All 98 modules compile, but pydantic v2 rejects Cython-compiled methods on
model classes (`cyfunction` fails its namespace inspection →
"non-annotated attribute" on every Settings/SQLModel/BaseModel class);
`binding=False` does not change class-body function types. Fixing means
`ignored_types` on every model or excluding models.py + most of api/ —
which guts the benefit. If native compilation is ever revisited, trial
Nuitka (claims full-compat function objects), and remember `app/` and
`app/core/` are namespace packages — name inference breaks on them.

## Option 3 — Hybrid (recommended if self-hosting is demanded)

Backend stays hosted by us forever (never shipped); users self-host only
workers (+ optionally the frontend) pointed at our cloud API. The
worker↔backend contract is already a clean HTTPS/API-key boundary, so
this splits along an existing seam. Backend exposure: zero. Shipped code:
the least sensitive part.

## Required regardless of option

1. **License** — a proprietary "free for community use" EULA or
   BSL-style terms on anything shipped. Legal protection beats technical.
2. **Rotate the credentials in git history** (known issue: committed
   `.env` never scrubbed) before ANY artifact of this repo — including
   images built in public CI — goes public.
3. Decide a versioning/release channel (image tags, changelog) before the
   first community release.

## Proposed sequencing

1. Ship Option 1 (hosted free tier: community plan + signup gating).
2. If self-host demand materializes: Option 2 scaffolding —
   `docker-compose.community.yml`, PyArmor backend image stage,
   build/publish script, LICENSE stub.
3. Option 3 remains the fallback posture if image obfuscation proves
   insufficient.
