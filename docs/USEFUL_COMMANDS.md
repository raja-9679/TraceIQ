# Useful commands

Operator cheat sheet. Add to it as we go — one heading per topic, the command,
and one line on when to use it. Compose commands run from `infrastructure/`.

Pick the compose file for the install you are on:

| Install | Compose file | Env file |
|---------|--------------|----------|
| Local dev | `docker-compose.yml` | `env.local` |
| Production | `docker-compose.prod.yml` | `env.prod` |
| Community (prebuilt images) | `docker-compose.community.yml` | `.env` |

Below, `$C` stands for `docker compose --env-file <env> -f <compose file>`, e.g.

```bash
C="docker compose --env-file env.local -f docker-compose.yml"
```

---

## Execution workers

### How many are running

```bash
$C ps execution-worker
```

The Worker Fleet page (`/fleet`, instance admins) shows the same from the
workers' own heartbeats, plus what each one is doing.

### Add (or remove) workers

```bash
# Set the total number of web workers to 8 (up or down)
$C up -d --no-recreate --scale execution-worker=8 execution-worker
```

- `--scale` sets the **total**, it does not add to it.
- `--no-recreate` leaves the workers that are already running alone, so jobs
  in progress are not killed.
- Each worker needs about 1 CPU and 1.5 GB of memory and runs one job at a
  time.
- Scaling down stops workers mid-job; their jobs are redelivered to another
  worker, but a case can run twice. Scale down when the queue is quiet.
- The number goes back to the file's default (`replicas:` — 4 in dev,
  `EXECUTION_WORKERS` in community, default 2) on the next plain `up -d`.
  To make it stick on the community install, set `EXECUTION_WORKERS=8` in
  `.env`.

Region workers (only runs pinned to that region):

```bash
$C --profile region up -d --scale execution-worker-region=2 execution-worker-region
```

Mobile worker (needs the emulator/Appium profile):

```bash
$C --profile mobile up -d mobile-worker
```

### Kubernetes (Helm)

```bash
# One-off
kubectl scale deployment <release>-execution-worker --replicas=8

# Lasting: set executionWorker.replicas in your values file, then
helm upgrade <release> deploy/helm/traceiq -f <your-values>.yaml
```

If the HPA or KEDA is switched on, it owns the replica count — change
`executionWorker.autoscaling.minReplicas/maxReplicas` instead.

### Restart / rebuild workers

```bash
# Restart all (picks up env changes)
$C restart execution-worker

# Rebuild after a code change (new step types need this)
$C build execution-worker && $C up -d execution-worker
```

### Logs

```bash
$C logs -f --tail=200 execution-worker
```

---

## Job queue (Redis)

Jobs wait in Redis streams: `jobs:pending` (web), `jobs:mobile:pending`
(mobile), `jobs:pending:<region>` (regional). Jobs that failed three claims go
to `jobs:dead-letter`. Workers read with consumer group `execution-workers`.

Open a Redis shell (add `-a "$REDIS_PASSWORD"` on installs that set one):

```bash
$C exec redis redis-cli
```

### How much is waiting

```bash
XLEN jobs:pending
XLEN jobs:mobile:pending
XLEN jobs:dead-letter
XPENDING jobs:pending execution-workers       # claimed but not finished
XINFO GROUPS jobs:pending                     # lag = not yet claimed
```

### Look at waiting jobs

```bash
XRANGE jobs:pending - + COUNT 10
```

Careful: a job's payload carries **resolved project secrets**. Don't paste it
into chat or tickets.

### Dead-letter queue

Use the API rather than raw Redis — replay also clears the retry counter,
without which the job is dead-lettered again on first claim:

```bash
TOKEN=<instance-admin JWT or API key header>
curl -s  -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/admin/dead-letter
curl -s -X POST   -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"replay_all": true}'  http://localhost:8000/api/admin/dead-letter/replay
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"discard_all": true}' http://localhost:8000/api/admin/dead-letter
```

### Clearing the queue

Use **Worker Fleet → Waiting jobs** (or the API below). It cancels each run
and removes its waiting jobs, and the cancels are audited:

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/admin/job-queue
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"run_ids": [123]}'     http://localhost:8000/api/admin/job-queue/clear
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"clear_all": true}'    http://localhost:8000/api/admin/job-queue/clear
```

By hand, as a last resort only: deleting jobs does not tell their runs, which
stay "Running" until the stale-run sweep times them out.

```bash
XTRIM jobs:pending MAXLEN 0
```

---

## Celery

```bash
$C logs -f --tail=200 celery_worker
$C exec celery_worker celery -A app.core.celery_app inspect active
$C exec celery_worker celery -A app.core.celery_app inspect reserved
```

`celery_beat` must be running for runs to finish (it drains `jobs:results`
every 2 s). Check: `curl -s localhost:8000/health/beat`.
