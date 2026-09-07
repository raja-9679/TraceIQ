"""phase_e

Revision ID: f8b3c4d5e6f7
Revises: e7a1b2c3d4e5
Create Date: 2026-05-13 12:00:00.000000

Phase E — Agent provenance:
  • created_by_agent_id (String, nullable) on testsuite, testcase, caseproposal
  • agent_session_id    (String, indexed, nullable) on the same three tables

Lets a workspace policy say "auto-approve delete proposals from the same
agent session that created the entity" without conflating with the
existing created_by_id (which points to a User).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8b3c4d5e6f7'
down_revision: Union[str, None] = 'e7a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ('testsuite', 'testcase', 'caseproposal'):
        op.add_column(table, sa.Column('created_by_agent_id', sa.String(), nullable=True))
        op.add_column(table, sa.Column('agent_session_id', sa.String(), nullable=True))
        op.create_index(f'ix_{table}_agent_session_id', table, ['agent_session_id'])


def downgrade() -> None:
    for table in ('testsuite', 'testcase', 'caseproposal'):
        op.drop_index(f'ix_{table}_agent_session_id', table_name=table)
        op.drop_column(table, 'agent_session_id')
        op.drop_column(table, 'created_by_agent_id')
