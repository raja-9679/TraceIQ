"""trust center + PoC report share links (WP13b)

trustcenter: one public trust page per workspace (off by default).
trustincidentpost: admin-authored incident communications.
trustdocument: gated documents; downloads only through sharelink rows of
kind trust_document. sharelink.params: a poc_report link's frozen range,
project selection and time-saved assumptions.

Revision ID: cf22b3c4d5f6
Revises: cf12b3c4d5f5
Create Date: 2026-10-16 22:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'cf22b3c4d5f6'
down_revision: Union[str, None] = 'cf12b3c4d5f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sharelink', sa.Column('params', sa.JSON(), nullable=True))

    op.create_table(
        'trustcenter',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('slug', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('intro', sa.Text(), nullable=True),
        sa.Column('contact_email', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('status_page_ids', sa.JSON(), nullable=True),
        sa.Column('posture_items', sa.JSON(), nullable=True),
        sa.Column('allow_access_requests', sa.Boolean(), nullable=False),
        sa.Column('audit_verified_at', sa.DateTime(), nullable=True),
        sa.Column('audit_verified_ok', sa.Boolean(), nullable=True),
        sa.Column('view_count', sa.Integer(), nullable=False),
        sa.Column('last_viewed_at', sa.DateTime(), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id']),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_trustcenter_workspace_id'), 'trustcenter', ['workspace_id'], unique=True)
    op.create_index(op.f('ix_trustcenter_slug'), 'trustcenter', ['slug'], unique=True)

    op.create_table(
        'trustincidentpost',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('components', sa.JSON(), nullable=True),
        sa.Column('updates', sa.JSON(), nullable=True),
        sa.Column('published', sa.Boolean(), nullable=False),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_trustincidentpost_workspace_id'), 'trustincidentpost', ['workspace_id'], unique=False)
    op.create_index(op.f('ix_trustincidentpost_created_at'), 'trustincidentpost', ['created_at'], unique=False)

    op.create_table(
        'trustdocument',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('filename', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('content_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('sha256', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('object_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('listed', sa.Boolean(), nullable=False),
        sa.Column('uploaded_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id']),
        sa.ForeignKeyConstraint(['uploaded_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_trustdocument_workspace_id'), 'trustdocument', ['workspace_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_trustdocument_workspace_id'), table_name='trustdocument')
    op.drop_table('trustdocument')
    op.drop_index(op.f('ix_trustincidentpost_created_at'), table_name='trustincidentpost')
    op.drop_index(op.f('ix_trustincidentpost_workspace_id'), table_name='trustincidentpost')
    op.drop_table('trustincidentpost')
    op.drop_index(op.f('ix_trustcenter_slug'), table_name='trustcenter')
    op.drop_index(op.f('ix_trustcenter_workspace_id'), table_name='trustcenter')
    op.drop_table('trustcenter')
    op.drop_column('sharelink', 'params')
