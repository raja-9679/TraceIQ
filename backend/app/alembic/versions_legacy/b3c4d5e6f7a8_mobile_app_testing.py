"""Mobile app testing (Phase MOB)

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-07-25 12:00:00.000000

Adds the MobileAppBuild registry (uploaded APK/AAB/IPA binaries, bytes in
MinIO) and TestRun.app_build_id so a mobile_appium run knows which binary to
install. The executor value itself needs no migration — ExecutorType is
stored as a plain string by design.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'mobileappbuild',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('platform', sa.String(), nullable=False, server_default='android'),
        sa.Column('app_name', sa.String(), nullable=False),
        sa.Column('version_name', sa.String(), nullable=True),
        sa.Column('build_number', sa.String(), nullable=True),
        sa.Column('package_id', sa.String(), nullable=True),
        sa.Column('file_key', sa.String(), nullable=False),
        sa.Column('file_size', sa.BigInteger(), nullable=True),
        sa.Column('original_filename', sa.String(), nullable=True),
        sa.Column('notes', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_mobileappbuild_project_id', 'mobileappbuild', ['project_id'])

    op.add_column('testrun', sa.Column('app_build_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('testrun', 'app_build_id')
    op.drop_index('ix_mobileappbuild_project_id', table_name='mobileappbuild')
    op.drop_table('mobileappbuild')
