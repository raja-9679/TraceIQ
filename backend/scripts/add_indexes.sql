-- ============================================================
-- TraceIQ: Add Missing Database Indexes
-- ============================================================
-- SAFE TO RUN: These are all CREATE INDEX IF NOT EXISTS.
-- They only speed up reads, they don't change any data or behavior.
-- You can run this on production without downtime (Postgres adds
-- indexes without locking the table for reads).
--
-- To run: psql -U <user> -d <database> -f add_indexes.sql
-- ============================================================

-- 1. testrun: Most queried table in the app
CREATE INDEX IF NOT EXISTS ix_testrun_project_id ON testrun (project_id);
CREATE INDEX IF NOT EXISTS ix_testrun_status ON testrun (status);
CREATE INDEX IF NOT EXISTS ix_testrun_created_at ON testrun (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_testrun_user_id ON testrun (user_id);
CREATE INDEX IF NOT EXISTS ix_testrun_test_suite_id ON testrun (test_suite_id);
CREATE INDEX IF NOT EXISTS ix_testrun_test_case_id ON testrun (test_case_id);

-- 2. testcaseresult: Joined on every run detail view
CREATE INDEX IF NOT EXISTS ix_testcaseresult_test_run_id ON testcaseresult (test_run_id);

-- 3. testsuite: Tree queries + project filtering
CREATE INDEX IF NOT EXISTS ix_testsuite_project_id ON testsuite (project_id);
CREATE INDEX IF NOT EXISTS ix_testsuite_parent_id ON testsuite (parent_id);

-- 4. testcase: Suite children + project access checks
CREATE INDEX IF NOT EXISTS ix_testcase_test_suite_id ON testcase (test_suite_id);
CREATE INDEX IF NOT EXISTS ix_testcase_project_id ON testcase (project_id);

-- 5. auditlog: Always filtered by entity
CREATE INDEX IF NOT EXISTS ix_auditlog_entity ON auditlog (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS ix_auditlog_user_id ON auditlog (user_id);

-- 6. testschedule: Filtered by project
CREATE INDEX IF NOT EXISTS ix_testschedule_project_id ON testschedule (project_id);

-- 7. workspace: Tenant-scoped queries
CREATE INDEX IF NOT EXISTS ix_workspace_tenant_id ON workspace (tenant_id);

-- 8. project: Workspace filtering
CREATE INDEX IF NOT EXISTS ix_project_workspace_id ON project (workspace_id);

-- Done!
SELECT 'All indexes created successfully!' AS result;
