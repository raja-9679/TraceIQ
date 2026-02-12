# Parallel Execution Architecture v2

## Overview

This document describes the new distributed architecture for parallel test execution in TraceIQ.

## Design Principles

1. **One Test = One Worker** - Complete isolation between test executions
2. **Stateless Workers** - Any worker can pick up any job
3. **Horizontal Scaling** - Scale workers based on queue depth
4. **Fault Tolerance** - Failed jobs can be retried on different workers
5. **Resource Isolation** - Each test gets dedicated browser context and memory

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                              TraceIQ Execution Architecture                       │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                   │
│  ┌─────────────┐     ┌─────────────┐     ┌────────────────────────────────────┐ │
│  │   Frontend  │────▶│   Backend   │────▶│         Redis Streams              │ │
│  │   (React)   │     │  (FastAPI)  │     │                                    │ │
│  └─────────────┘     └─────────────┘     │  ┌──────────────────────────────┐ │ │
│                             │             │  │  jobs:pending                 │ │ │
│                             │             │  │  - job_id, run_id, test_case  │ │ │
│                             │             │  │  - browser, device, settings  │ │ │
│                             │             │  └──────────────────────────────┘ │ │
│                             │             │                                    │ │
│                             │             │  ┌──────────────────────────────┐ │ │
│                             │             │  │  jobs:results                 │ │ │
│                             │             │  │  - job_id, status, artifacts  │ │ │
│                             │             │  └──────────────────────────────┘ │ │
│                             │             │                                    │ │
│                             │             │  ┌──────────────────────────────┐ │ │
│                             │             │  │  runs:{run_id}:status        │ │ │
│                             │             │  │  - Hash for aggregation      │ │ │
│                             │             │  └──────────────────────────────┘ │ │
│                             │             └────────────────────────────────────┘ │
│                             │                            │                       │
│                             │                            ▼                       │
│                             │             ┌────────────────────────────────────┐ │
│                             │             │      Execution Worker Pool         │ │
│                             │             │                                    │ │
│                             │             │  ┌────────┐ ┌────────┐ ┌────────┐ │ │
│                             │             │  │Worker 1│ │Worker 2│ │Worker N│ │ │
│                             │             │  │        │ │        │ │        │ │ │
│                             │             │  │Playwright│Playwright│Playwright│ │
│                             │             │  │Browser │ │Browser │ │Browser │ │ │
│                             │             │  └────────┘ └────────┘ └────────┘ │ │
│                             │             └────────────────────────────────────┘ │
│                             │                            │                       │
│                             │                            ▼                       │
│                             │             ┌────────────────────────────────────┐ │
│                             │             │           MinIO                     │ │
│                             ▼             │  (Videos, Traces, Screenshots)     │ │
│                      ┌─────────────┐      └────────────────────────────────────┘ │
│                      │ Aggregator  │                     │                       │
│                      │  Service    │◀────────────────────┘                       │
│                      │             │                                             │
│                      │ - Combines  │                                             │
│                      │   results   │                                             │
│                      │ - Updates   │                                             │
│                      │   TestRun   │                                             │
│                      └─────────────┘                                             │
│                                                                                   │
└──────────────────────────────────────────────────────────────────────────────────┘
```

## Components

### 1. Job Dispatcher (Backend)

When a test run is created:

- For `PARALLEL` or `SEPARATE` mode: Create individual jobs per test case
- For `CONTINUOUS` mode: Create single job with all test cases

```python
# Job structure
{
    "job_id": "uuid",
    "run_id": 123,
    "test_case_id": 456,
    "test_case": {
        "id": 456,
        "name": "Login Test",
        "steps": [...]
    },
    "browser": "chromium",
    "device": "iPhone 14",
    "settings": {
        "headers": {},
        "params": {},
        "allowed_domains": []
    },
    "created_at": "2026-02-10T10:00:00Z"
}
```

### 2. Execution Worker

Stateless Node.js process that:

1. Pulls ONE job from Redis stream
2. Launches browser, executes test
3. Uploads artifacts to MinIO
4. Publishes result to results stream
5. Acknowledges job completion

Key characteristics:

- **No shared state** between tests
- **Isolated browser context** per job
- **Clean exit** after each job (optional: reuse for efficiency)

### 3. Result Aggregator

Celery task that:

1. Monitors `jobs:results` stream
2. Groups results by `run_id`
3. When all jobs for a run complete:
   - Aggregates pass/fail counts
   - Updates TestRun record
   - Publishes completion event

### 4. Redis Data Structures

```redis
# Pending jobs stream
XADD jobs:pending * job_id {uuid} run_id 123 payload {json}

