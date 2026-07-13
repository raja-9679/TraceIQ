"""environments_secrets

Revision ID: b7d8e9f0a1b2
Revises: a1c2e3b4d5f6
Create Date: 2026-07-13 14:00:00.000000

Environments + secrets:
  • projectenvironment — named deployment targets ({{env.KEY}}, base_url)
  • projectsecret      — Fernet-encrypted values ({{secret.KEY}})
  • testrun.environment_id — which environment a run executed against
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d8e9f0a1b2'
down_revision: Union[str, None] = 'a1c2e3b4d5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'projectenvironment',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('project.id'), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('base_url', sa.String(), nullable=True),
        sa.Column('variables', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('project_id', 'name', name='uq_projectenvironment_project_name'),
    )
    op.create_index('ix_projectenvironment_project_id', 'projectenvironment', ['project_id'])

    op.create_table(
        'projectsecret',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('project.id'), nullable=False),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('value_encrypted', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('project_id', 'key', name='uq_projectsecret_project_key'),
    )
    op.create_index('ix_projectsecret_project_id', 'projectsecret', ['project_id'])

    op.add_column('testrun', sa.Column('environment_id', sa.Integer(),
                                       sa.ForeignKey('projectenvironment.id'), nullable=True))


def downgrade() -> None:
    op.drop_column('testrun', 'environment_id')
    op.drop_index('ix_projectsecret_project_id', table_name='projectsecret')
    op.drop_table('projectsecret')
    op.drop_index('ix_projectenvironment_project_id', table_name='projectenvironment')
    op.drop_table('projectenvironment')
