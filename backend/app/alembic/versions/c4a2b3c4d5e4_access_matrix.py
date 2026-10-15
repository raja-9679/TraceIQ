"""access matrix (identity matrix runs + access-control testing)

Revision ID: c4a2b3c4d5e4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-15 18:00:00.000000

Adds testrun.matrix_group / testrun.anonymous for persona fan-out runs and the
accessmatrix / accessmatrixrun / accessmatrixcell tables.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'c4a2b3c4d5e4'
down_revision: Union[str, None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('testrun', sa.Column('matrix_group', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('testrun', sa.Column('anonymous', sa.Boolean(), nullable=True))
    op.create_index(op.f('ix_testrun_matrix_group'), 'testrun', ['matrix_group'], unique=False)

    op.create_table(
        'accessmatrix',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('base_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('persona_ids', sa.JSON(), nullable=True),
        sa.Column('include_anonymous', sa.Boolean(), nullable=False),
        sa.Column('deny_statuses', sa.JSON(), nullable=True),
        sa.Column('rows', sa.JSON(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_accessmatrix_project_id'), 'accessmatrix', ['project_id'], unique=False)

    op.create_table(
        'accessmatrixrun',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('matrix_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('matrix_group', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=True),
        sa.Column('summary', sa.JSON(), nullable=True),
        sa.Column('authorized', sa.Boolean(), nullable=False),
        sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['matrix_id'], ['accessmatrix.id']),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_accessmatrixrun_matrix_id'), 'accessmatrixrun', ['matrix_id'], unique=False)
    op.create_index(op.f('ix_accessmatrixrun_project_id'), 'accessmatrixrun', ['project_id'], unique=False)
    op.create_index(op.f('ix_accessmatrixrun_matrix_group'), 'accessmatrixrun', ['matrix_group'], unique=False)

    op.create_table(
        'accessmatrixcell',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('matrix_run_id', sa.Integer(), nullable=False),
        sa.Column('row_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('persona_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('expect', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('expected_status', sa.Integer(), nullable=True),
        sa.Column('verdict', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('reason', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('observed', sa.JSON(), nullable=True),
        sa.Column('test_run_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['matrix_run_id'], ['accessmatrixrun.id']),
        sa.ForeignKeyConstraint(['test_run_id'], ['testrun.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_accessmatrixcell_matrix_run_id'), 'accessmatrixcell', ['matrix_run_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_accessmatrixcell_matrix_run_id'), table_name='accessmatrixcell')
    op.drop_table('accessmatrixcell')
    op.drop_index(op.f('ix_accessmatrixrun_matrix_group'), table_name='accessmatrixrun')
    op.drop_index(op.f('ix_accessmatrixrun_project_id'), table_name='accessmatrixrun')
    op.drop_index(op.f('ix_accessmatrixrun_matrix_id'), table_name='accessmatrixrun')
    op.drop_table('accessmatrixrun')
    op.drop_index(op.f('ix_accessmatrix_project_id'), table_name='accessmatrix')
    op.drop_table('accessmatrix')
    op.drop_index(op.f('ix_testrun_matrix_group'), table_name='testrun')
    op.drop_column('testrun', 'anonymous')
    op.drop_column('testrun', 'matrix_group')
