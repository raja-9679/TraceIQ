"""tenant isolation in the database: row-level security policies (WP15a, H1)

Creates, per covered table, one policy `traceiq_tenant` and ENABLEs (never
FORCEs) row-level security, plus a NOLOGIN role `<database>_rls` that request
transactions switch into while RLS_MODE=enforce. ENABLE without FORCE leaves
the owner — every existing session: API, Celery, beat, migrations, pg_dump —
exactly as before; only a transaction that `SET LOCAL ROLE`s into the
restricted role is filtered, and even then only once it also set
traceiq.rls_mode = 'enforce'. See app/services/rls.py.

Role creation needs CREATEROLE (or superuser). Without it the role is skipped
with a NOTICE and enforce mode falls back to FORCE ROW LEVEL SECURITY, which
the application applies at runtime (rls.reconcile_sync) only while enforcing.

Roles are cluster-wide, hence the per-database name: two databases in one
cluster (or scratch test databases) never share or fight over one role.

The policy text is FROZEN here on purpose (a migration must not import
application code that changes under it); tests/test_rls_plan.py checks it
against app.services.rls.policy_sql().

Revision ID: e012b3c4d601
Revises: d092b3c4d600
Create Date: 2026-10-20 20:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'e012b3c4d601'
down_revision: Union[str, None] = 'd092b3c4d600'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POLICY_NAME = "traceiq_tenant"

_IDS_FN = """
CREATE OR REPLACE FUNCTION traceiq_rls_ids(setting text) RETURNS integer[]
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(string_to_array(NULLIF(current_setting(setting, true), ''), ',')::integer[],
                    '{}'::integer[])
$$
"""

_ROLE_FN = """
CREATE OR REPLACE FUNCTION traceiq_rls_role() RETURNS name
LANGUAGE sql STABLE AS $$
    SELECT (left(current_database(), 59) || '_rls')::name
