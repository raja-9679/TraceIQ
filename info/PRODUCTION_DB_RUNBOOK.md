# Production DB Runbook

This document lists every database change introduced in this improvement batch,
in the order they must be applied, with verification queries and rollback steps.

---

## Pre-flight checklist

1. Take a full database backup before running **any** migration.
2. Put the application in maintenance mode (or at minimum stop the Celery workers
   so no new test runs are dispatched during schema changes).
3. Run each migration step in a **read replica** or staging environment first.
4. Keep this file open alongside a `psql` session.

---

## Migration 1 — Performance Indexes (a7b3c9d2e1f4)

**File:** `backend/app/alembic/versions/a7b3c9d2e1f4_add_performance_indexes.py`

**What it does:** Creates six B-tree indexes on high-frequency query columns.
All indexes are built with `CREATE INDEX CONCURRENTLY` so **no table lock** is
acquired; the application can continue serving traffic during the build.

### Apply

```bash
cd backend
alembic upgrade a7b3c9d2e1f4
```

Because `CONCURRENTLY` cannot run inside a transaction, the migration commits
before issuing each `CREATE INDEX` statement — this is intentional.

### Verify

```sql
SELECT indexname, tablename
FROM pg_indexes
WHERE indexname IN (
    'ix_testrun_created_at',
    'ix_testrun_project_id',
    'ix_testrun_status',
    'ix_testcase_test_suite_id',
    'ix_testcaseresult_test_run_id',
    'ix_user_email'
);
-- Expect: 6 rows
```

### Rollback

```bash
alembic downgrade 1f266105057e
```

Or manually:
```sql
DROP INDEX CONCURRENTLY IF EXISTS ix_testrun_created_at;
DROP INDEX CONCURRENTLY IF EXISTS ix_testrun_project_id;
DROP INDEX CONCURRENTLY IF EXISTS ix_testrun_status;
DROP INDEX CONCURRENTLY IF EXISTS ix_testcase_test_suite_id;
DROP INDEX CONCURRENTLY IF EXISTS ix_testcaseresult_test_run_id;
DROP INDEX CONCURRENTLY IF EXISTS ix_user_email;
```

---

## Migration 2 — Backfill role_id from string fields (b2c4e6f8a0d1)

**File:** `backend/app/alembic/versions/b2c4e6f8a0d1_backfill_role_id_from_string_fields.py`

**What it does:** Populates `role_id` on `userworkspace`, `userprojectaccess`,
and `teamprojectaccess` rows that still carry only the legacy string role value
(`role` / `access_level`).  **No columns are added or dropped** — this is a
pure data migration.

### Pre-condition check

Verify that the system roles exist before running the migration:
```sql
SELECT id, name FROM role WHERE tenant_id IS NULL ORDER BY name;
-- Must include: Workspace Admin, Workspace Member,
--               Project Admin, Project Editor, Project Viewer, Tenant Admin
```

If any role is missing, run the RBAC initialisation first:
```bash
# Inside the backend container / virtualenv
python -c "
import asyncio
from app.core.database import async_session_factory
from app.core.rbac_init import init_rbac
async def run():
    async with async_session_factory() as s:
        await init_rbac(s)
asyncio.run(run())
"
```

### Apply

```bash
cd backend
alembic upgrade b2c4e6f8a0d1
```

The migration prints per-table update counts so you can confirm how many rows
were backfilled.

### Verify

```sql
-- Should return 0 rows after migration
SELECT COUNT(*) FROM userworkspace      WHERE role_id IS NULL;
SELECT COUNT(*) FROM userprojectaccess  WHERE role_id IS NULL;
SELECT COUNT(*) FROM teamprojectaccess  WHERE role_id IS NULL;
```

### Rollback

The string columns are untouched, so a rollback is a no-op — the application
will still read the legacy string columns as before.

---

## Migration 3 — Drop deprecated string columns (MANUAL — run after verification)

**This migration is NOT yet in the Alembic chain.** Run it only after:
- Migration 2 has been applied successfully.
- All three verification queries above return `0`.
- The new access_service code (using `role_id`) has been deployed and is stable
  in production for at least one week.

### Create the migration file

```bash
cd backend
alembic revision --autogenerate -m "drop_deprecated_role_string_columns"
```

Then replace the generated `upgrade` / `downgrade` bodies with:

```python
def upgrade() -> None:
    op.drop_column('userworkspace',     'role')
    op.drop_column('userprojectaccess', 'access_level')
    op.drop_column('teamprojectaccess', 'access_level')

def downgrade() -> None:
    import sqlalchemy as sa
    op.add_column('userworkspace',
        sa.Column('role', sa.String(), nullable=False, server_default='member'))
    op.add_column('userprojectaccess',
        sa.Column('access_level', sa.String(), nullable=False, server_default='editor'))
    op.add_column('teamprojectaccess',
        sa.Column('access_level', sa.String(), nullable=False, server_default='editor'))
```

Then apply:
```bash
alembic upgrade head
```

---

## Environment variable changes required

The following new environment variables must be set **before** deploying the new
backend image:

| Variable | Required | Default | Notes |
|---|---|---|---|
| `WEBHOOK_SECRET` | No | Falls back to `SECRET_KEY` | Dedicated secret for execution-engine → backend callbacks. Set this to a random 32-byte hex string in production to isolate it from the main JWT secret. |

The following variable must be set in the **execution engine** containers:

| Variable | Required | Default | Notes |
|---|---|---|---|
| `WEBHOOK_SECRET` | Yes (after deploy) | — | Must match the `WEBHOOK_SECRET` set in the backend. The execution engine sends this as `X-TraceIQ-Secret` on all `/webhook` and `/finalize` callbacks. |
| `MAX_JOB_DURATION_MS` | No | `600000` (10 min) | Hard timeout per test job. Increase for very long-running test suites. |
| `AI_MAX_HEALS_PER_RUN` | No | `10` | Maximum OpenAI selector-healing calls per test run. |

---

## Deployment order

1. Apply Migration 1 (indexes) — safe with traffic running.
2. Apply Migration 2 (role_id backfill) — safe with traffic running.
3. Deploy new backend image (includes `access_service.py` changes, `slowapi`
   rate limiting, bulk-delete fix, etc.).
4. Deploy new execution engine image (includes `X-TraceIQ-Secret` header changes,
   per-test network event tagging, artifact cleanup fix).
5. Verify `WEBHOOK_SECRET` is set consistently in both services.
6. Monitor logs for 24 h. If all is well, schedule Migration 3 for a later
   maintenance window.
