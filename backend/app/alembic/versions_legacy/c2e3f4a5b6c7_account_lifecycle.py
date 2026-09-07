"""account lifecycle: email verification + account tokens

Revision ID: c2e3f4a5b6c7
Revises: b1d2e3f4a5b6
Create Date: 2026-07-17 14:00:00.000000

Adds users.is_verified / email_verified_at and the accounttoken table backing
password-reset and email-verification flows.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2e3f4a5b6c7'
down_revision: Union[str, None] = 'b1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('is_verified', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('email_verified_at', sa.DateTime(), nullable=True))

    op.create_table(
        'accounttoken',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('purpose', sa.String(), nullable=False),
        sa.Column('hashed_token', sa.String(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_accounttoken_user_id', 'accounttoken', ['user_id'])
    op.create_index('ix_accounttoken_purpose', 'accounttoken', ['purpose'])
    op.create_index('ix_accounttoken_hashed_token', 'accounttoken', ['hashed_token'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_accounttoken_hashed_token', table_name='accounttoken')
    op.drop_index('ix_accounttoken_purpose', table_name='accounttoken')
    op.drop_index('ix_accounttoken_user_id', table_name='accounttoken')
    op.drop_table('accounttoken')
    op.drop_column('users', 'email_verified_at')
    op.drop_column('users', 'is_verified')
