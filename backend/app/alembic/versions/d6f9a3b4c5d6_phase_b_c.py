"""phase_b_c

Revision ID: d6f9a3b4c5d6
Revises: c5d8f1a2b3c4
Create Date: 2026-05-13 01:00:00.000000

Phase B / Phase C schema:
- New tables: persona, selectorhealproposal, flakerecord
- New columns on testrun: baseline_run_id, target_url, persona_id
- New columns on testcaseresult: retry_count, confidence, is_flaky

Note: `TestStep.intent` is a Pydantic field stored inside `testcase.steps`
(JSON column); no DDL change required to add it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd6f9a3b4c5d6'
down_revision: Union[str, None] = 'c5d8f1a2b3c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # testrun: comparison + persona columns
    # ------------------------------------------------------------------
    op.add_column('testrun', sa.Column('baseline_run_id', sa.Integer(), nullable=True))
    op.add_column('testrun', sa.Column('target_url', sa.String(), nullable=True))
    op.add_column('testrun', sa.Column('persona_id', sa.Integer(), nullable=True))
    op.create_index('ix_testrun_baseline_run_id', 'testrun', ['baseline_run_id'])

    # ------------------------------------------------------------------
    # testcaseresult: retry + flake fields
    # ------------------------------------------------------------------
    op.add_column('testcaseresult', sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('testcaseresult', sa.Column('confidence', sa.Float(), nullable=True))
    op.add_column('testcaseresult', sa.Column('is_flaky', sa.Boolean(), nullable=False, server_default=sa.false()))

    # ------------------------------------------------------------------
    # persona
    # ------------------------------------------------------------------
    op.create_table(
        'persona',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('workspace_id', sa.Integer(), sa.ForeignKey('workspace.id'), nullable=False),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('project.id'), nullable=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('session_state', sa.JSON(), nullable=True),
        sa.Column('auth_headers', sa.JSON(), nullable=True),
        sa.Column('login_steps', sa.JSON(), nullable=True),
        sa.Column('refresh_after_hours', sa.Integer(), nullable=True, server_default='24'),
        sa.Column('last_refreshed_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_persona_workspace_id', 'persona', ['workspace_id'])

    # ------------------------------------------------------------------
    # selectorhealproposal
    # ------------------------------------------------------------------
    op.create_table(
        'selectorhealproposal',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('test_case_id', sa.Integer(), sa.ForeignKey('testcase.id'), nullable=False),
        sa.Column('step_id', sa.String(), nullable=False),
        sa.Column('old_selector', sa.String(), nullable=True),
        sa.Column('new_selector', sa.String(), nullable=False),
        sa.Column('intent', sa.String(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('rationale', sa.String(), nullable=True),
        sa.Column('source_run_id', sa.Integer(), sa.ForeignKey('testrun.id'), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('decided_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
    )
    op.create_index('ix_selectorhealproposal_test_case_id', 'selectorhealproposal', ['test_case_id'])
    op.create_index('ix_selectorhealproposal_step_id', 'selectorhealproposal', ['step_id'])

    # ------------------------------------------------------------------
    # flakerecord
    # ------------------------------------------------------------------
    op.create_table(
        'flakerecord',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('test_case_id', sa.Integer(), sa.ForeignKey('testcase.id'), nullable=False),
        sa.Column('step_id', sa.String(), nullable=True),
        sa.Column('flake_score', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('is_quarantined', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('first_observed_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('last_observed_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('sample_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_failure_message', sa.String(), nullable=True),
    )
    op.create_index('ix_flakerecord_test_case_id', 'flakerecord', ['test_case_id'])
    op.create_index('ix_flakerecord_is_quarantined', 'flakerecord', ['is_quarantined'])


def downgrade() -> None:
    op.drop_index('ix_flakerecord_is_quarantined', table_name='flakerecord')
    op.drop_index('ix_flakerecord_test_case_id', table_name='flakerecord')
    op.drop_table('flakerecord')

    op.drop_index('ix_selectorhealproposal_step_id', table_name='selectorhealproposal')
    op.drop_index('ix_selectorhealproposal_test_case_id', table_name='selectorhealproposal')
    op.drop_table('selectorhealproposal')

    op.drop_index('ix_persona_workspace_id', table_name='persona')
    op.drop_table('persona')

    op.drop_column('testcaseresult', 'is_flaky')
    op.drop_column('testcaseresult', 'confidence')
    op.drop_column('testcaseresult', 'retry_count')

    op.drop_index('ix_testrun_baseline_run_id', table_name='testrun')
    op.drop_column('testrun', 'persona_id')
    op.drop_column('testrun', 'target_url')
    op.drop_column('testrun', 'baseline_run_id')
