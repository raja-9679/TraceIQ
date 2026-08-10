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

`scripts/bootstrap_db.py` is what the container entrypoint runs. It creates the
schema on an empty database and upgrades an existing one. (It cannot be replaced
by `alembic upgrade head`: the Alembic baseline is an empty stub, so migrations
alone cannot build a schema from scratch. See below.)

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

### Known gap: there is no trustworthy rollback

The Alembic baseline (`1f266105057e`) is an empty `pass` stub. Schema truth lives
in the SQLModel definitions, not in the migration chain, so:

- `alembic upgrade head` against an empty database **fails**.
- **A failed upgrade cannot be rolled back with confidence.** Individual
  migrations have `downgrade()` and are tested, but there is no verified path
  back to an arbitrary earlier revision.

For a change-controlled environment, take a database snapshot before upgrading
and treat restore-from-snapshot as the rollback plan. Writing a real squashed
initial migration is tracked as H3 in `info/REGULATED_READINESS.md` and is the
correct fix.

One trap for anyone writing migrations here: **`bootstrap_db.py` never runs
them.** Any DDL that exists only in a migration — triggers, functions, grants,
RLS policies — is absent on fresh installs, and anything a migration *names* that
metadata also creates (constraint names, index names) will diverge between the
two paths. Both have already bitten once.

## Retention and deletion

See `docs/DATA_RESIDENCY.md`. Everything is off by default; the orphaned-artifact
sweep additionally defaults to report-only.

## Not done: Kubernetes

There is no Helm chart or manifest set — compose only. Tracked as H5. The
container images are ordinary and stateless apart from the object store, so a
chart is mechanical work rather than a design problem, but it does not exist and
this document is not going to imply otherwise.
