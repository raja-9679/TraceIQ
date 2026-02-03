# Alembic Migration: Add Parallel Execution Mode

## Migration Details
- **Revision ID**: `5108734f9b51`
- **Migration File**: `alembic/versions/5108734f9b51_add_parallel_to_execution_mode.py`
- **Purpose**: Add `parallel` value to the `executionmode` PostgreSQL enum

## Running the Migration

### Step 1: Update alembic.ini with your database URL

Edit `alembic.ini` and update the database URL (line 63):

```ini
# Change this line:
sqlalchemy.url = driver://user:pass@localhost/dbname

# To your actual database URL, for example:
sqlalchemy.url = postgresql://user:password@localhost:5432/quality_intelligence
```

Or use environment variable override:

```bash
export DATABASE_URL="postgresql://user:password@localhost:5432/quality_intelligence"
```

### Step 2: Run the migration

From the **root directory** of your project:

```bash
# Upgrade to latest
alembic upgrade head

# Or if using Docker:
docker compose -f infrastructure/docker-compose.yml exec backend alembic upgrade head
```

### Step 3: Restart services

**IMPORTANT**: After running the migration, restart your backend services:

```bash
docker compose -f infrastructure/docker-compose.yml restart backend celery_worker
```

## Verification

Check that the migration was applied:

```bash
# Check current revision
alembic current

# Expected output: 5108734f9b51 (head)

# Verify enum values in database
docker compose -f infrastructure/docker-compose.yml exec postgres psql -U user -d quality_intelligence -c "SELECT enum_range(NULL::executionmode);"

# Expected output: {continuous,separate,parallel}
```

## Migration Features

### Idempotent
The migration checks if `parallel` already exists before adding it, so it's safe to run multiple times.

### Downgrade Support
The migration includes a downgrade function, but note:
- PostgreSQL does not support removing enum values directly
- Downgrade will fail if any rows use `execution_mode='parallel'`
- Manual intervention required for true downgrade

## Troubleshooting

### Error: "relation 'test_suite' does not exist"
This means your database schema hasn't been initialized yet. Run:
```bash
# Initialize database schema first
docker compose -f infrastructure/docker-compose.yml exec backend python -c "from app.core.database import init_db; import asyncio; asyncio.run(init_db())"

# Then run migration
alembic upgrade head
```

### Error: "enum label 'parallel' already exists"
The migration has already been applied. This is safe to ignore, or you can check:
```bash
alembic current
```

### Services still don't recognize 'parallel'
Make sure you restarted the backend services after running the migration:
```bash
docker compose -f infrastructure/docker-compose.yml restart backend celery_worker
```

## Alternative: Manual SQL

If you prefer to run the SQL directly:

```sql
-- Connect to your database
psql -U user -d quality_intelligence

-- Add the enum value
ALTER TYPE executionmode ADD VALUE IF NOT EXISTS 'parallel';

-- Verify
SELECT enum_range(NULL::executionmode);
```

## Related Files

- Migration: [5108734f9b51_add_parallel_to_execution_mode.py](file:///home/raja/Documents/repos/TraceIQ/alembic/versions/5108734f9b51_add_parallel_to_execution_mode.py)
- Backend Model: [backend/app/models.py](file:///home/raja/Documents/repos/TraceIQ/backend/app/models.py)
- Worker: [backend/app/worker.py](file:///home/raja/Documents/repos/TraceIQ/backend/app/worker.py)
- Execution Engine: [execution-engine/src/runner.ts](file:///home/raja/Documents/repos/TraceIQ/execution-engine/src/runner.ts)
