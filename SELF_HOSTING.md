# Self-hosting TraceIQ

Run TraceIQ on your own server from prebuilt images. No source checkout, no
build step. Use of the Community Edition is governed by
[LICENSE-COMMUNITY.md](./LICENSE-COMMUNITY.md) — free to self-host for your
team; no redistribution or hosting it for others.

Everything below refers to files in `infrastructure/`.

---

## Just evaluating? The all-in-one image

If you only want to *try* TraceIQ, skip everything below and run the single
evaluation container:

```bash
docker run -d --name traceiq --shm-size=1g \
  -p 8080:8080 -p 9000:9000 \
  -v traceiq:/data \
  ghcr.io/raja-9679/traceiq-aio:latest
```

Wait a minute or two for first-boot setup (`docker logs -f traceiq`), then
open <http://localhost:8080>. The first account registered becomes the admin.
Unique secrets are generated on first boot into the `traceiq` volume — there
are no defaults to change and nothing secret baked into the image. Port 9000
must stay published: the browser fetches run artifacts (traces, videos)
directly from the bundled MinIO.

Know its limits — it is an evaluation tier, not the production path:

- one execution worker, chromium only (no firefox/webkit runs)
- everything shares one container and one `/data` volume
- no independent scaling of anything

When you outgrow it, deploy the compose stack below and migrate with a
database dump (see Backups).

---

## Requirements

- Docker Engine 24+ with the Compose plugin
- 4 CPU / 8 GB RAM minimum for a small team. Each browser worker wants roughly
  1 CPU and 1.5 GB under load, so size `EXECUTION_WORKERS` to your hardware.
- Disk: budget generously. Traces, videos, and HAR archives are large — a few
  thousand runs reaches tens of GB. `RUN_RETENTION_DAYS` (default 90) is what
  keeps this bounded.

---

## Quick install (one line)

```bash
curl -fsSL https://raw.githubusercontent.com/raja-9679/TraceIQ/main/install.sh | bash
```

This does everything in the Install section below for you: downloads the
compose files into a `./traceiq` directory, generates unique secrets, pulls
the images, starts the stack, and waits until the backend is ready. Re-running
it later pulls newer images without touching your secrets.

### Bringing your own secrets and settings

Everything — DB password, signing keys, ports — is auto-generated or defaulted,
but all of it can be customized **before the first start**. Configuration is
always injected at runtime; nothing secret is ever baked into an image. Three
ways, combinable:

```bash
# 1. Inline environment variables: anything you set wins over auto-generation
curl -fsSL .../install.sh | POSTGRES_PASSWORD='my-own-pass' FRONTEND_PORT=9090 bash

# 2. Interactive: prompts for each secret/setting, Enter accepts the default
curl -fsSL .../install.sh | bash -s -- --interactive

# 3. Review-first: writes .env, then STOPS so you can edit anything before starting
curl -fsSL .../install.sh | bash -s -- --configure
```

One rule for hand-picked infrastructure passwords (`POSTGRES_PASSWORD`,
`REDIS_PASSWORD`): letters, digits, and `. _ ~ -` only — they are embedded in
connection URLs where other characters cannot be escaped. The setup script
enforces this and tells you when a value is rejected. `SECRET_KEY` and
`WEBHOOK_SECRET` must each be at least 32 characters and differ from each
other, same as the backend enforces at boot.

**Most optional settings can also be changed later from the UI** — as the
first-registered (instance admin) account, open Settings → *Instance (Admin)*:
SMTP, Slack/Teams, the AI provider and its API keys, SSO (OIDC), retention,
and network policy are all editable there, with test buttons for email and the
LLM. Values saved in the UI are stored in the database (secrets encrypted) and
override the environment; a Reset button falls back to the env value. Only the
bootstrap infrastructure (database, Redis, signing keys) must be configured
via `.env`, and storage (MinIO/S3) changes need a restart plus matching worker
environment updates.

The same applies to the all-in-one evaluation image: pass secrets with `-e` on
the **first** `docker run` and they are used instead of generated ones —

```bash
docker run -d --name traceiq --shm-size=1g -p 8080:8080 -p 9000:9000 \
  -v traceiq:/data \
  -e POSTGRES_PASSWORD='my-own-pass' \
  -e SECRET_KEY="$(openssl rand -hex 32)" \
  ghcr.io/raja-9679/traceiq-aio:latest
```

After the first boot the stored values win; a differing `-e` value is warned
about and ignored (rotating the DB password requires the database to agree,
not just the environment).

Prefer to see each step? Do it manually:

## Install

```bash
# Fetch just the two files you need
curl -fsSLO https://raw.githubusercontent.com/raja-9679/TraceIQ/main/infrastructure/docker-compose.community.yml
curl -fsSLO https://raw.githubusercontent.com/raja-9679/TraceIQ/main/infrastructure/env.community.example
curl -fsSLO https://raw.githubusercontent.com/raja-9679/TraceIQ/main/infrastructure/traceiq-setup.sh
chmod +x traceiq-setup.sh

# Generate secrets into .env
./traceiq-setup.sh

# Start
docker compose -f docker-compose.community.yml --env-file .env up -d
```