# Job results stream
XADD jobs:results * job_id {uuid} run_id 123 result {json}

# Run tracking hash
HSET runs:123:jobs total 5 completed 0 passed 0 failed 0

# Run job mapping (for cleanup)
SADD runs:123:job_ids {job_id_1} {job_id_2} ...

# Worker heartbeat
SETEX worker:{worker_id}:heartbeat 30 "alive"
```

## Execution Modes

### PARALLEL Mode

- Creates N jobs (one per test case)
- All jobs added to queue simultaneously
- Workers process in parallel
- Aggregator waits for all N to complete

### SEPARATE Mode

- Creates N independent TestRuns (one per test case)
- Each TestRun has 1 job
- Results tracked independently

### CONTINUOUS Mode

- Creates 1 job containing all test cases
- Single worker executes sequentially
- Shared browser context within that job

## Scaling Strategy

### Horizontal Scaling

```yaml
# docker-compose.yml
execution-worker:
  deploy:
    replicas: ${WORKER_REPLICAS:-3}
```

### Auto-scaling (Kubernetes)

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
          name: redis_stream_length
          selector:
            matchLabels:
              stream: jobs:pending
        target:
          type: AverageValue
          averageValue: 5 # Scale up when >5 jobs per worker
```

## Failure Handling

### Job Timeout

- Each job has TTL (default: 5 minutes)
- If worker dies, job becomes claimable by another worker
- Redis consumer groups handle this automatically

### Worker Crash Recovery

```
1. Worker claims job (XREADGROUP)
2. Worker crashes mid-execution
3. Job stays in PEL (Pending Entries List)
4. After XCLAIM timeout, another worker can claim it
5. Retry counter incremented
6. After 3 retries, moved to dead-letter queue
```

### Partial Run Completion

- If some jobs fail permanently, run marked as PARTIAL
- Completed results are still available
- Failed jobs logged with error details

## Benefits Over Current Architecture

| Aspect    | Current (v1)                   | New (v2)                 |
| --------- | ------------------------------ | ------------------------ |
| Isolation | Shared memory, race conditions | Complete isolation       |
| Scaling   | Vertical only                  | Horizontal (add workers) |
| Failure   | One failure can affect others  | Isolated failures        |
| Memory    | Single process limit           | Distributed memory       |
| Artifacts | Complex mapping                | Clean per-job storage    |
| Debugging | Mixed logs                     | Per-job logs             |

## Migration Path

1. **Phase 1**: Add new worker alongside existing engine
2. **Phase 2**: Route PARALLEL jobs to new architecture
3. **Phase 3**: Route SEPARATE jobs to new architecture
4. **Phase 4**: Deprecate old execution engine
5. **Phase 5**: Route ALL jobs through new architecture

## Configuration

```env
# Worker configuration
WORKER_CONCURRENCY=1          # Jobs per worker (keep at 1 for isolation)
WORKER_JOB_TIMEOUT=300000     # 5 minutes max per test
WORKER_IDLE_TIMEOUT=60000     # Shutdown after 1 minute idle (for cost savings)

# Queue configuration
REDIS_JOBS_STREAM=jobs:pending
REDIS_RESULTS_STREAM=jobs:results
JOB_MAX_RETRIES=3
JOB_RETRY_DELAY=5000

# Scaling
MIN_WORKERS=2
MAX_WORKERS=20
SCALE_UP_THRESHOLD=10         # Queue depth to trigger scale-up
```