$$
"""

_CREATE_ROLE = """
DO $$
DECLARE
    r name := traceiq_rls_role();
    sch name := current_schema();
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
        EXECUTE format('CREATE ROLE %I NOLOGIN NOINHERIT NOBYPASSRLS', r);
    END IF;
    EXECUTE format('GRANT %I TO %I', r, current_user);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', sch, r);
    EXECUTE format('GRANT ALL ON ALL TABLES IN SCHEMA %I TO %I', sch, r);
    EXECUTE format('GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA %I TO %I', sch, r);
    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT ALL ON TABLES TO %I', sch, r);
    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I', sch, r);
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'traceiq rls: cannot create or grant role % (%); enforce mode will use FORCE ROW LEVEL SECURITY', r, SQLERRM;
END $$
"""

_DROP_ROLE = """
DO $$
DECLARE
    r name := traceiq_rls_role();
    sch name := current_schema();
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I REVOKE ALL ON TABLES FROM %I', sch, r);
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I REVOKE ALL ON SEQUENCES FROM %I', sch, r);
        EXECUTE format('DROP OWNED BY %I', r);
        BEGIN
            EXECUTE format('DROP ROLE %I', r);
        EXCEPTION WHEN dependent_objects_still_exist OR insufficient_privilege THEN
            RAISE NOTICE 'traceiq rls: role % kept (%)', r, SQLERRM;
        END;
    END IF;
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'traceiq rls: cannot drop role % (%)', r, SQLERRM;
END $$
"""

# table -> USING / WITH CHECK expression. Frozen copy of rls.policy_sql().
POLICIES = {
    'project': (
        "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' "
        'THEN COALESCE((project.workspace_id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND "
        "(cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0"
        ' OR project.id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.project_ids'))::int[]))), false) ELSE true END"
    ),
    'persona': (
        "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' "
        'THEN COALESCE((persona.workspace_id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND "
        "(cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0"
        ' OR persona.project_id IS NULL OR persona.project_id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.project_ids'))::int[]))), false) ELSE true END"
    ),
    'testsuite': (
        "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' "
        'THEN COALESCE(((testsuite.project_id) IN (SELECT p.id FROM project p '
        'WHERE p.workspace_id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND "
        "(cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0"
        ' OR p.id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END"
    ),
    'testcase': (
        "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' "
        'THEN COALESCE(((COALESCE(testcase.project_id, (SELECT s.project_id FROM '
        'testsuite s WHERE s.id = testcase.test_suite_id))) IN (SELECT p.id FROM '
        'project p WHERE p.workspace_id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND "
        "(cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0"
        ' OR p.id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END"
    ),
    'testrun': (
        "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' "
        'THEN COALESCE(((COALESCE(testrun.project_id, (SELECT s.project_id FROM '
        'testsuite s WHERE s.id = testrun.test_suite_id))) IN (SELECT p.id FROM '
        'project p WHERE p.workspace_id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND "
        "(cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0"
        ' OR p.id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END"
    ),
    'testcaseresult': (
        "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' "
        'THEN COALESCE((((SELECT COALESCE(r.project_id, (SELECT s.project_id FROM'
        ' testsuite s WHERE s.id = r.test_suite_id)) FROM testrun r WHERE r.id = '
        'testcaseresult.test_run_id)) IN (SELECT p.id FROM project p WHERE '
        'p.workspace_id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND "
        "(cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0"
        ' OR p.id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END"
    ),
    'projectsecret': (
        "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' "
        'THEN COALESCE(((projectsecret.project_id) IN (SELECT p.id FROM project p'
        ' WHERE p.workspace_id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND "
        "(cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0"
        ' OR p.id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END"
    ),
    'securityfinding': (
        "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' "
        'THEN COALESCE(((COALESCE(securityfinding.project_id, (SELECT '
        'sc.project_id FROM securityscan sc WHERE sc.id = '
        'securityfinding.scan_id), (SELECT r.project_id FROM testrun r WHERE r.id'
        ' = securityfinding.run_id))) IN (SELECT p.id FROM project p WHERE '
        'p.workspace_id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND "
        "(cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0"
        ' OR p.id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END"
    ),
    'authsession': (
        "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' "
        'THEN COALESCE(((authsession.project_id) IN (SELECT p.id FROM project p '
        'WHERE p.workspace_id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND "
        "(cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0"
        ' OR p.id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END"
    ),
    'mobileappbuild': (
        "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' "
        'THEN COALESCE(((mobileappbuild.project_id) IN (SELECT p.id FROM project '
        'p WHERE p.workspace_id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND "
        "(cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0"
        ' OR p.id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END"
    ),
    'visualcomparison': (
        "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' "
        'THEN COALESCE(((visualcomparison.project_id) IN (SELECT p.id FROM '
        'project p WHERE p.workspace_id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND "
        "(cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0"
        ' OR p.id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END"
    ),
    'visualbaseline': (
        "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' "
        'THEN COALESCE((((SELECT COALESCE(c.project_id, (SELECT s.project_id FROM'
        ' testsuite s WHERE s.id = c.test_suite_id)) FROM testcase c WHERE c.id ='
        ' visualbaseline.test_case_id)) IN (SELECT p.id FROM project p WHERE '
        'p.workspace_id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND "
        "(cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0"
        ' OR p.id = ANY ((SELECT '
        "traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END"
    ),
}


def upgrade() -> None:
    op.execute(_IDS_FN)
    op.execute(_ROLE_FN)
    op.execute(_CREATE_ROLE)
    for table, expr in POLICIES.items():
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY {POLICY_NAME} ON "{table}" FOR ALL '
                   f'USING ({expr}) WITH CHECK ({expr})')


def downgrade() -> None:
    for table in POLICIES:
        op.execute(f'DROP POLICY IF EXISTS {POLICY_NAME} ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    op.execute(_DROP_ROLE)
    op.execute("DROP FUNCTION IF EXISTS traceiq_rls_role()")
    op.execute("DROP FUNCTION IF EXISTS traceiq_rls_ids(text)")
