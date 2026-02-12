# TraceIQ Distributed Execution Guide

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Components](#components)
4. [Execution Modes](#execution-modes)
5. [Setup & Deployment](#setup--deployment)
6. [Scaling](#scaling)
7. [Configuration](#configuration)
8. [Monitoring](#monitoring)
9. [Troubleshooting](#troubleshooting)
10. [Migration from Legacy](#migration-from-legacy)

---

## Overview

TraceIQ v2 introduces a **distributed execution architecture** that solves the fundamental limitations of running parallel tests in a single process. Instead of running multiple tests concurrently within one execution engine (which causes race conditions, shared memory issues, and OOM crashes), we now dispatch **one test per worker** with complete isolation.

### Key Benefits

| Aspect             | Legacy (v1)                    | Distributed (v2)                 |
| ------------------ | ------------------------------ | -------------------------------- |
| **Isolation**      | Shared memory, race conditions | Complete isolation per test      |
| **Scaling**        | Single process limit           | Horizontal: add more workers     |
| **Memory**         | OOM with many parallel tests   | Each worker has dedicated memory |
| **Failures**       | One crash affects all tests    | Isolated failures, auto-retry    |
| **Artifacts**      | Complex mapping, mixed up      | Clean per-job storage            |
| **Network Events** | Race conditions between tests  | Per-test capture                 |
| **Debugging**      | Mixed logs from all tests      | Clean per-job logs               |

---

## Architecture

### High-Level Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         DISTRIBUTED EXECUTION FLOW                               │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│   ┌──────────┐     ┌──────────────┐     ┌─────────────────────────────────────┐│
│   │ Frontend │────▶│   Backend    │────▶│         Celery Worker               ││
│   │  React   │     │   FastAPI    │     │         (worker.py)                 ││
│   └──────────┘     └──────────────┘     │                                     ││
│                                          │  ┌─────────────────────────────┐   ││
│                                          │  │ if PARALLEL mode:           │   ││
│                                          │  │   dispatch_parallel_jobs()  │   ││
│                                          │  │   → N jobs to Redis Stream  │   ││
│                                          │  │                             │   ││
│                                          │  │ if CONTINUOUS mode:         │   ││
│                                          │  │   dispatch_legacy_execution()│  ││
│                                          │  │   → Single engine request   │   ││
│                                          │  └─────────────────────────────┘   ││
│                                          └─────────────────────────────────────┘│
│                                                         │                        │
│                    ┌────────────────────────────────────┼────────────────────┐  │
│                    │            Redis Streams           │                    │  │
│                    │         (jobs:pending)             │                    │  │
│                    └────────────────────────────────────┼────────────────────┘  │
│                                                         │                        │
│         ┌───────────────────┬───────────────────┬───────┴───────────┐           │
│         │                   │                   │                   │           │
│         ▼                   ▼                   ▼                   ▼           │
│   ┌───────────┐       ┌───────────┐       ┌───────────┐       ┌───────────┐    │
│   │ Worker 1  │       │ Worker 2  │       │ Worker 3  │       │ Worker N  │    │
│   │           │       │           │       │           │       │           │    │
│   │ Playwright│       │ Playwright│       │ Playwright│       │ Playwright│    │
│   │  Browser  │       │  Browser  │       │  Browser  │       │  Browser  │    │
│   │           │       │           │       │           │       │           │    │
│   │ ONE TEST  │       │ ONE TEST  │       │ ONE TEST  │       │ ONE TEST  │    │
│   └─────┬─────┘       └─────┬─────┘       └─────┬─────┘       └─────┬─────┘    │
│         │                   │                   │                   │           │
│         └───────────────────┴───────────────────┴───────────────────┘           │
│                                         │                                        │
│                                         ▼                                        │
│                    ┌────────────────────────────────────────────────────────┐   │
│                    │              Redis Streams (jobs:results)              │   │
│                    └────────────────────────────────────────────────────────┘   │
│                                         │                                        │
│                                         ▼                                        │
│                    ┌────────────────────────────────────────────────────────┐   │
│                    │            Result Aggregator (Celery)                  │   │
│                    │                                                        │   │
│                    │  • Consumes results from stream                        │   │
│                    │  • Creates TestCaseResult records                      │   │
│                    │  • Updates TestRun progress/status                     │   │
│                    │  • Publishes WebSocket updates                         │   │
│                    └────────────────────────────────────────────────────────┘   │
│                                         │                                        │
│                                         ▼                                        │
│                    ┌────────────────────────────────────────────────────────┐   │
│                    │                   PostgreSQL                           │   │
│                    │              (TestRun, TestCaseResult)                 │   │
│                    └────────────────────────────────────────────────────────┘   │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Data Flow

```
1. User triggers test run (POST /api/runs)
           │
           ▼
2. Backend creates TestRun record (status: PENDING)
           │
           ▼
3. Celery task queued (run_test_suite)
           │
           ▼
4. Worker checks execution mode
           │
           ├── SEPARATE ──────────────────────────────────┐
           │                                              │
           │   4a. Creates N jobs in Redis Stream         │
           │       (one per test case)                    │
           │                                              │
           │   4b. Initializes progress tracking          │
           │       HSET runs:{id}:progress total N        │
           │                                              │
           └── CONTINUOUS ─────────────────────────────┐  │
                                                       │  │
               4c. Sends request to legacy             │  │
                   execution-engine                    │  │
                                                       │  │
           ┌───────────────────────────────────────────┘  │
           │                                              │
           ▼                                              ▼
5. Legacy engine runs all               5. Execution Workers claim jobs
   tests sequentially                      (XREADGROUP from stream)
           │                                              │
           │                                              ▼
           │                              6. Each worker:
           │                                 • Launches isolated browser
           │                                 • Executes single test
           │                                 • Uploads artifacts to MinIO
           │                                 • Publishes result to jobs:results
           │                                              │
           │                                              ▼
           │                              7. Result Aggregator:
           │                                 • Consumes from jobs:results
           │                                 • Updates TestCaseResult in DB
           │                                 • Increments progress counters
           │                                 • When all complete → finalizes TestRun
           │                                              │
           └──────────────────────────────────────────────┘
                              │
                              ▼
8. Frontend receives WebSocket update
   UI shows real-time progress
```

---

## Components

### 1. Backend Services

#### worker.py (Celery Task)

**Location:** `backend/app/worker.py`

Routes test runs to appropriate execution strategy:

```python
@celery_app.task(name="app.worker.run_test_suite")
def run_test_suite(run_id: int):
    # ...
    if USE_DISTRIBUTED_EXECUTION and execution_mode == ExecutionMode.PARALLEL:
        dispatch_parallel_jobs(run, cases_to_run, effective_settings, session)
    else:
        dispatch_legacy_execution(run, cases_to_run, effective_settings, execution_mode)
```

#### result_aggregator.py (Celery Task)

**Location:** `backend/app/tasks/result_aggregator.py`

Processes job results and updates database:

- Runs every 2 seconds via Celery Beat
- Consumes from `jobs:results` Redis stream
- Creates/updates `TestCaseResult` records
- Updates `TestRun` progress and final status

#### job_dispatcher.py (Service)

**Location:** `backend/app/services/job_dispatcher.py`

Async service for job queue operations:

- `dispatch_parallel_run()` - Creates N jobs for parallel execution
- `dispatch_continuous_run()` - Creates single job for continuous
- `get_run_progress()` - Gets real-time progress
- `cancel_run()` - Cancels pending jobs

### 2. Execution Engine Services

#### worker.ts (Execution Worker)

**Location:** `execution-engine/src/worker.ts`

Stateless worker that processes ONE job at a time:

```typescript
class ExecutionWorker {
  async processLoop() {
    while (!this.isShuttingDown) {
      const claimed = await this.jobQueue.claimJob();
      if (claimed) {
        const result = await this.executeJob(claimed.job);
        await this.jobQueue.completeJob(claimed.streamId, result);
      }
    }
  }
}
```

Key features:

- Complete browser isolation per test
- Automatic artifact upload to MinIO
- Graceful shutdown handling
- Memory cleanup after N jobs

#### job-queue.ts (Redis Client)

**Location:** `execution-engine/src/core/job-queue.ts`

Redis Streams client for job queue operations:

- Consumer group management
- Job claiming with XREADGROUP
- Abandoned job recovery with XCLAIM
- Result publishing
- Progress tracking

#### server.ts (Legacy Engine)

**Location:** `execution-engine/src/server.ts`

HTTP server for CONTINUOUS mode execution (unchanged from v1).

### 3. Redis Data Structures

```redis
# Pending jobs stream
XADD jobs:pending * job_id {uuid} run_id 123 payload {json}

# Job results stream
XADD jobs:results * job_id {uuid} run_id 123 result {json}

# Run progress tracking (Hash)
HSET runs:123:progress total 10 completed 3 passed 2 failed 1 status running

# Run job mapping (Set)
SADD runs:123:job_ids {job_id_1} {job_id_2} ...

# Consumer group for workers
XGROUP CREATE jobs:pending execution-workers 0 MKSTREAM
```

---

## Execution Modes

### CONTINUOUS Mode (Single Engine)

**When to use:** Tests that share browser state (login → actions → logout)

**Behavior:**

1. Single job containing all test cases
2. Sent to legacy execution-engine
3. Tests run sequentially in shared browser context
4. State preserved between tests

**Benefits:**

- Shared authentication/session
- Lower overhead for related tests
- Maintains test order

### SEPARATE Mode (Distributed Workers)

**When to use:** Independent tests that don't share state

**Behavior:**

1. Creates N jobs (one per test case)
2. Jobs distributed to worker pool via Redis Streams
3. Each worker runs ONE test with isolated browser
4. Results aggregated when all complete

**Benefits:**

- True parallelism across multiple processes
- Complete isolation (no shared memory)
- Horizontal scaling
- Fault tolerance (retry on different worker)

---

## Setup & Deployment

### Prerequisites

- Docker & Docker Compose v2
- 4GB+ RAM for development
- 8GB+ RAM for production

### Development Setup

```bash
# Navigate to infrastructure directory
cd infrastructure

# Start all services with 2 workers (default)
docker compose --env-file env.local -f docker-compose.yml up -d --build

# Watch logs
docker compose --env-file env.local logs -f

# Check worker status
docker compose --env-file env.local ps
```

### Environment Variables

Create `infrastructure/env.local`:

```env
# Security
SECRET_KEY=your-secure-secret-key
ALGORITHM=HS256

# OpenAI (for AI features)
OPENAI_API_KEY=sk-...

# Database
POSTGRES_USER=user
POSTGRES_PASSWORD=secure-password

# MinIO
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=secure-password

# Execution
DEFAULT_TIMEOUT=30000
WORKER_IDLE_TIMEOUT=300000

# Frontend
VITE_API_BASE_URL=http://localhost:8000/api
```

### Production Deployment

```bash
# Use production compose file
docker compose --env-file env.prod -f docker-compose.prod.yml up -d --build

# Scale workers based on load
docker compose --env-file env.prod -f docker-compose.prod.yml up -d --scale execution-worker=10
```

---

## Scaling

### Horizontal Scaling (Workers)

```bash
# Scale up for heavy load
docker compose --env-file env.local up -d --scale execution-worker=10

# Scale down when idle (save resources)
docker compose --env-file env.local up -d --scale execution-worker=2

# Check current scale
docker compose ps | grep execution-worker
```

### Resource Recommendations

| Worker Count | RAM Required | Use Case    |
| ------------ | ------------ | ----------- |
| 2            | 4GB          | Development |
| 5            | 10GB         | Small team  |
| 10           | 20GB         | Medium load |
| 20           | 40GB         | High volume |

### Auto-Scaling (Kubernetes)

For Kubernetes deployments, use HPA based on queue depth:

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: execution-worker-hpa
spec:
  scaleTargetRef:
    kind: Deployment
    name: execution-worker
  minReplicas: 2
  maxReplicas: 20
  metrics:
    - type: External
      external:
        metric:
          name: redis_stream_pending_messages
          selector:
            matchLabels:
              stream: jobs:pending
        target:
          type: AverageValue
          averageValue: 5
```

---

## Configuration

### Worker Configuration

| Variable                  | Default                | Description                |
| ------------------------- | ---------------------- | -------------------------- |
| `REDIS_URL`               | `redis://redis:6379/0` | Redis connection URL       |
| `REDIS_JOBS_STREAM`       | `jobs:pending`         | Stream for pending jobs    |
| `REDIS_RESULTS_STREAM`    | `jobs:results`         | Stream for results         |
| `REDIS_CONSUMER_GROUP`    | `execution-workers`    | Consumer group name        |
| `DEFAULT_TIMEOUT`         | `30000`                | Test timeout (ms)          |
| `WORKER_IDLE_TIMEOUT`     | `300000`               | Shutdown after idle (ms)   |
| `MAX_JOBS_BEFORE_RESTART` | `50`                   | Jobs before memory cleanup |
| `ARTIFACTS_DIR`           | `/tmp/artifacts`       | Local artifacts directory  |

### Backend Configuration

| Variable                    | Default                | Description             |
| --------------------------- | ---------------------- | ----------------------- |
| `USE_DISTRIBUTED_EXECUTION` | `true`                 | Enable new architecture |
| `CELERY_BROKER_URL`         | `redis://redis:6379/0` | Celery broker           |
| `DATABASE_URL`              | -                      | PostgreSQL connection   |

### Feature Flag

To disable distributed execution and use legacy only:

```env
USE_DISTRIBUTED_EXECUTION=false
```

---

## Monitoring

### Queue Metrics

```bash
# Check queue depth
docker compose exec redis redis-cli XLEN jobs:pending

# Check consumer group info
docker compose exec redis redis-cli XINFO GROUPS jobs:pending

# Check pending (processing) jobs
docker compose exec redis redis-cli XPENDING jobs:pending execution-workers

# Check results queue
docker compose exec redis redis-cli XLEN jobs:results
```

### Worker Health

```bash
# Check worker logs
docker compose logs -f execution-worker

# Check worker count
docker compose ps | grep execution-worker | wc -l

# Check memory usage
docker stats --format "table {{.Name}}\t{{.MemUsage}}" | grep execution-worker
```

### Run Progress

```bash
# Check specific run progress
docker compose exec redis redis-cli HGETALL runs:123:progress

# Expected output:
# total 10
# completed 5
# passed 4
# failed 1
# status running
```

### API Endpoints

```bash
# Get queue stats (add to backend)
curl http://localhost:8000/api/admin/queue-stats

# Get run progress
curl http://localhost:8000/api/runs/123
```

---

## Troubleshooting

### Common Issues

#### 1. Jobs Not Being Processed

**Symptoms:** Jobs stay in pending queue, workers not claiming

**Check:**

```bash
# Verify workers are running
docker compose ps | grep execution-worker

# Check worker logs for errors
docker compose logs execution-worker

# Verify consumer group exists
docker compose exec redis redis-cli XINFO GROUPS jobs:pending
```

**Solution:**

```bash
# Restart workers
docker compose restart execution-worker

# Or recreate consumer group
docker compose exec redis redis-cli XGROUP DESTROY jobs:pending execution-workers
docker compose exec redis redis-cli XGROUP CREATE jobs:pending execution-workers 0 MKSTREAM
```

#### 2. Workers Crashing with OOM

**Symptoms:** Workers restart frequently, "Killed" in logs

**Solution:**

```yaml
# Increase memory limit in docker-compose.yml
execution-worker:
  deploy:
    resources:
      limits:
        memory: 4G
  shm_size: "2gb"
```

#### 3. Tests Timing Out

**Symptoms:** Tests marked as failed after timeout

**Solution:**

```env
# Increase timeout in env.local
DEFAULT_TIMEOUT=60000
```

#### 4. Abandoned Jobs Not Being Reclaimed

**Symptoms:** Jobs stuck in pending state

**Check:**

```bash
# View pending entries list
docker compose exec redis redis-cli XPENDING jobs:pending execution-workers - + 10

# Manually claim old jobs
docker compose exec redis redis-cli XCLAIM jobs:pending execution-workers worker-manual 60000 <message-id>
```

#### 5. Results Not Appearing

**Symptoms:** Workers complete but results not in DB

**Check:**

```bash
# Check aggregator logs
docker compose logs celery_aggregator

# Check results stream
docker compose exec redis redis-cli XLEN jobs:results

# Check for processing errors
docker compose exec redis redis-cli XPENDING jobs:results result-processors
```

### Debug Mode

Enable verbose logging:

```yaml
# In docker-compose.yml
execution-worker:
  environment:
    - DEBUG=true
    - LOG_LEVEL=debug

celery_aggregator:
  command: celery -A app.core.celery_app worker --loglevel=debug -Q aggregator-queue
```

### Reset Queue (Development Only)

```bash
# Clear all pending jobs
docker compose exec redis redis-cli DEL jobs:pending

# Clear all results
docker compose exec redis redis-cli DEL jobs:results

# Clear all run progress
docker compose exec redis redis-cli KEYS "runs:*" | xargs docker compose exec redis redis-cli DEL
```

---

## Migration from Legacy

### Phase 1: Parallel Deployment (Current)

Both architectures run simultaneously:

- `PARALLEL` mode → New distributed workers
- `CONTINUOUS` mode → Legacy execution-engine

No changes required to existing tests.

### Phase 2: Gradual Migration

1. Create new test suites with `PARALLEL` mode
2. Monitor performance and reliability
3. Convert suitable `CONTINUOUS` suites to `PARALLEL`

### Phase 3: Legacy Deprecation (Future)

1. Add `CONTINUOUS` support to distributed workers
2. Route all traffic to new architecture
3. Remove legacy execution-engine

### Compatibility Notes

- Existing test runs unaffected
- Historical data preserved
- API unchanged
- Frontend unchanged

---

## Appendix

### Job Payload Structure

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "run_id": 123,
  "test_case_id": 456,
  "test_case": {
    "id": 456,
    "name": "Login Test",
    "steps": [
      { "id": "1", "type": "goto", "value": "https://example.com" },
      {
        "id": "2",
        "type": "fill",
        "selector": "#email",
        "value": "test@example.com"
      },
      { "id": "3", "type": "click", "selector": "#submit" }
    ]
  },
  "browser": "chromium",
  "device": "iPhone 14",
  "settings": {
    "headers": { "Authorization": "Bearer xxx" },
    "params": { "env": "staging" },
    "allowed_domains": ["example.com"],
    "domain_settings": {}
  },
  "created_at": "2026-02-10T10:00:00Z",
  "retry_count": 0
}
```

### Result Payload Structure

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "run_id": 123,
  "test_case_id": 456,
  "test_name": "Login Test",
  "status": "passed",
  "duration_ms": 5432,
  "error": null,
  "artifacts": {
    "video": "runs/123/videos/550e8400.webm",
    "trace": "runs/123/traces/550e8400.zip",
    "screenshots": ["runs/123/screenshots/550e8400-failure.png"]
  },
  "response_data": {
    "status": 200,
    "headers": {},
    "body": "{...}"
  },
  "network_events": [...],
  "completed_at": "2026-02-10T10:00:05Z"
}
```

### Service Dependencies

```
┌─────────────────────────────────────────────────────────────┐
│                    Service Dependency Graph                  │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   postgres ◄───────┬──────────────────────────────┐         │
│                    │                              │         │
│   redis ◄──────────┼────────┬────────┬───────────┤         │
│                    │        │        │           │         │
│   minio ◄──────────┼────────┼────────┼───────────┤         │
│                    │        │        │           │         │
│                    ▼        ▼        ▼           ▼         │
│               backend  celery_  celery_   execution-       │
│                        worker   beat      worker           │
│                    │        │                              │
│                    │        ▼                              │
│                    │   celery_aggregator                   │
│                    │                                       │
│                    ▼                                       │
│               execution-engine (legacy)                    │
│                                                            │
└─────────────────────────────────────────────────────────────┘
```

---

## Support

For issues or questions:

1. Check logs: `docker compose logs -f <service>`
2. Review this guide's troubleshooting section
3. Check Redis queue state
4. File an issue with logs and reproduction steps

---

_Last updated: February 10, 2026_
_Architecture version: 2.0_
