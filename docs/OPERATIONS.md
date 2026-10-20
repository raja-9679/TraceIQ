# Running TraceIQ in production

Workstream H. What breaks, how you find out, and what to do about it.

The theme of this document is that most of these failures used to have **no
signal at all**. The pipeline could stop entirely and the first symptom was a
user asking why their run had been "running" for an hour.

## The scheduler is the single point of failure

`celery_beat` drains the `jobs:results` stream every two seconds. Everything
downstream of a test finishing depends on it:

- run finalisation and result aggregation
- scheduled (cron) suites
- retention and audit expiry
- the beat heartbeat itself

**If it stops, runs stay at RUNNING forever and nothing else fires.** There is no
retry, no fallback, and — before workstream H — no alert.

### Knowing (do this first)

Beat dispatches `app.tasks.heartbeat_tasks.beat_heartbeat` every 30 seconds; a
worker executes it and writes a timestamp to Redis with a TTL. Two consequences
worth understanding:

- It proves beat can **dispatch**, not merely that its process exists. A beat
  that is up but cannot reach the broker is just as broken, and that is the
  silent-stall class this catches.
- The TTL means the failure mode is "reports unknown", never "reports healthy
  from a stale key".

Surfaces:

| Where | What |
|---|---|
| `GET /health/beat` | `{state, age_seconds, last_tick, detail}`; 503 when stale or clock-skewed |
| `/metrics` | `traceiq_beat_healthy`, `traceiq_beat_heartbeat_age_seconds` (`-1` = never reported) |

`BEAT_STALE_SECONDS` (default 180 — three missed ticks) sets the threshold.

`/health/beat` is deliberately **not** folded into `/health/ready`: readiness
gates load-balancer rotation, and pulling the API out of service because a
scheduler is down turns a degraded system into an outage.

### Surviving it (redbeat)

Run more than one beat, with a Redis lock deciding which is active:

```yaml
celery_beat:
  command: celery -A app.core.celery_app beat --loglevel=info
  environment:
    CELERY_BEAT_SCHEDULER: redbeat.RedBeatScheduler
    # REDBEAT_REDIS_URL defaults to the broker URL
    # REDBEAT_LOCK_TIMEOUT defaults to 300s
  deploy:
    replicas: 2
```

Opt-in on purpose. Switching it on by default would move every existing
deployment's schedule state from a file to Redis during an upgrade, and a
schedule that silently fails to migrate is worse than the single point of failure
it replaces.

`REDBEAT_LOCK_TIMEOUT` must exceed the longest gap between ticks. Set it too low
and the lock expires mid-cycle, so a second beat starts firing the same schedule
— duplicate finalisation, duplicate notifications.

## Dead-lettered jobs

A job that fails to be claimed three times goes to the `jobs:dead-letter` stream.
Until workstream H2 nothing read that stream: there was **no requeue path at
all**, so a job that hit a transient worker crash three times was gone, and its
run stayed short one result permanently.

```bash
# What is in there (payloads are summarised, not echoed — they contain
# resolved project secrets)
curl -s "$API/admin/dead-letter" -H "Authorization: Bearer $TOKEN"

# Requeue everything once the cause is fixed
curl -s -X POST "$API/admin/dead-letter/replay" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"replay_all": true}'

# Or specific entries
curl -s -X POST "$API/admin/dead-letter/replay" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"entry_ids": ["1723291200000-0"]}'
```

Replay clears the job's retry counter. That is not incidental: the counter is
what dead-lettered it, so a replay that left it in place would be re-killed on
first claim, and the button would look like it worked while changing nothing.

Discarding is a separate endpoint (`DELETE /api/admin/dead-letter`) because "I
have looked at these and they are not coming back" is a different decision from
"try again" — conflating them means an operator clearing a backlog silently
re-runs production traffic.

All three are instance-admin gated: a dead-letter payload is the job as
dispatched, including resolved project secrets.

## Monitoring

`/metrics` was real but nothing scraped it — no scrape config, no alert rules, no
dashboard anywhere in the repo. That is now in `infrastructure/monitoring/`:

```bash
cd infrastructure
# Prometheus authenticates with the same value as METRICS_TOKEN
printf '%s' "$METRICS_TOKEN" > monitoring/metrics_token && chmod 600 monitoring/metrics_token
export GRAFANA_ADMIN_PASSWORD=...
docker compose -f docker-compose.yml -f monitoring/docker-compose.monitoring.yml up -d
```

