# Database Optimization Guide

## 1. Add Missing Indexes

### Why
The database currently has only primary key indexes. Every `WHERE`, `JOIN`, and `ORDER BY` on foreign key or filter columns (like `project_id`, `status`, `created_at`) triggers a full table scan. As data grows, queries get slower.

### How to Run

**Option A: Using the SQL file**
```bash
psql -U <your_db_user> -d <your_db_name> -f add_indexes.sql
```
<!-- psql -U user -d quality_intelligence -f /home/raja/Documents/repos/public/TraceIQ/info/add_indexes.sql -->

**Option B: Via Docker (if using Docker Compose)**
```bash
docker exec -i <postgres_container_name> psql -U <db_user> -d <db_name> < add_indexes.sql
```

**Option C: Paste directly into pgAdmin / any SQL client**
Open `add_indexes.sql` and run the contents.

### Safety
- Uses `CREATE INDEX IF NOT EXISTS` — safe to run multiple times
- Postgres creates indexes **without blocking reads** (no downtime)
- No code changes needed — indexes are transparent to the application
- Does **not** modify any data

### Verify After Running
```sql
-- Check all custom indexes exist
SELECT indexname, tablename FROM pg_indexes
WHERE schemaname = 'public' AND indexname LIKE 'ix_%'
ORDER BY tablename, indexname;
```

---

## 2. Backend Code Optimizations (Already Applied)

These are code-level fixes already committed to the codebase:

| File | What Changed |
|---|---|
| `backend/app/api/projects.py` | Replaced N+1 per-project role lookup with 3 batched queries |
| `backend/app/api/endpoints/test_runs.py` | Added `defer()` to skip loading massive JSON columns on list views |
| `backend/app/api/endpoints/test_suites.py` | Replaced recursive DB queries with in-memory tree traversal |

These take effect automatically when you deploy the latest backend code.
