# TraceIQ Helm chart

Deploys the same stack as `infrastructure/docker-compose.community.yml` onto
Kubernetes: backend API, Celery worker/aggregator/beat, Playwright execution
workers, frontend (nginx, proxies `/api/`), and — for evaluation — single-replica
PostgreSQL, Redis and an S3-compatible object store (RustFS). Images are the
published community images.

## Install

There are **no default secrets** (a default in a published chart is a shared
secret across every deployment). Generate them and pass them in, or manage a
Secret yourself and point `secrets.existingSecret` at it.

```bash
helm install traceiq deploy/helm/traceiq -n traceiq --create-namespace \
  --set secrets.secretKey=$(openssl rand -hex 32) \
  --set secrets.webhookSecret=$(openssl rand -hex 32) \
  --set secrets.postgresPassword=$(openssl rand -hex 24) \
  --set secrets.redisPassword=$(openssl rand -hex 24) \
  --set secrets.minioRootUser=traceiq \
  --set secrets.minioRootPassword=$(openssl rand -hex 24) \
  --set config.frontendBaseUrl=https://traceiq.example.com \
  --set 'config.corsOrigins=["https://traceiq.example.com"]' \
  --set ingress.enabled=true --set ingress.host=traceiq.example.com
```

`helm upgrade` with the same values is safe: the backend pods run
`scripts/bootstrap_db.py` on start behind a Postgres advisory lock, so any
number of replicas can start together and the schema migrates once.

