# Database Migration Guide: TraceIQ

This document outlines the standard procedure for managing database schema changes using Alembic, as well as providing raw SQL fallbacks for emergency manual interventions.

## Standard Procedure (Alembic)

When you make changes to `backend/app/models.py`, you must generate and apply a new database migration.

### 1. Generate the Migration Script

Run this from the `/backend` directory to auto-generate a new migration script based on changes in `models.py`:

```bash
cd backend
alembic revision --autogenerate -m "descriptive_name_of_your_change"
```

Review the generated file in `backend/alembic/versions/` to ensure it correctly captures the intended schema modifications.

### 2. Apply the Migration

To apply the migration to your local database or production database, run:

```bash
cd backend
alembic upgrade head
```

### 3. Rollback (If Needed)

If an issue occurs, you can rollback to a previous state:

```bash
# Rollback one step
alembic downgrade -1

# Rollback to specific revision
alembic downgrade <revision_id>
```

---

## 🚨 Emergency Manual Approvals (Raw SQL) 🚨

In emergency situations where Alembic is unavailable or migrations fail, use raw SQL directly against the PostgreSQL instance.

*Note: Ensure you record any manual changes and reconcile them with Alembic later by updating the alembic_version table.*

```bash
docker exec -it <postgres-container> psql -U <user> -d <db_name>
```

### Table definitions from `models.py` for TestSchedule:

```sql
CREATE TABLE testschedule (
    id SERIAL PRIMARY KEY,
    name VARCHAR NOT NULL,
    description VARCHAR,
    project_id INTEGER NOT NULL REFERENCES project(id),
    test_suite_id INTEGER REFERENCES testsuite(id),
    test_case_id INTEGER REFERENCES testcase(id),
    browser VARCHAR NOT NULL DEFAULT 'chromium',
    device VARCHAR,
    cron_expression VARCHAR NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    next_run_at TIMESTAMP WITHOUT TIME ZONE,
    last_run_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
    created_by_id INTEGER REFERENCES users(id),
    updated_by_id INTEGER REFERENCES users(id)
);

-- Indexing for performance
CREATE INDEX ix_testschedule_project_id ON testschedule (project_id);
```