Prometheus on :9090, Grafana on :3001 with the pipeline dashboard
pre-provisioned. `monitoring/alerts.yml` carries rules for every failure above —
each annotation states the user-visible consequence, because "queue depth is
high" does not tell whoever is paged what to do.

`/metrics` is never anonymous: queue depth and run volume are operational
intelligence about the deployment. Prometheus cannot present a JWT, hence
`METRICS_TOKEN`. A mismatched token returns 401, which on the
`TraceIQBackendDown` alert looks identical to the backend being down — check the
token before assuming the worst.

## Schema upgrades

`scripts/bootstrap_db.py` is what the container entrypoint runs. On an empty or
a current database it is `alembic upgrade head`; it adds the advisory lock below
and the legacy bridge described under "Rollback".

**Concurrent replicas are now serialised by a Postgres advisory lock.**
`RUN_MIGRATIONS` defaults to true and every replica ran this, with nothing
coordinating them — two API containers starting together both executed
`alembic upgrade head` concurrently, which Alembic is not safe under. The lock
blocks rather than skipping: a replica that skipped migrating would start serving
against a schema it has not verified.

Verified by starting three bootstraps simultaneously against one empty database:
one creates the schema, the others wait and then no-op, and the final revision is
consistent.

Set `RUN_MIGRATIONS=false` on replicas anyway — the lock makes concurrency safe,
not free.

### Rollback

Since 2026-09 the chain's root is a real migration
(`e0f1a2b3c4d5_squashed_initial_schema.py`): `alembic upgrade head` builds the
full schema from an empty database, `alembic downgrade <rev>` goes back to any
revision on the live chain, and `alembic downgrade base` leaves a genuinely empty
database. `scripts/verify_migrations.py` proves all three against a real
Postgres on every CI run, including that the schema after `upgrade head` is
byte-for-byte the models' (`alembic check`).

Rollback of a release is therefore `alembic downgrade <previous head>` with the
*previous* image's code — a downgrade is only defined by the revision files that
know about the change. Still snapshot first: downgrades that drop a column drop
its data, and Postgres cannot remove an enum label.

**Databases that predate the squash.** The 49 earlier revisions live in
`backend/app/alembic/versions_legacy/` and are not on Alembic's
`version_locations`. Their head has the same id as the new root, so a database
that had reached it needs nothing. A database stamped at an *earlier* legacy
revision is bridged by `bootstrap_db.py` automatically (it runs the legacy chain
to its head, then continues). Plain `alembic upgrade head` on such a database
fails with "Can't locate revision" — run the bootstrap script instead. To
downgrade *into* legacy history, point a `Config` at that directory (its README
shows how).

One rule for anyone writing migrations here: DDL that is not model metadata —
triggers, functions, grants, RLS policies — must be written into a migration by
hand. Autogenerate cannot see it, `alembic check` cannot miss it, and the old
"attach it to the table's `after_create` event too" workaround is no longer the
path fresh installs take.

## Logs, traces, errors

Three independent, all-optional layers (workstream H4), each a single
environment variable away. Everything lives in `app/core/telemetry.py`; the
API process wires it in `app/main.py`, Celery in `app/core/celery_app.py`.

**Structured logs.** `LOG_FORMAT=json` makes every line one JSON object —
`ts`, `level`, `logger`, `msg`, `service`, plus `request_id`, `trace_id` and
`span_id` when present, any `extra=` fields, and `exc` for tracebacks. uvicorn's
own loggers are folded in, Celery's are folded in through its logging signals,
and `sys.stdout` is wrapped so the ~150 legacy `print()` calls come out as JSON
too (logger `stdout`). The only non-JSON lines left are the two the shell
entrypoint prints before Python starts. `LOG_LEVEL` sets the root level; third-
party chatter (SQLAlchemy, httpx, botocore) stays at WARNING unless DEBUG. The
compose stack defaults to `text` because `docker compose logs` is how a
single-host install is read; the Helm chart defaults to `json`.

**Request ids.** Every API response carries `X-Request-ID` — the caller's if it
sent a clean one (printable, no whitespace, ≤128 chars), a fresh one otherwise.
The same id is on every log line for that request and, when Sentry is on, on
the event as a tag.

