"""phase_d

Revision ID: e7a1b2c3d4e5
Revises: d6f9a3b4c5d6
Create Date: 2026-05-13 02:00:00.000000

Phase D — Agent owns the test suite:
- testcase: code_paths (JSON), is_ai_authored, ai_confidence,
  last_human_reviewed_at, last_human_reviewed_by_id
- workspace: ai_generation_limit_daily (default 100)
- new table: caseproposal
- new enum: caseproposalaction
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects import postgresql
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a1b2c3d4e5'
down_revision: Union[str, None] = 'd6f9a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CASE_PROPOSAL_ACTIONS = ('create', 'update', 'delete', 'move')


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # enum: caseproposalaction
    # ------------------------------------------------------------------
    # Idempotent type creation. We bind a postgresql.ENUM with create_type=False
    # to the column below so SQLAlchemy doesn't try to create it again.
    bind.execute(sa.text(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'caseproposalaction') THEN "
        "CREATE TYPE caseproposalaction AS ENUM ('create', 'update', 'delete', 'move'); "
        "END IF; END $$;"
    ))

    action_type = postgresql.ENUM(
        *CASE_PROPOSAL_ACTIONS,
        name='caseproposalaction',
        create_type=False,
    )

    # ------------------------------------------------------------------
    # testcase: agent-ownership metadata
    # ------------------------------------------------------------------
    op.add_column('testcase', sa.Column('code_paths', sa.JSON(), nullable=True))
    op.add_column('testcase', sa.Column(
        'is_ai_authored', sa.Boolean(), nullable=False, server_default=sa.false(),
    ))
    op.add_column('testcase', sa.Column('ai_confidence', sa.Float(), nullable=True))
    op.add_column('testcase', sa.Column('last_human_reviewed_at', sa.DateTime(), nullable=True))
    op.add_column(
        'testcase',
        sa.Column('last_human_reviewed_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
    )
    op.create_index('ix_testcase_is_ai_authored', 'testcase', ['is_ai_authored'])

    # ------------------------------------------------------------------
    # workspace: daily AI-generation budget
    # ------------------------------------------------------------------
    op.add_column('workspace', sa.Column(
        'ai_generation_limit_daily', sa.Integer(), nullable=False, server_default='100',
    ))

    # ------------------------------------------------------------------
    # caseproposal
    # ------------------------------------------------------------------
    op.create_table(
        'caseproposal',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('project.id'), nullable=False),
        sa.Column('test_suite_id', sa.Integer(), sa.ForeignKey('testsuite.id'), nullable=True),
        sa.Column('target_case_id', sa.Integer(), sa.ForeignKey('testcase.id'), nullable=True),
        sa.Column('action', action_type, nullable=False),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('rationale', sa.String(), nullable=True),
        sa.Column('ai_confidence', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('agent_id', sa.String(), nullable=True),
        sa.Column('source_run_id', sa.Integer(), sa.ForeignKey('testrun.id'), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('decided_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('decision_note', sa.String(), nullable=True),
    )
    op.create_index('ix_caseproposal_project_id', 'caseproposal', ['project_id'])
    op.create_index('ix_caseproposal_status', 'caseproposal', ['status'])


def downgrade() -> None:
    op.drop_index('ix_caseproposal_status', table_name='caseproposal')
    op.drop_index('ix_caseproposal_project_id', table_name='caseproposal')
    op.drop_table('caseproposal')

    op.drop_column('workspace', 'ai_generation_limit_daily')

    op.drop_index('ix_testcase_is_ai_authored', table_name='testcase')
    op.drop_column('testcase', 'last_human_reviewed_by_id')
    op.drop_column('testcase', 'last_human_reviewed_at')
    op.drop_column('testcase', 'ai_confidence')
    op.drop_column('testcase', 'is_ai_authored')
    op.drop_column('testcase', 'code_paths')

    op.get_bind().execute(sa.text('DROP TYPE IF EXISTS caseproposalaction'))
