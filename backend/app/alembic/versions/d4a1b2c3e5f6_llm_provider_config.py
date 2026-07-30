"""llm_provider_config table (multi-provider AI registry)

Several saved LLM providers, admin-managed; multiple can be active and users
pick one per analysis. api_key_encrypted is Fernet ciphertext. With no rows,
the legacy single-provider instance settings still apply.

Revision ID: d4a1b2c3e5f6
Revises: cb390bfd3836
Create Date: 2026-07-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd4a1b2c3e5f6'
down_revision: Union[str, Sequence[str], None] = 'cb390bfd3836'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'llm_provider_config',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('provider_type', sa.String(), nullable=False),
        sa.Column('model', sa.String(), nullable=False),
        sa.Column('base_url', sa.String(), nullable=True),
        sa.Column('api_key_encrypted', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('updated_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
    )
    op.create_index('ix_llm_provider_config_name', 'llm_provider_config', ['name'], unique=True)
    op.create_index('ix_llm_provider_config_is_active', 'llm_provider_config', ['is_active'])


def downgrade() -> None:
    op.drop_index('ix_llm_provider_config_is_active', table_name='llm_provider_config')
    op.drop_index('ix_llm_provider_config_name', table_name='llm_provider_config')
    op.drop_table('llm_provider_config')