**Traces.** Set `OTEL_EXPORTER_OTLP_ENDPOINT` (OTLP over HTTP, e.g.
`http://jaeger:4318`, or a vendor endpoint plus `OTEL_EXPORTER_OTLP_HEADERS`)
and the backend and every Celery process export spans for FastAPI requests,
SQL statements, Redis commands, outbound HTTP, and Celery tasks. Each compose
service and each Helm workload sets its own `OTEL_SERVICE_NAME`, so a trace
shows `traceiq-backend → traceiq-celery-worker` as separate services. The
`/health*` and `/metrics` endpoints are excluded. Sampling is the standard
`OTEL_TRACES_SAMPLER` / `OTEL_TRACES_SAMPLER_ARG`
(`parentbased_traceidratio` + `0.1` under load); `OTEL_SDK_DISABLED=true` is
the kill switch. The monitoring overlay ships Jaeger all-in-one (UI :16686) as
somewhere to look. Verified 2026-09-07 against Jaeger: request, `SELECT`,
`PING` and outbound spans arrive under the configured service name, and the
`trace_id` in the JSON log line matches the trace.

**One run = one trace (WP15b).** Celery is instrumented in the API process
too (the producer side), so `POST /api/runs` → `run_test_suite` is one trace,
and the dispatcher stamps a W3C `traceparent` on every worker job
(`telemetry.stamp_trace`, in `_enqueue_job` and the side-job senders). The
web and mobile workers (`execution-engine/src/core/tracing.ts`) continue it:
one `job <type>` span per job, then one `case` span per case and one
`step <type>` span per step, rebuilt from the result's step evidence after the
job (marked `traceiq.timing=reconstructed`; capped at 100 cases / 300 steps
per job). Same switch as the backend — `OTEL_EXPORTER_OTLP_ENDPOINT` (or
`_TRACES_ENDPOINT` / `OTEL_ENABLED`), `OTEL_SDK_DISABLED=true` wins — and with
it unset the SDK is never loaded. Span attributes are an allow-list: run/job/
case ids, job/step type, status, error kind, counts, durations; never step
values, URLs, selectors, headers, bodies or error text. The worker exports
OTLP/HTTP **JSON** (`/v1/traces`); collectors and Jaeger accept it on the same
port as the backend's protobuf. The community compose and Helm pass the OTEL
variables to the workers; `docker-compose.prod.yml` wires OTEL for no service
— add it to the worker with the backend if you use that file. Results flow
back through `jobs:results` into the aggregator as a separate trace (not
linked). Not verified against a live collector in CI; checked with a local
OTLP receiver.

**Error tracking.** `SENTRY_DSN` turns on Sentry (or a compatible receiver).
PII is off, request bodies are never sent, cookies and the query string are
dropped, and TraceIQ's secret-bearing names (`X-API-Key`, `X-Worker-Secret`,
`X-TraceIQ-Secret`, refresh/access/MFA tokens, SAML private key, SMTP password…)
are added to Sentry's scrubber. Tracing is left to OpenTelemetry (Sentry's
`traces_sample_rate` is 0). Events are tagged with `request_id` and `trace_id`
so an issue, its log lines and its trace can be joined.

## Kubernetes (Helm)

`deploy/helm/traceiq/` (workstream H5) deploys the community stack: backend,
Celery worker/aggregator/beat, execution workers with a memory-backed
`/dev/shm` and optional HPA (or KEDA, below), region worker pools
(`regionWorkers`), frontend + Ingress, and single-replica PostgreSQL/
Redis/object-store (RustFS) StatefulSets for evaluation that you switch off in favour of
managed services for production. Same rule as compose: **no default secrets** —
`required` on every one, plus refusals for `webhookSecret == secretKey` and
`minioRootUser=minioadmin`. `helm lint`, `helm template` and kubeconform run in
CI (`helm-chart` job). The chart README covers install, the production shape,
and what is deliberately not in it (mobile emulator, the monitoring overlay).

## Retention and deletion

See `docs/DATA_RESIDENCY.md`. Everything is off by default; the orphaned-artifact
sweep additionally defaults to report-only.

## Worker fleet (WP8)

Execution workers used to be invisible: the only evidence one existed was a
consumer name in `XINFO GROUPS`, and a worker image older than the backend
showed up as cases failing with "Unknown step type".

