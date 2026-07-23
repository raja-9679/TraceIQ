"""security scan: OpenAPI import + header auth

Revision ID: a1b2c3d4e5f6
Revises: f0a1b2c3d4e5
Create Date: 2026-07-23 17:30:00.000000

Adds columns to securityscan for API scanning (item 6) and header/token auth
(item 7):
- openapi_url: an OpenAPI/Swagger spec URL imported into ZAP before scanning.
- auth_header_name / auth_header_value: a request header injected for auth
  (e.g. Authorization: Bearer …). The value is a transient secret, cleared by
  the scan task once ZAP holds the replacer rule.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f0a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('securityscan', sa.Column('openapi_url', sa.String(), nullable=True))
    op.add_column('securityscan', sa.Column('auth_header_name', sa.String(), nullable=True))
    op.add_column('securityscan', sa.Column('auth_header_value', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('securityscan', 'auth_header_value')
    op.drop_column('securityscan', 'auth_header_name')
    op.drop_column('securityscan', 'openapi_url')
