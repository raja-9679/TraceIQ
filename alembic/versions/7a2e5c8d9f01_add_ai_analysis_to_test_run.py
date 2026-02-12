"""add_ai_analysis_to_test_run

Revision ID: 7a2e5c8d9f01
Revises: 5108734f9b51
Create Date: 2024-02-04 10:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '7a2e5c8d9f01'
down_revision = '5108734f9b51'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Add ai_analysis column to testrun table.

    This column stores AI-powered analysis of test failures including:
    - Failure patterns and classifications
    - Root cause suggestions
    - Recommended fixes
    """
    op.add_column('testrun', sa.Column(
        'ai_analysis', postgresql.JSON(), nullable=True))


def downgrade() -> None:
    """
    Remove ai_analysis column from testrun table.
    """
    op.drop_column('testrun', 'ai_analysis')