The backend applies database migrations on first boot, so the initial start
takes a little longer. Follow it:

```bash
docker compose -f docker-compose.community.yml logs -f backend
```

Then open <http://localhost:8080> and register. **The first account registered
becomes the workspace admin**, so do this before exposing the instance.

---

## There are no default passwords

Compose will refuse to start until the required secrets are set, and it names
the missing one:

```
error while interpolating x-backend-env.WEBHOOK_SECRET: required variable
WEBHOOK_SECRET is missing a value: Set WEBHOOK_SECRET in .env
(openssl rand -hex 32) — must differ from SECRET_KEY
```

This is deliberate. A default baked into a published image is the *same* secret
on every deployment — for `SECRET_KEY`, which signs login tokens, that would let
anyone mint a valid session against your instance. `traceiq-setup.sh` generates
all of them; if you write `.env` by hand, use `openssl rand -hex 32` per value
and keep `SECRET_KEY` and `WEBHOOK_SECRET` different so either can be rotated
alone.

The backend independently re-checks at startup and refuses to serve on a short,
low-entropy, or placeholder secret, on `minioadmin` credentials, or on
`BACKEND_CORS_ORIGINS=["*"]`.

---

## Configuration you will probably need to change

### Serving to other machines

Two settings assume `localhost`. If anyone but you uses this instance, both need
your real host or domain:

```bash
MINIO_PUBLIC_URL=http://traceiq.internal:9000
BACKEND_CORS_ORIGINS=["http://traceiq.internal:8080"]
FRONTEND_BASE_URL=http://traceiq.internal:8080
```

`MINIO_PUBLIC_URL` catches people out. Test artifacts are fetched **by the
browser** from presigned URLs, so it must be an address the browser can reach —
not an internal Docker hostname. If traces and videos fail to load while
everything else works, this is why.

The frontend itself never needs a backend URL: the app calls a same-origin
relative `/api`, which its nginx proxies to the backend. That proxy target is
runtime configuration too — if you run the backend somewhere *outside* this
compose stack (split deployment), point the frontend at it with:

```bash
FRONTEND_PROXY_BACKEND_URL=http://backend.internal.example.com:8000
```

### Testing apps on your own network

By default TraceIQ refuses to fetch URLs that resolve to private or loopback
addresses. That blocks the case where a user aims a test at your internal
services and reads the response back.

Most self-hosters do need internal targets, so you will likely set:

```bash
ALLOW_PRIVATE_NETWORK_TARGETS=true
```

Understand what that means: any user of the instance can make the server fetch
any address the server can reach. That is fine for a trusted single team. It is
**not** fine if you let untrusted people register. If you only need a couple of
internal hosts, prefer the narrower form:

```bash
OUTBOUND_ALLOWED_HOSTS=["staging.internal","host.docker.internal"]
```

Cloud instance-metadata addresses (`169.254.169.254`) stay blocked either way.

### TLS

The compose file serves plain HTTP. **Do not expose it to the internet as-is** —
login tokens would cross the network in clear text. Put it behind a reverse
proxy that terminates TLS (Caddy, Traefik, or nginx) pointed at the frontend
port, then update `BACKEND_CORS_ORIGINS`, `FRONTEND_BASE_URL`, and
`MINIO_PUBLIC_URL` to their `https://` forms.

Bundled TLS termination is not shipped yet.

---

## What runs

| Service | Purpose | Host port |
|---|---|---|
| `frontend` | UI; also proxies `/api` to the backend | 8080 |
| `backend` | API; applies migrations at startup | 8000 |
| `execution-worker` | Playwright browsers (scale with `EXECUTION_WORKERS`) | — |
| `celery_worker` | Run orchestration | — |
| `celery_aggregator` | Result aggregation | — |
| `celery_beat` | **Required.** Drains results and finalizes runs | — |
| `postgres` `redis` `minio` | Storage | MinIO 9000 only |

Two things worth knowing. `celery_beat` is not optional — without it, runs
dispatch and execute but never leave `RUNNING`. And Postgres and Redis are
deliberately not published to the host; nothing outside the compose network
needs them.

The backend port is exposed so CI systems and AI agents can call the API
directly with an API key. The UI itself does not need it — it talks to `/api` on
its own origin through the frontend's nginx.

### Optional: mobile app testing (Android)

Native Android journeys (`executor=mobile_appium`) run through an optional
`mobile` profile that adds three services: a bundled Android emulator
(budtmo/docker-android), an Appium server, and a `mobile-worker` consuming the
`jobs:mobile:pending` stream.

