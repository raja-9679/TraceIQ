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
directly from the bundled object store (RustFS).

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
via `.env`, and object-storage (S3) changes need a restart plus matching worker
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
| `postgres` `redis` `object-store` | Storage (`object-store` is RustFS, S3-compatible) | object store 9000 only |

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

### Optional: test inbox (email) and webhook capture

Tests that need to receive an email — signup verification, password reset,
magic links, OTP by email — use `{{inbox.address}}` (each run gets its own
address) and a **Wait For Email** step that extracts the link or code. Tests
for webhook-emitting flows register `{{webhook.url}}` with the app and use a
**Wait For Webhook** step.

**Inbox, self-hosted:** the `inbox` profile adds [Mailpit](https://mailpit.axllent.org),
an SMTP catcher that accepts mail for any address and delivers nothing.

```bash
docker compose -f docker-compose.community.yml --env-file .env --profile inbox up -d mailpit
```

1. Point the **app under test's SMTP** at this host, port 1025 (no TLS, no
   auth), or `mailpit:1025` if the app runs on the compose network.
2. **Settings → Instance (Admin) → Test inbox & webhook capture:** provider
   `mailpit`, Mailpit API URL `http://mailpit:8025`, and a domain for the
   generated addresses (e.g. `inbox.traceiq.test` — never a real one). Save,
   then **Test inbox connection**.
3. The Mailpit UI is on `http://localhost:8025` (bound to localhost; it shows
   every captured email). `MAILPIT_SMTP_PORT` / `MAILPIT_UI_PORT` change the
   host ports. Mailpit keeps at most 5000 messages for one day.

**Inbox, real mailbox:** for apps that can only send to real domains, choose
provider `imap` with a mailbox that supports plus-addressing (Gmail, Microsoft
365, Fastmail). Runs get `qa+<token>@company.com`. IMAPS (TLS) only; use an
app password. The credentials are stored encrypted and only the backend reads
the mailbox — execution workers never hold them, and a run can only read mail
sent to its own addresses.

**Webhook capture** needs no extra service, but the app under test must be
able to **reach TraceIQ**: `{{webhook.url}}` is
`<Public URL for webhook capture>/api/hooks/capture/<token>`, falling back to
`FRONTEND_BASE_URL`. Set the public URL in the same settings group if the app
reaches TraceIQ under a different name than your browser does. Captured
requests are redacted before storage, capped at 256 kB and 50 per URL, and
deleted after 24 hours.

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
everyone out, and losing the Postgres or object-store credentials (or
`STORAGE_SSE_MASTER_KEY`, once encryption is on) loses access to your data.

```bash
# Database
docker compose -f docker-compose.community.yml exec -T postgres \
  pg_dump -U traceiq traceiq | gzip > traceiq-$(date +%F).sql.gz

# Artifacts (traces, videos, screenshots). Stop the store for the copy so
# the snapshot is consistent (runs in progress retry their uploads).
docker compose -f docker-compose.community.yml --env-file .env stop object-store
docker run --rm -v traceiq_object_store_data:/data:ro -v "$PWD":/backup alpine \
  tar czf /backup/traceiq-artifacts-$(date +%F).tar.gz -C /data .
docker compose -f docker-compose.community.yml --env-file .env start object-store
```

For a copy without downtime, replicate the bucket to a second S3 store with
`scripts/migrate_object_store.py` (see "Object storage" below) — it is
idempotent, so a nightly run only copies what is new.

Test a restore before you need one. An untested backup is a guess.
`scripts/restore_drill.sh --dump <file> --artifacts <tar.gz>` does that
without touching the live stack.

---

## Object storage

