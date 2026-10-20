"""environment locks + validated repairs (WP16c, decisions V3/V4)

projectenvironment.exclusive: one run at a time on that environment (Redis
lease at dispatch). testsuite.protected and
workspace.require_validation_before_accept: proposals need a passing
validation run before accept. testrun.is_validation (+ which proposal it
validated): validation runs are excluded from every statistic. Proposal
validation state on selectorhealproposal / caseproposal, and repairoutcome —
the event log WP16d computes repair success rates from.

Revision ID: e132b3c4d613
Revises: e122b3c4d612
Create Date: 2026-10-20 21:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'e132b3c4d613'
down_revision: Union[str, None] = 'e122b3c4d612'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROPOSAL_TABLES = ('selectorhealproposal', 'caseproposal')


def upgrade() -> None:
    op.add_column('projectenvironment', sa.Column('exclusive', sa.Boolean(), nullable=False,
                                                  server_default=sa.false()))
    op.add_column('testsuite', sa.Column('protected', sa.Boolean(), nullable=False,
                                         server_default=sa.false()))
    op.add_column('workspace', sa.Column('require_validation_before_accept', sa.Boolean(),
                                         nullable=False, server_default=sa.false()))
    op.add_column('testrun', sa.Column('is_validation', sa.Boolean(), nullable=False,
                                       server_default=sa.false()))
    op.create_index(op.f('ix_testrun_is_validation'), 'testrun', ['is_validation'], unique=False)
    op.add_column('testrun', sa.Column('validation_proposal_kind',
                                       sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('testrun', sa.Column('validation_proposal_id', sa.Integer(), nullable=True))

    for table in _PROPOSAL_TABLES:
        op.add_column(table, sa.Column('validation_status', sqlmodel.sql.sqltypes.AutoString(),
                                       nullable=True))
        op.add_column(table, sa.Column('validation_run_id', sa.Integer(), nullable=True))
        op.create_index(op.f(f'ix_{table}_validation_run_id'), table, ['validation_run_id'],
                        unique=False)
        op.create_foreign_key(None, table, 'testrun', ['validation_run_id'], ['id'])
        op.add_column(table, sa.Column('validation_conditions', sa.JSON(), nullable=True))
        op.add_column(table, sa.Column('validation_detail', sqlmodel.sql.sqltypes.AutoString(),
                                       nullable=True))
        op.add_column(table, sa.Column('validated_at', sa.DateTime(), nullable=True))

    op.create_table(
        'repairoutcome',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('proposal_kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('proposal_id', sa.Integer(), nullable=False),
        sa.Column('event', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=True),
        sa.Column('validated', sa.Boolean(), nullable=True),
        sa.Column('conditions_matched', sa.Boolean(), nullable=True),
        sa.Column('conditions', sa.JSON(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_repairoutcome_project_id'), 'repairoutcome', ['project_id'], unique=False)
    op.create_index(op.f('ix_repairoutcome_proposal_id'), 'repairoutcome', ['proposal_id'], unique=False)
    op.create_index(op.f('ix_repairoutcome_created_at'), 'repairoutcome', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_repairoutcome_created_at'), table_name='repairoutcome')
    op.drop_index(op.f('ix_repairoutcome_proposal_id'), table_name='repairoutcome')
    op.drop_index(op.f('ix_repairoutcome_project_id'), table_name='repairoutcome')
    op.drop_table('repairoutcome')
    for table in _PROPOSAL_TABLES:
        op.drop_column(table, 'validated_at')
        op.drop_column(table, 'validation_detail')
        op.drop_column(table, 'validation_conditions')
        op.drop_constraint(f'{table}_validation_run_id_fkey', table, type_='foreignkey')
        op.drop_index(op.f(f'ix_{table}_validation_run_id'), table_name=table)
        op.drop_column(table, 'validation_run_id')
        op.drop_column(table, 'validation_status')
    op.drop_column('testrun', 'validation_proposal_id')
    op.drop_column('testrun', 'validation_proposal_kind')
    op.drop_index(op.f('ix_testrun_is_validation'), table_name='testrun')
    op.drop_column('testrun', 'is_validation')
    op.drop_column('workspace', 'require_validation_before_accept')
    op.drop_column('testsuite', 'protected')
    op.drop_column('projectenvironment', 'exclusive')