```bash
docker compose -f docker-compose.community.yml --env-file .env --profile mobile up -d
```

Requirements and caveats:

- The emulator needs **`/dev/kvm` on the host** (Linux with virtualization
  enabled) and runs privileged. First boot takes a few minutes; watch the
  device screen at `http://localhost:6080` (noVNC, port settable via
  `MOBILE_VNC_PORT`).
- No KVM, or need iOS? Set `MOBILE_DEVICE_PROVIDER` to `browserstack`,
  `saucelabs`, or `lambdatest` (plus credentials) in `.env` — sessions run in
  the device cloud and you can start the profile without the emulator:
  `docker compose ... --profile mobile up -d mobile-worker`.
- Upload app binaries at **Project → App builds** (or
  `POST /api/projects/{id}/app-builds`) and pin a run with `app_build_id`.

---

## Operating it

```bash
# Scale browser workers (also settable via EXECUTION_WORKERS)
docker compose -f docker-compose.community.yml up -d --scale execution-worker=4

# Logs
docker compose -f docker-compose.community.yml logs -f backend
docker compose -f docker-compose.community.yml logs -f execution-worker

# Stop / start
docker compose -f docker-compose.community.yml down
docker compose -f docker-compose.community.yml up -d
```

### Upgrading

Pin `TRACEIQ_VERSION` in `.env` so upgrades are deliberate rather than whatever
`latest` happens to be:

```bash
# Back up first — see below
sed -i 's/^TRACEIQ_VERSION=.*/TRACEIQ_VERSION=0.2.0/' .env
docker compose -f docker-compose.community.yml --env-file .env pull
docker compose -f docker-compose.community.yml --env-file .env up -d
```

Migrations run automatically on backend start. Roll back by setting the previous
version and re-running — but note that a schema migration is not always
reversible, so restore from backup if a rollback misbehaves.

### Backups

Nothing is backed up for you. At minimum, snapshot the database and the
artifact store, and keep `.env` somewhere safe — losing `SECRET_KEY` logs
everyone out, and losing the Postgres or MinIO credentials loses access to your
data.

```bash
# Database
docker compose -f docker-compose.community.yml exec -T postgres \
  pg_dump -U traceiq traceiq | gzip > traceiq-$(date +%F).sql.gz

# Artifacts (traces, videos, screenshots)
docker run --rm -v traceiq_minio_data:/data -v "$PWD":/backup alpine \
  tar czf /backup/traceiq-artifacts-$(date +%F).tar.gz -C /data .
```

Test a restore before you need one. An untested backup is a guess.

---

## Optional features

**AI failure analysis** is off unless you configure a provider. Leave
`LLM_PROVIDER` empty and TraceIQ falls back to heuristic summaries; everything
else works normally. To keep data on your own hardware, point it at Ollama:

```bash
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5-coder:14b
OLLAMA_BASE_URL=http://host.docker.internal:11434
```

**Notifications** (email, Slack, Teams) need `NOTIFICATIONS_ENABLED=true` plus
the relevant channel settings — see `env.community.example`.

**Mobile app testing** needs Appium and an emulator or device cloud, which the
community compose does not include.

---

## Troubleshooting

**Compose exits immediately naming a variable.** A required secret is unset. Run
`./traceiq-setup.sh`, or set the named variable in `.env`.

**Backend logs "Refusing to start — insecure configuration detected".** The
message lists exactly what to fix. It only triggers when
`ENVIRONMENT=production`, which the community compose sets.

**Runs stay in RUNNING forever.** `celery_beat` is not running. Check
`docker compose ps` and its logs.

**Traces and videos don't load, everything else works.** `MINIO_PUBLIC_URL` is
not reachable from the browser. Set it to a host the browser can resolve.

**Live run progress never updates.** WebSocket upgrade is being blocked. If you
added your own reverse proxy in front, it must forward `Upgrade` and
`Connection` headers.

**Tests against an internal URL are refused.** Expected — see
`ALLOW_PRIVATE_NETWORK_TARGETS` above. The error message names the setting.

**Migrations retry in a loop at startup.** The backend retries while Postgres
comes up, then gives up with the underlying error. If it persists, check
`DATABASE_URL` and that the Postgres container is healthy.

---

## Security notes

The distribution is hardened where it can be, but be clear about the boundaries:

- Containers run as an unprivileged user with `no-new-privileges`.
- No default credentials; startup validation refuses weak ones.
- Private-network fetches are denied by default; metadata addresses always.
- **`RAW_PLAYWRIGHT_ENABLED=true` executes user-supplied code inside the worker.**
  Leave it off unless every user is trusted, and never on an internet-facing
  instance.
- TraceIQ has not completed a third-party security audit or SOC 2 examination.
- Treat every user of an instance as able to reach whatever the instance can
  reach. There is no hard tenant isolation at the network layer.

Report security issues privately rather than in a public issue.
