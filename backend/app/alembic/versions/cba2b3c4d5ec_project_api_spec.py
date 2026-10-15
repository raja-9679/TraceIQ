"""project OpenAPI spec for expect_schema references (WP11)

Revision ID: cba2b3c4d5ec
Revises: c7a2b3c4d5e7
Create Date: 2026-10-15 20:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'cba2b3c4d5ec'
down_revision: Union[str, None] = 'c7a2b3c4d5e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('projectapispec',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('spec', sa.JSON(), nullable=True),
    sa.Column('source_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('spec_version', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_projectapispec_project_id'), 'projectapispec', ['project_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_projectapispec_project_id'), table_name='projectapispec')
    op.drop_table('projectapispec')
