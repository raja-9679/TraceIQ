# TraceIQ Helm chart

Deploys the same stack as `infrastructure/docker-compose.community.yml` onto
Kubernetes: backend API, Celery worker/aggregator/beat, Playwright execution
workers, frontend (nginx, proxies `/api/`), and — for evaluation — single-replica
PostgreSQL, Redis and MinIO. Images are the published community images.

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
`SECRETS_KEY`, `SECRETS_KEY_PREVIOUS`, `METRICS_TOKEN`, `SENTRY_DSN`,
`OTEL_EXPORTER_OTLP_HEADERS`, `ADMIN_PASSWORD`):

```bash
helm install traceiq deploy/helm/traceiq -n traceiq --set secrets.existingSecret=traceiq-secrets ...
```

Passwords are embedded in connection URLs verbatim — keep them to
`[A-Za-z0-9._~-]`.

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

## Validate locally

```bash
helm lint deploy/helm/traceiq --set secrets.secretKey=a --set secrets.webhookSecret=b \
  --set secrets.postgresPassword=c --set secrets.redisPassword=d \
  --set secrets.minioRootUser=e --set secrets.minioRootPassword=f
helm template traceiq deploy/helm/traceiq --set ... | kubeconform -strict -summary
```

CI does both on every change (`helm-chart` job).