With your own Secret (keys: `SECRET_KEY`, `WEBHOOK_SECRET`, `POSTGRES_PASSWORD`,
`REDIS_PASSWORD`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`; optional
`STORAGE_SSE_MASTER_KEY`, `SECRETS_KEY`, `SECRETS_KEY_PREVIOUS`, `METRICS_TOKEN`, `SENTRY_DSN`,
`OTEL_EXPORTER_OTLP_HEADERS`, `ADMIN_PASSWORD`):

```bash
helm install traceiq deploy/helm/traceiq -n traceiq --set secrets.existingSecret=traceiq-secrets ...
```

Passwords are embedded in connection URLs verbatim — keep them to
`[A-Za-z0-9._~-]`.

## Object storage

The bundled store is RustFS (`minio.image`, default
`rustfs/rustfs:1.0.1@sha256:1803faef57627e2d9c2e7d89d655d712ddded5389040054987163043fecb6a3c`;
mirror it by digest for production). The values keys are still `minio.*` and
`secrets.minioRootUser` / `secrets.minioRootPassword`, so existing values files
work; the server reads that pair as its root credentials. It runs as uid 10001
in the StatefulSet `<release>-object-store`, with readiness/liveness probes on
`/health/ready` and `/health/live`; the web console is disabled.

- **Upgrading an install that ran the bundled MinIO** creates a NEW PVC for
  `<release>-object-store`; the old MinIO PVC is kept and not reused, so runs
  recorded before the upgrade show no artifacts until their objects are
  copied. Copy them with `backend/scripts/migrate_object_store.py` (dry run by
  default, never modifies the source) — see `SELF_HOSTING.md` → "Migrating
  from MinIO".
- `secrets.storageSseMasterKey` (optional, `openssl rand -base64 32`) gives the
  bundled store the master key it needs for SSE-S3 (`AES256`) and turns
  encryption on for new objects. With `secrets.existingSecret`, put
  `STORAGE_SSE_MASTER_KEY` in that Secret and set `minio.encryptAtRest=true`.
  Back the key up with the other secrets: losing it makes encrypted objects
  unreadable. Execution-worker uploads are not encrypted (they send no SSE
  headers).

## Production shape

- `postgresql.enabled=false`, `redis.enabled=false`, `minio.enabled=false` and
  fill the `external` blocks (managed Postgres with `sslmode=require`, Redis
  with TLS, S3 or an S3-compatible store with SSE). Then
  `config.requireTransportSecurity=true` makes the backend refuse to boot on a
  plaintext hop.
- `config.minioPublicUrl` must be a URL the **browser** can reach (presigned
  artifact links point there).
- `executionWorker.autoscaling.enabled=true` scales Playwright workers on CPU;
  each pod gets a memory-backed `/dev/shm` (`executionWorker.shmSize`).
- `keda.enabled=true` (needs the [KEDA](https://keda.sh) operator) scales
  workers on what actually matters — the Redis stream lag of `jobs:pending`
  (undelivered jobs, `keda.lagCount` per replica) — and suppresses the CPU HPA
  so the two never fight. `keda.minReplicas: 0` scales an idle pool to zero.
  Credentials come from the chart Secret via a `TriggerAuthentication`.
- `regionWorkers` adds worker pools pinned to a region (`WORKER_REGION`):
  runs and monitors created with that region go to `jobs:pending:<region>` and
  only those pools claim them. Each entry renders
  `<release>-execution-worker-<name>` and, with KEDA on, its own ScaledObject
  on its own stream:

  ```yaml
  regionWorkers:
    - name: eu
      region: eu-west
      exclusive: true      # default; false also drains the global stream (every tenant's jobs)
      replicas: 2
  ```

  Workspaces may only pin runs to a region an instance admin granted them in
  the `WORKER_REGIONS` instance setting (`eu` = everyone, `eu=12,15` = those
  workspaces). `executionWorker.region` labels the main pool instead. Region workers are
  Node workers; no Celery queue is involved.
- `celeryBeat.redbeat=true` (default) keeps the schedule in Redis with a lock,
  so a rescheduled beat pod resumes cleanly. `/health/beat` on the backend
  reports whether beat is alive.
- `metrics.serviceMonitor.enabled=true` with `secrets.metricsToken` scrapes
  `/metrics` via the Prometheus Operator.
- `config.otelExporterOtlpEndpoint` turns on OpenTelemetry traces;
  `config.logFormat=json` (default) emits one JSON object per log line.
- Ingress: everything routes to the frontend, whose nginx proxies `/api/`
  (SCIM and the SAML ACS included). Raise the body-size limit for mobile app
  uploads (`nginx.ingress.kubernetes.io/proxy-body-size: 200m`).

## Not in the chart

- Mobile testing (Android emulator needs `/dev/kvm` and privileged pods) — run
  a device cloud (`MOBILE_DEVICE_PROVIDER`) via `config.extra` instead.
- The Prometheus/Grafana/Jaeger overlay from `infrastructure/monitoring/` — use
  your cluster's.
- The all-in-one evaluation image.

## Verified

Installed on a kind v1.31 cluster on 2026-09-07 with in-cluster data services:
all ten pods ready, `/health/ready` and `/health/beat` (RedBeat) green, the
frontend proxying `/api/` to the backend, the first-boot admin able to log in,
`X-Request-ID` round-tripping. Three things broke on the way and are fixed in
the chart/images — remember them if you fork it:

- `runAsNonRoot` alone fails with *"image has non-numeric user (traceiq)"*; the
  chart sets `runAsUser/runAsGroup/fsGroup: 10001` and the images now declare
  `USER 10001:10001`.
- nginx's `resolver` does not use the pod's DNS search list and the image used
  to hardcode Docker's `127.0.0.11`; the frontend now reads `/etc/resolv.conf`
  at start and the chart passes the backend's fully qualified service name.
- An **empty** `MINIO_PUBLIC_URL` is an invalid boto3 endpoint and crashed the
  backend at startup; the chart omits the key when blank and the backend falls
  back to the internal endpoint.

## Validate locally

```bash
helm lint deploy/helm/traceiq --set secrets.secretKey=a --set secrets.webhookSecret=b \
  --set secrets.postgresPassword=c --set secrets.redisPassword=d \
  --set secrets.minioRootUser=e --set secrets.minioRootPassword=f
helm template traceiq deploy/helm/traceiq --set ... | kubeconform -strict -summary
```

CI does both on every change (`helm-chart` job).