Run artifacts (traces, videos, screenshots, HAR files, app builds, visual
baselines) live in an S3-compatible object store. The bundled one is
**[RustFS](https://github.com/rustfs/rustfs)** (Apache-2.0), the `object-store`
service. Until October 2026 it was MinIO; MinIO's official images and binary
downloads are no longer published, so new installs could not start.

### What is pinned, and how to mirror it

| | |
|---|---|
| Image | `rustfs/rustfs:1.0.1@sha256:1803faef57627e2d9c2e7d89d655d712ddded5389040054987163043fecb6a3c` |
| Platforms | linux/amd64, linux/arm64 (one index digest for both) |
| Source revision | `6de965ae3c965a78ff819fbcd7acd4aa44177d92` (tag `1.0.1`) |
| Health | `GET /health/live`, `GET /health/ready` (also answers MinIO's `/minio/health/*`) |
| Runs as | uid/gid 10001 |

The same reference is used by every compose file, the Helm chart
(`minio.image`), the all-in-one image (`STORAGE_IMAGE` build arg) and CI. The
digest is what makes it immutable: a tag can be re-pointed, a digest cannot.

**Private registry (office and production installs).** So an install never
depends on a public registry staying up, copy the approved image into your own
registry *by digest* and point TraceIQ at the copy:

```bash
# any of: crane, skopeo, regctl, docker buildx imagetools
docker buildx imagetools create \
  --tag registry.example.internal/mirror/rustfs:1.0.1 \
  rustfs/rustfs:1.0.1@sha256:1803faef57627e2d9c2e7d89d655d712ddded5389040054987163043fecb6a3c
# .env
TRACEIQ_STORAGE_IMAGE=registry.example.internal/mirror/rustfs:1.0.1@sha256:1803faef57627e2d9c2e7d89d655d712ddded5389040054987163043fecb6a3c
```

The digest stays identical through the copy; keep it in the reference. Keep
the **current approved image and at least the previous known-good one** in
the mirror, so a rollback never needs the public registry. Laptops and
evaluation installs need none of this — the default pulls from Docker Hub.

**Provenance.** The upstream images carry BuildKit SLSA provenance
attestations (no cosign signature at the time of writing). Inspect them
before approving a new version:

```bash
docker buildx imagetools inspect rustfs/rustfs:<version>@sha256:<digest> \
  --format '{{json .Provenance}}'   # check the source revision and build args
```

### Settings

The application keeps its historical `MINIO_*` setting names, so existing
`.env` files keep working:

| Variable | Meaning |
|---|---|
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | Root credentials of the bundled store (RustFS reads them as `RUSTFS_ACCESS_KEY` / `RUSTFS_SECRET_KEY`); the apps use the same pair |
| `MINIO_PUBLIC_URL` | Address **browsers** use for presigned artifact links (see below) |
| `MINIO_PORT` | Host port of the store (default 9000) |
| `STORAGE_BIND_ADDRESS` | Interface that port is published on (default `0.0.0.0`; `127.0.0.1` for single-host) |
| `STORAGE_SSE_MASTER_KEY` | Master key for at-rest encryption (base64 of 32 bytes; `traceiq-setup.sh` generates it) |
| `MINIO_SSE_ALGORITHM` | `AES256` to encrypt every object at rest (needs the key above); blank = off |
| `TRACEIQ_STORAGE_IMAGE` | Override the image, e.g. your mirror (keep the digest) |

The bucket (`test-artifacts`) is created by the backend on startup, which
also sets a GET/HEAD CORS rule on it — the trace timeline downloads trace
files from the store with `fetch()`. Data lives in the named volume
`traceiq_object_store_data` (`object_store_data` in the compose file); to use
a host directory instead, mount one at `/data` owned by uid 10001.

**Presigned URLs.** Artifact links are signed for the host in
`MINIO_PUBLIC_URL`, and the store rejects a request whose `Host` differs from
the signed one. A reverse proxy in front of the store must therefore pass the
`Host` header through unchanged, and `MINIO_PUBLIC_URL` must be exactly the
address browsers use. Workers and Appium get separate links signed for the
internal address (`object-store:9000`).

**Encryption at rest.** Set `MINIO_SSE_ALGORITHM=AES256` and make sure
`STORAGE_SSE_MASTER_KEY` is set (new installs have one), then restart. Only
objects written afterwards are encrypted. Losing the key makes those objects
unreadable — it belongs in the same backup as `.env`. Note that the execution
workers' uploads do not request encryption (see "Known limitations").

### Upgrading and rolling back the store

1. Read the RustFS release notes for every version between the current and
   the new one; check open security advisories at
   <https://github.com/rustfs/rustfs/security/advisories>.
2. Back up (see "Backups") — a new version may change its on-disk metadata.
3. Pin the new reference (version **and** digest) in `TRACEIQ_STORAGE_IMAGE`
   (or update the default in the compose file), mirror it if you mirror.
4. `docker compose ... up -d object-store`, wait for `healthy`, then open a
   run's trace and video to confirm reads.
5. Roll back by restoring the previous reference. If the new version already
   wrote data in a newer format, restore the pre-upgrade artifact backup into
   an empty volume with the store stopped.

### Migrating from MinIO (installs created before the switch)

RustFS is **not** pointed at the old MinIO data. After upgrading, the stack
has a new, empty `traceiq_object_store_data` volume; the old
`traceiq_minio_data` volume is left exactly as it was (the compose file no
longer declares it, so `down -v` cannot remove it either). Until objects are
copied, runs recorded before the upgrade show no traces/videos. Copy them over
the S3 API:

```bash
cd /path/to/your/stack        # where docker-compose.community.yml and .env live
DC="docker compose -f docker-compose.community.yml --env-file .env"
# The old MinIO used the same root credentials the new store now uses.
U="$(grep '^MINIO_ROOT_USER=' .env | cut -d= -f2-)"
P="$(grep '^MINIO_ROOT_PASSWORD=' .env | cut -d= -f2-)"

# 0. Back up the old volume first (it is never modified, but back it up anyway).
docker run --rm -v traceiq_minio_data:/data:ro -v "$PWD":/backup alpine \
  tar czf /backup/minio-before-migration-$(date +%F).tar.gz -C /data .

# 1. Start the OLD MinIO next to the new stack, on its old volume. Use the
#    MinIO image this host already has: docker image ls minio/minio
docker run -d --name old-minio --network traceiq_traceiq_internal \
  -e MINIO_ROOT_USER="$U" -e MINIO_ROOT_PASSWORD="$P" \
  -v traceiq_minio_data:/data minio/minio:RELEASE.2025-04-22T22-12-26Z server /data

# 2. Dry run (prints what would be copied), then copy for real.
$DC exec -e SOURCE_ACCESS_KEY="$U" -e SOURCE_SECRET_KEY="$P" backend \
  python scripts/migrate_object_store.py --source-endpoint http://old-minio:9000
$DC exec -e SOURCE_ACCESS_KEY="$U" -e SOURCE_SECRET_KEY="$P" backend \
  python scripts/migrate_object_store.py --source-endpoint http://old-minio:9000 --apply

# 3. Open a few old runs in the UI (trace, video, screenshots), then remove
#    the helper container. The old volume stays.
docker rm -f old-minio
```

The script never writes to the source, skips objects already copied (re-run
it after an interruption), verifies every copy (size, and MD5 against the
source ETag for single-part uploads) and exits non-zero if anything failed.
Keep `traceiq_minio_data` until you have checked the result; deleting it is a
separate, deliberate step. If the old MinIO image is no longer on the host,
any MinIO build of the same or a newer release can read the volume for this
one-off copy.

The all-in-one image keeps old data in `/data/minio` and starts RustFS on
`/data/object-store`; the same script applies (run the old MinIO binary
against `/data/minio` on another port inside the container).

### Using another S3 store (office server, OCI, AWS, Azure)

The application speaks plain S3 (SigV4, path-style for custom endpoints). To
use an external store, remove the `object-store` service, set
`MINIO_ENDPOINT` (with `MINIO_USE_SSL=true` or an `https://` endpoint),
`MINIO_PUBLIC_URL`, the credentials and the bucket, and check it against the
call list below. Only RustFS is tested.

- **RustFS on a separate office/customer server** — same image and settings;
  publish 9000 (behind TLS) and set both endpoints to it.
- **AWS S3** — expected to work as-is (the call list is core S3); not tested.
- **OCI Object Storage** — via its S3 Compatibility API
  (`https://<namespace>.compat.objectstorage.<region>.oraclecloud.com`) with a
  Customer Secret Key. Not tested; its CORS, multi-object delete and
  presigned-URL behaviour must be verified before relying on it.
- **Azure Blob Storage** — has no native S3 API. It needs either an
  S3-compatible gateway in front or a Blob adapter behind
  `app/core/storage.py` (and in `execution-engine/src/core/artifact-store.ts`,
  which uploads directly). Not implemented.

Calls a store must support: HeadBucket, CreateBucket, PutBucketCors, PutObject
and multipart upload, GetObject, HeadObject, CopyObject, ListObjectsV2 (prefix,
delimiter, continuation), DeleteObject, DeleteObjects, presigned GET; and SSE
(`AES256` or `aws:kms`) only if you turn it on.

### Known limitations

- RustFS reached 1.0 in September 2026. It is young; pin, mirror, back up,
  and upgrade deliberately.
- The execution workers upload with the root credentials and without SSE
  headers; only backend-written objects honour `MINIO_SSE_ALGORITHM`.
- The store's web console is disabled; nothing in TraceIQ needs it.

---

## Kubernetes

There is a Helm chart at `deploy/helm/traceiq/` that deploys the same stack
(with optional in-cluster PostgreSQL/Redis/object store for evaluation). Same rule as
here: no default secrets — the install command in its README generates them.

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
not reachable from the browser, or a proxy in front of the store rewrites the
`Host` header (presigned links are signed for that host). Set it to the exact
address the browser uses.

**Old runs show no traces or videos after upgrading from a MinIO install.**
Their objects are still in the old volume; see "Migrating from MinIO".

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
