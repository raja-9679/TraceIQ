"""SCIM external ids on users and teams (workstream F2)

Okta and Entra reconcile their directory against the target system by
`externalId`, not by username. Without storing it, every sync run looks at a
user it has already provisioned and sees a stranger — which is how SCIM
integrations end up creating duplicate accounts on every poll.

Nullable on purpose: local, invited and self-registered accounts have no IdP
counterpart, and adopting an existing account is a supported flow (SCIM POST
for an email that already exists links the row rather than 409ing forever).

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
"""
from alembic import op
import sqlalchemy as sa

revision = 'd9e0f1a2b3c4'
down_revision = 'c8d9e0f1a2b3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('scim_external_id', sa.String(), nullable=True))
    op.create_index('ix_users_scim_external_id', 'users', ['scim_external_id'])
    op.add_column('team', sa.Column('scim_external_id', sa.String(), nullable=True))
    op.create_index('ix_team_scim_external_id', 'team', ['scim_external_id'])


def downgrade() -> None:
    op.drop_index('ix_team_scim_external_id', table_name='team')
    op.drop_column('team', 'scim_external_id')
    op.drop_index('ix_users_scim_external_id', table_name='users')
    op.drop_column('users', 'scim_external_id')
