# Database Migration: Parallel Execution Mode

## Overview
This migration adds the `parallel` value to the `executionmode` PostgreSQL enum type to support parallel test execution.

## Running the Migration

### Option 1: Using Docker (Recommended)
```bash
# From the backend directory
docker compose -f ../infrastructure/docker-compose.yml exec backend python scripts/migrate_parallel_execution.py
```

### Option 2: Local Environment
```bash
# From the backend directory
python scripts/migrate_parallel_execution.py
```

## Post-Migration Steps

**IMPORTANT**: After running the migration, you **must restart** the backend services for them to recognize the new enum value:

```bash
# Restart backend and celery worker
docker compose -f ../infrastructure/docker-compose.yml restart backend celery_worker
```

## Verification

To verify the migration was successful:

```bash
# Connect to PostgreSQL
docker compose -f ../infrastructure/docker-compose.yml exec postgres psql -U user -d quality_intelligence

# Check enum values
SELECT enum_range(NULL::executionmode);

# Expected output: {continuous,separate,parallel}
```

## Rollback

PostgreSQL does not support removing enum values directly. If you need to rollback:
1. Remove references to 'parallel' in your application code
2. Consider creating a new enum type and migrating data if absolutely necessary

## What This Migration Does

- Checks if 'parallel' value already exists (idempotent)
- Adds 'parallel' to the `executionmode` enum type
- Provides clear status messages during execution
- Handles errors gracefully

## Related Changes

This migration is part of the parallel test execution feature implementation. See:
- `backend/app/models.py` - ExecutionMode enum
- `backend/app/worker.py` - Worker task updates
- `execution-engine/src/runner.ts` - Parallel execution logic
