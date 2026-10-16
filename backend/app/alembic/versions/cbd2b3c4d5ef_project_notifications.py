"""project notification settings + per-person project preferences

Project.notification_settings: who is emailed about the project's runs and
the project's own Slack/Teams webhooks (encrypted). ProjectNotificationPref:
one person's all/failures/off choice for one project.

Behaviour change for installs that set the instance-wide SLACK/TEAMS_WEBHOOK_URL:
it is no longer used for project runs (it received every project's runs);
each project sets its own webhook. Not migrated into projects on purpose —
copying it everywhere would recreate the cross-project channel.

Revision ID: cbd2b3c4d5ef
Revises: cbc2b3c4d5ee
Create Date: 2026-10-16 17:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'cbd2b3c4d5ef'
down_revision: Union[str, None] = 'cbc2b3c4d5ee'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('project', sa.Column('notification_settings', sa.JSON(), nullable=True))
    op.create_table(
        'projectnotificationpref',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('mode', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'project_id', name='uq_project_notification_pref'),
    )
    op.create_index(op.f('ix_projectnotificationpref_user_id'), 'projectnotificationpref', ['user_id'], unique=False)
    op.create_index(op.f('ix_projectnotificationpref_project_id'), 'projectnotificationpref', ['project_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_projectnotificationpref_project_id'), table_name='projectnotificationpref')
    op.drop_index(op.f('ix_projectnotificationpref_user_id'), table_name='projectnotificationpref')
    op.drop_table('projectnotificationpref')
    op.drop_column('project', 'notification_settings')