Every web worker (`worker.ts`) and mobile worker (`mobile-worker.ts`) now
writes a heartbeat to Redis every 15 s with a 45 s TTL — directly, not through
the API, so "the API is down" never looks like "every worker is dead". Local
(developer) workers report through `POST /api/jobs/heartbeat` with their API
key, namespaced by workspace like their job queue.

```bash
curl -s "$API/admin/workers" -H "Authorization: Bearer $TOKEN"   # instance admin
```

Returns each worker (id = its Redis consumer name, host, version + git sha,
region, consumed streams, capabilities, current job, processed/failed counters,
start time), every job stream with its length, consumer-group **lag**
(dispatched, never delivered) and per-consumer pending counts from `XPENDING`,
the dead-letter depth, and warnings. The admin *Worker fleet* page renders it.

- **lost** — the heartbeat key expired. The worker is listed for ten more
  minutes with its last-seen time instead of silently disappearing. A graceful
  shutdown deletes the key, so a lost worker is one that crashed or was killed.
- **version skew** — the worker's supported step types (it reports its own
  list) lack types the backend's `/api/step-types` catalog has. Cases using
  those steps fail on that worker. Rebuild the image; the page lists the
  missing types. Workers older than WP8 report no list and are shown as
  unknown, not skewed.

Image provenance: build with `--build-arg GIT_SHA=$(git rev-parse HEAD)`
(`release-images.yml` passes it); the worker reports `TRACEIQ_GIT_SHA`.

`/metrics` gained `traceiq_workers_alive{kind,region}`,
`traceiq_queue_lag{stream}` and `traceiq_workers_version_skew`;
`monitoring/alerts.yml` alerts on no live workers while jobs wait, lag high or
growing, and skew, and the Grafana dashboard has panels for the first two.

## Run end states, cancel and time limits (WP15f)

A run ends in one of five states. `passed`, `failed` and `error` are verdicts.
`cancelled` (someone pressed Cancel, `POST /api/runs/{id}/cancel`) and
`timed_out` (the platform gave up) are over **without** a verdict: they are
never counted as a pass or a failure in pass rates, the quality gate or
release readiness — a gate with nothing else to go on reads "insufficient
data".

Where `timed_out` comes from:

| Limit | Default | What happens |
|---|---|---|
| `MAX_JOB_DURATION_MS` (worker config) | 600 000 | The worker reports the job `timed_out`, stops it at its next step boundary, and waits up to `TIMED_OUT_JOB_GRACE_MS` (60 000) for it to wind down before taking more work; past that it closes the browser. |
| `STALE_RUN_INACTIVITY_MINUTES` (backend) | 15 | `check_stale_runs` marks a run with no new results `timed_out`. Results that arrive later still update it. |
| `MAX_RUN_DURATION_HOURS` (backend) | 6 | Hard cap, same outcome. |

A step that times out waiting for the page (`DEFAULT_TIMEOUT`, 30 s) is an
ordinary test **failure**, not `timed_out`.

Every non-passing result also carries `error_kind`: `test`, `setup`,
`infrastructure`, `timeout`, `cancelled` or `not_executed`. A rise in
`infrastructure` means look at the workers, not the application; flake
scoring ignores those results.

Cancellation reaches workers through the Redis key `run:cancelled:<id>`
(72 h). If Redis is down when someone cancels, the run is still cancelled (its
status is final and late results cannot reopen it) — queued jobs just run to
no effect, and the API says `workers_notified: false`.

**Finishing a run fires its side effects exactly once** (notifications,
outbound webhooks, monitor check, paging, scans, triage). Since WP15f the
aggregator does this itself; before, only the legacy `execution-engine`
service did, and deployments without it never sent any of them.

## Legacy execution engine (deprecated)

`execution-engine/src/server.ts` (the Express "execution controller" on port
3000 with `POST /run`) is **deprecated and will be removed one release after
2026-10**. Nothing needs it:

- The backend never calls it. `dispatch_legacy_execution` is only reachable
  through `USE_DISTRIBUTED_EXECUTION`, which was never a real setting — so
  every run, CONTINUOUS mode included, has gone to `execution-worker` for
  several releases (CONTINUOUS = one job per case; a shared-browser job is
  parked in `SCOPE_NOTES.md`). Setting `USE_DISTRIBUTED_EXECUTION=false` does
  nothing; there is no switch back.
