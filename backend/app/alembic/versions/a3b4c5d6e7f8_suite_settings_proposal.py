"""caseproposalaction: add update_suite_settings

Revision ID: a3b4c5d6e7f8
Revises: f1a2b3c4d5e6
Create Date: 2026-08-05 12:00:00.000000

Agents can now propose changes to a TestSuite's settings blob (inherited
headers/params/auth) through the same CaseProposal review queue as case
edits. The new action is deliberately excluded from workspace auto-apply:
suite headers are sent to the app under test with real credentials, so a
human must review every change.

Note the enum labels for caseproposalaction are lowercase in Postgres
(values_callable on the model), matching executionmode — not the uppercase
teststatus convention.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PG >= 12 allows ADD VALUE inside a transaction as long as the new
    # label is not used in the same transaction (we don't use it here).
    op.execute(
        "ALTER TYPE caseproposalaction ADD VALUE IF NOT EXISTS 'update_suite_settings'"
    )


def downgrade() -> None:
    # Postgres cannot drop an enum label. Rows using it would have to be
    # deleted and the type rebuilt; leaving the label in place is harmless.
    pass
