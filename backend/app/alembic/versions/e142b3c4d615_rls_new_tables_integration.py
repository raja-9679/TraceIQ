"""row-level security for tables added alongside WP15a (integration)

WP15d (accessibilitycheck, accessibilityviolation, accessibilitywaiver,
hararchive), WP16b (projectapispecversion, caseownernotice) and WP16c
(repairoutcome) were built in parallel with WP15a, so its migration does not
cover them. Same policy shape as e012b3c4d601 (project-keyed, restricts only
when the transaction says 'enforce'); the role and helper functions already
exist. The text below is frozen; tests/test_rls_plan.py checks it against
rls.policy_sql().

Revision ID: e142b3c4d615
Revises: e012b3c4d601
Create Date: 2026-10-21 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'e142b3c4d615'
down_revision: Union[str, None] = 'e012b3c4d601'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POLICY_NAME = "traceiq_tenant"

POLICIES = {
    'accessibilitycheck': "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' THEN COALESCE(((accessibilitycheck.project_id) IN (SELECT p.id FROM project p WHERE p.workspace_id = ANY ((SELECT traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND (cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0 OR p.id = ANY ((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END",
    'accessibilityviolation': "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' THEN COALESCE(((accessibilityviolation.project_id) IN (SELECT p.id FROM project p WHERE p.workspace_id = ANY ((SELECT traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND (cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0 OR p.id = ANY ((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END",
    'accessibilitywaiver': "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' THEN COALESCE(((accessibilitywaiver.project_id) IN (SELECT p.id FROM project p WHERE p.workspace_id = ANY ((SELECT traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND (cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0 OR p.id = ANY ((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END",
    'hararchive': "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' THEN COALESCE(((hararchive.project_id) IN (SELECT p.id FROM project p WHERE p.workspace_id = ANY ((SELECT traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND (cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0 OR p.id = ANY ((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END",
    'projectapispecversion': "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' THEN COALESCE(((projectapispecversion.project_id) IN (SELECT p.id FROM project p WHERE p.workspace_id = ANY ((SELECT traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND (cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0 OR p.id = ANY ((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END",
    'caseownernotice': "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' THEN COALESCE(((caseownernotice.project_id) IN (SELECT p.id FROM project p WHERE p.workspace_id = ANY ((SELECT traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND (cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0 OR p.id = ANY ((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END",
    'repairoutcome': "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' THEN COALESCE(((repairoutcome.project_id) IN (SELECT p.id FROM project p WHERE p.workspace_id = ANY ((SELECT traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND (cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0 OR p.id = ANY ((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END",
}


def upgrade() -> None:
    for table, expr in POLICIES.items():
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY {POLICY_NAME} ON "{table}" FOR ALL '
                   f'USING ({expr}) WITH CHECK ({expr})')


def downgrade() -> None:
    for table in POLICIES:
        op.execute(f'DROP POLICY IF EXISTS {POLICY_NAME} ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