- Run completion side effects (notifications, webhooks, monitors, paging,
  scans, triage) no longer depend on its controller calling
  `/api/runs/{id}/finalize` — the aggregator finalizes (WP15f L0).

If you start it anyway it prints a banner at boot and on every `/run`, and
answers with `Deprecation: true`. It does **not** honour the workspace AI
opt-out (`ai_allowed`), the project egress allowlist, or run cancel.

What still starts it: `docker-compose.yml` (local dev), and the legacy
`docker-compose.distributed.yml` / `docker-compose.prod-1.yml` — all three now
only under `--profile legacy`. The community and prod compose files, the AIO
image and the Helm chart never ran it. To remove it from a host that ran it:
`docker compose rm -sf execution-engine`.
## Exclusive environments (WP16c)

Mark an environment **exclusive** (Environments page → Exclusive use) when two
runs against it would interfere — one shared test account, a staging database
a suite resets, a sandbox with one merchant. Dispatch then takes a Redis lease
on the environment (policy rule `environment.exclusive_lock`, stage `admit`):

- Other runs on that environment stay **PENDING** and are retried every 20 s.
  They never fail for waiting. Their run page says *Waiting for environment X,
  held by run #N*; the environment shows *In use by run #N since …*.
- **Several runs from one request queue behind each other.** A browser /
  device / persona / viewport matrix started with one `POST /api/runs` on an
  exclusive environment runs one variant at a time — expect it to take N times
  as long. A run uses one environment, so runs cannot deadlock.
- The lease is released on every terminal status (pass, fail, error, cancel,
  time-out, force-complete) and when dispatch itself fails. Its TTL is 180 s,
  renewed every 30 s by the beat task `refresh-environment-leases` while the
  holder is RUNNING; a crashed worker holds it only until `check_stale_runs`
  times the run out. If beat stops, leases expire on their own.
- **Force release** (editor, audited as `lock_force_released`) drops the lease
  without stopping the holder run — the next waiting run then shares the
  environment with it. Use it for a lease you know is stale.
- Comparison / benchmark runs (`target_url`) do not take the lease: they do not
  use the environment's deployment. Redis errors admit the run (fail open,
  like the concurrency cap).

## Validation runs (WP16c)

A heal or case-edit proposal can be **validated** before it is accepted: the
case runs once with the proposed change applied to that run only (the stored
case, its revisions, git-sync export and repro bundles never see it), under
the failing run's browser, device, environment and persona, with retries and
runtime self-heal switched off. Validation runs are excluded from every
statistic (`app/services/run_scope.py`) and send no notifications, CI
webhooks, pages or monitor checks. Workspaces can require a passing
validation before accept (Proposals → policy); `REQUIRE_REPAIR_VALIDATION` is
the instance floor; a **protected** suite forces it and never auto-applies.
They do count as runs for plan quota and browser-minute cost.

## Outbound webhook deliveries

Every attempt is logged (Webhooks page → a webhook → Recent deliveries, or
`GET /api/workspaces/{ws}/webhooks/{id}/deliveries`) for 30 days. Failed
attempts retry after 1, 5 and 30 minutes; `410 Gone` and URLs refused by the
network policy are not retried. Any logged delivery can be sent again
(Redeliver) — same body and `X-TraceIQ-Delivery` id, freshly signed. URLs are
checked against the network policy before every attempt and redirects are not
followed. Only workspace admins see or manage webhooks.

## DB-driven worker config

Instance settings group **worker** (Settings → Instance (Admin)) holds what a
worker can safely take at runtime: default step timeout, maximum job duration,
runtime self-heal, HAR capture, and the worker-side LLM provider
(`WORKER_LLM_PROVIDER`: `inherit` = use the backend's own default provider,
a provider name = worker-specific model/key/base URL, blank = the worker's env).

Workers fetch `GET /api/internal/worker-config` (shared `X-Worker-Secret`,
optional mTLS below) at start and every 60 s (`WORKER_CONFIG_POLL_MS`). A value
that is not set comes back as `null`, meaning **keep the worker's own env** —
not the backend's. When the backend is unreachable, or older than this
endpoint, the last applied config stays in force (last-known-good, the same
rule as the backend's instance-settings cache); before the first successful
fetch the env is the config. The LLM key is the only secret in the response; it
is sent only to an authenticated worker and logged by neither side.

Env-only, by construction — a worker needs these before it can reach the
backend at all: `REDIS_URL` / `REDIS_*`, `WORKER_WEBHOOK_SECRET` /
`WEBHOOK_SECRET`, `BACKEND_URL`, `WORKER_TLS_*`, `MINIO_*` (credentials and
endpoint), `WORKER_REGION` / `WORKER_REGION_EXCLUSIVE`, `APPIUM_URL` and the
device-cloud credentials, and process-shape settings read once at start
(`MAX_JOBS_BEFORE_RESTART`, `ARTIFACTS_DIR`).

## Regions

A worker started with `WORKER_REGION=eu` consumes `jobs:pending:eu` first and
then helps with the global `jobs:pending`; add `WORKER_REGION_EXCLUSIVE=true`
for a worker that must only ever run its region's jobs (e.g. inside a customer
network). Mobile workers do the same on `jobs:mobile:pending:<region>`. Labels
are 1–32 characters of `[a-z0-9-]`; an invalid one stops the worker at start
rather than letting it consume a stream nothing dispatches to. **No
`WORKER_REGION` behaves exactly as before.**

**Declare every region before anyone can use it.** Instance setting
`WORKER_REGIONS` (Settings → Instance → Execution workers), one per line:
`eu` lets every workspace pin runs to `eu`; `acme-onprem=12,15` lets only
workspaces 12 and 15 do so. A region that is not listed is refused (403) at
run and schedule creation and again at dispatch — a region pool can sit inside
one customer's network, and a free-form label would let any tenant run its
journeys (and its resolved secrets) there. The compose region service and Helm
`regionWorkers` are **exclusive by default** for the same reason; set
`exclusive: false` / `WORKER_REGION_EXCLUSIVE=false` only for a pool that is
plain extra capacity you control. Every worker holds the instance-wide Redis
password and webhook secret, so a worker at a site you do not control is only
appropriate on a single-tenant instance.

Runs opt in with `"region": "eu"` in the `POST /api/runs` body; monitors and
schedules take `regions: ["eu", "us"]` and create one run per region per cycle,
each evaluated, alerted and paged on separately (the monitor's overall state is
down if any region is down; the monitor status API returns each region's
state under `regions`).

Deploying a region pool: compose `--profile region` (`execution-worker-region`
service, in every compose file), Helm `regionWorkers: [{name, region,
exclusive, replicas}]`, the all-in-one image `-e WORKER_REGION=eu`. Region
workers are Node workers, not Celery workers — there is no new queue.

A run pinned to a region with **no** worker there waits. The fleet page warns
("has queued jobs but no live worker in region …") and `TraceIQQueueLagHigh`
fires on that stream. A dead-letter replay puts a job back on the stream it
came from — its region's, or the mobile stream; before WP8 every replay went to
`jobs:pending`, where a web worker cannot run a mobile case.

## Paging: PagerDuty, Opsgenie, maintenance windows

Per project (`/api/projects/{id}/paging-channels`, project admin; the routing
key / API key is encrypted at rest and never returned):

- **PagerDuty** Events API v2 — trigger/resolve with `dedup_key`.
- **Opsgenie** Alerts API (US or EU) — create/close by `alias`.

What pages:

- **Monitors** (`on_monitor`) on the DOWN/RECOVERY transitions that already
  drive Slack/Teams/email. Key `traceiq:monitor:<schedule>[:<region>]`. A
  recovery always resolves, even when `alert_on_recovery` is off — that flag is
  about chat noise; leaving a page open after recovery is wrong.
- **Run failures** (`on_run_failure`, opt-in) — only unattended runs:
  scheduled (non-monitor), `triggered_by=ci` and webhook runs. A human clicking
  Run or an agent iterating on a branch never pages; local-worker runs never
  page. Key `traceiq:run:<project>:<suite>[:<case>][@region]`; the next passing
  run of the same target resolves it.

**Maintenance windows** (`/api/projects/{id}/maintenance-windows`) are one-off
(`starts_at`/`ends_at`) or weekly (`weekdays` 0=Mon…6=Sun, `start_time`/
`end_time` HH:MM in an IANA `timezone`, DST-correct; an end before the start
wraps past midnight). A page inside a window is **suppressed and recorded**
(`GET /api/projects/{id}/paging-events`, outcome `suppressed`) — "why was
nobody paged at 02:00" has an answer. A resolve is only sent for a key whose
last *sent* action was a trigger, so a suppressed outage does not produce a
stray "closed" alert. Vendor errors are recorded as `failed` on the event and
the channel's `last_error`. `POST …/paging-channels/{id}/test` sends a trigger
and an immediate resolve under a one-off key.

## IP allowlists: API keys and SCIM

- `ApiKey.allowed_cidrs` — set at creation or with
  `PUT /api/workspaces/{ws}/api-keys/{id}/allowed-cidrs` (key creator or
  workspace admin; an API key can never change any key's list). Empty = any
  address.
- `SCIM_ALLOWED_CIDRS` (instance settings, group *network*) — the ranges your
  IdP provisions from (Okta and Entra publish theirs).

The client address is the TCP peer unless the peer is in
`TRUSTED_PROXY_CIDRS` — **default empty: no proxy is trusted** and
`X-Forwarded-For` is ignored, because any client can send that header. Behind
the shipped nginx, trust nginx's network and let nginx append the real address:

```nginx
location /api/ {
    proxy_pass http://backend:8000;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

```bash
# e.g. the compose network nginx and backend share
TRUSTED_PROXY_CIDRS='["172.18.0.0/16"]'   # or set it in the admin UI
```

The header is walked right to left, skipping trusted hops, so only the address
your first trusted proxy saw is believed. An unparseable hop matches nothing
(fail closed). Saving an invalid CIDR is refused at the form.

Refusals answer 403 and are audited (`api_key` / `ip_refused`, `scim` /
`ip_refused`, with the client IP) when the credential itself was **valid** —
that is a leaked credential in use. An invalid token from outside the range is
refused without an audit row; on an internet-facing SCIM endpoint it is scanner
noise and would flood an append-only log.

## Optional worker mTLS

The shared worker secret stays required. On top of it, `REQUIRE_WORKER_MTLS`
(instance settings, group *network*) makes `/api/internal/*` demand a client
certificate verified by your reverse proxy. TLS ends at the proxy, so the
backend sees the result as headers — and believes them **only from a trusted
proxy** (`TRUSTED_PROXY_CIDRS`); from anyone else they are attacker-supplied
text. `WORKER_MTLS_ALLOWED_SUBJECTS` (one DN per line) optionally pins which
certificates.

Workers:

```bash
BACKEND_URL=https://traceiq-internal.example.com     # must be https://
WORKER_TLS_CERT_FILE=/certs/worker.crt
WORKER_TLS_KEY_FILE=/certs/worker.key
WORKER_TLS_CA_FILE=/certs/ca.crt                     # CA of the proxy's server cert
```

Every internal call (worker-config, LLM usage, visual baselines, test-inbox /
webhook-capture lookups) goes through one client that presents the
certificate. Setting only one of cert/key is an error, not "no mTLS".

nginx — a separate server for workers, and the public server must strip the
headers so nobody can forge them through it:

```nginx
server {                          # workers only
    listen 8443 ssl;
    ssl_certificate         /etc/nginx/tls/server.crt;
    ssl_certificate_key     /etc/nginx/tls/server.key;
    ssl_client_certificate  /etc/nginx/tls/worker-ca.crt;
    ssl_verify_client       on;
    location /api/internal/ {
        proxy_pass http://backend:8000;
        proxy_set_header X-Client-Verify       $ssl_client_verify;
        proxy_set_header X-Client-Cert-Subject $ssl_client_s_dn;
        proxy_set_header X-Forwarded-For       $proxy_add_x_forwarded_for;
    }
}
server {                          # public
    # ...
    location /api/ {
        proxy_set_header X-Client-Verify "";
        proxy_set_header X-Client-Cert-Subject "";
        proxy_pass http://backend:8000;
    }
}
```

## Worker egress allowlist

`PUT /api/projects/{id}/network-policy` (project admin):
`{"enforce": true, "allowed_hosts": ["shop.example.com"], "allow_private": false}`.
`EGRESS_ALLOWLIST_ENFORCED` (group *worker*) forces enforcement on every
project; `EGRESS_ALLOWED_HOSTS` adds hosts every project may reach (SSO, CDN).
The environment's `base_url` host is always allowed. Entries cover subdomains.

Resolved at dispatch and carried in the job — a worker never decides its own
policy. When enforced the worker aborts any http(s)/ws(s) request to a host
off the list and records it in the run's network events with `blocked: true`
and the reason, so "the page called a host it should not have" is visible
evidence. Rules match net_guard: link-local/metadata, multicast and reserved
addresses are never allowed; private and loopback addresses need
`allow_private`, which only takes effect when the instance allows private
targets (`ALLOW_PRIVATE_NETWORK_TARGETS` is the ceiling) — so an allowlisted
public name that resolves to 10.x is refused.

Limits, so nobody mistakes this for a firewall: Playwright routing does not see
WebSocket upgrades or service-worker fetches, and the browser does its own DNS,
so a rebinding host can differ between the worker's check and the connection.
Network egress rules on the worker hosts remain the real control.

## KEDA (Helm)

`keda.enabled=true` (needs the KEDA operator) renders a `ScaledObject` per
worker Deployment — the main pool on `jobs:pending`, each `regionWorkers` entry
on its own `jobs:pending:<region>` — with the `redis-streams` trigger on the
`execution-workers` group's lag (`keda.lagCount` undelivered jobs per replica),
and a `TriggerAuthentication` reading `REDIS_PASSWORD` from the chart Secret.
The CPU HPA (`executionWorker.autoscaling`) is not rendered while KEDA is on;
two autoscalers on one Deployment fight. `minReplicas: 0` scales a pool to zero
when idle (`activationLagCount` wakes it). Off by default; with it off the
chart renders byte-for-byte what it did before.

## Restore drill

`scripts/restore_drill.sh` restores the newest backup into a throwaway
database and proves it is usable:

```bash
BACKUP_DIR=/srv/backups scripts/restore_drill.sh \
  --artifacts /srv/backups/traceiq-artifacts-2026-10-15.tar.gz
scripts/restore_drill.sh --dump traceiq-2026-10-15.sql.gz --compose   # own postgres
```

It takes the formats SELF_HOSTING.md's backup recipe writes (`.sql.gz`, plus
`.sql` and `pg_dump -Fc` `.dump`), restores into `DRILL_DB` (default
`traceiq_restore_drill`) inside the running postgres container — or, with
`--compose`, into a separate postgres in a throwaway compose project
`traceiq-drill`, which is the mode for production hosts — and then, inside the
backend image:

| check | FAIL when |
|---|---|
| restore | psql/pg_restore errors (missing owner roles are tolerated) |
| schema revision | — (WARN when the backup is behind the code; bootstrap migrates it) |
| bootstrap_db | `scripts/bootstrap_db.py` cannot bring the data to head |
| alembic check | the restored schema drifts from the models |
| row counts / latest run | WARN when there are no users |
| audit append-only trigger | missing |
| audit chain | the hash chain breaks (WARN when old rows predate chaining) |
| artifact snapshot | the object-store snapshot (RustFS, or a pre-switch MinIO one) is unreadable (objects counted by `xl.meta`, system dirs excluded) |

It prints a PASS/FAIL table, drops what it created (`--keep` to inspect) and
exits 1 on any FAIL — schedule it and alert on the exit code. Verified
2026-10-15 against the local stack: a seeded backup passes every check in both
modes; the same dump with one audit row edited fails `audit chain` at
that row.

## Server metrics next to load tests

Set `PROMETHEUS_URL` (and optionally `PROMETHEUS_BEARER_TOKEN`; group
*observability*), then approve queries in `PROMETHEUS_QUERY_TEMPLATES`, one
`name=PromQL` per line, e.g.
`cpu=sum(rate(container_cpu_usage_seconds_total{traceiq_project="$project_id"}[1m]))`.
`$project_id`, `$workspace_id` and `$run_id` are substituted server-side (as
integers), so a template can be label-scoped to the caller. A `load-test` step
names templates in `params.prometheus_queries: [{name, template}]` (max 10, a
bare string works too); opening the result evaluates them as range queries over
the test window next to the k6 numbers
(`GET /api/runs/{id}/results/{result_id}/server-metrics`).

Free-form PromQL written in the step is **refused by default**: Prometheus has
no tenancy, so it would let any member who can author a load test read every
series the server holds. `PROMETHEUS_ALLOW_ADHOC_QUERIES=true` re-enables it —
only on a single-tenant instance, or with `PROMETHEUS_URL` pointing at a
label-enforcing proxy such as prom-label-proxy. Fetched through net_guard with
the Prometheus host as the private-network exemption; redirects are not
followed.
