"""Result↔case link + case validation stamps (MCP v2 / impact-analysis v2)

- testcaseresult.test_case_id (FK testcase.id, indexed): which case produced a
  result. Fixes the run-history conflation where results were matched to cases
  by test_name alone (same-named cases across suites got mixed together).
  Backfilled exactly for single-case runs from testrun.test_case_id; suite-run
  rows stay NULL and keep the name-matching fallback until stamped by the
  aggregator going forward.
- testcase.last_validated_commit / last_validated_at: stamped by the result
  aggregator when a run carrying git_commit PASSES a case. Powers the
  "which tests need altering vs just re-running" signal in impact analysis.

Revision ID: f1a2b3c4d5e6
Revises: e7c2d3f4a5b6
Create Date: 2026-08-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'e7c2d3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('testcase', sa.Column('last_validated_commit', sa.String(), nullable=True))
    op.add_column('testcase', sa.Column('last_validated_at', sa.DateTime(), nullable=True))

    op.add_column('testcaseresult', sa.Column('test_case_id', sa.Integer(), nullable=True))
    # SET NULL: suite-level runs' results may outlive an individual case.
    op.create_foreign_key(
        'fk_testcaseresult_test_case_id', 'testcaseresult', 'testcase',
        ['test_case_id'], ['id'], ondelete='SET NULL')
    op.create_index(
        'ix_testcaseresult_test_case_id', 'testcaseresult', ['test_case_id'])

    # Exact backfill for single-case runs; suite runs stay NULL (name fallback).
    op.execute(
        """
        UPDATE testcaseresult
        SET test_case_id = testrun.test_case_id
        FROM testrun
        WHERE testcaseresult.test_run_id = testrun.id
          AND testrun.test_case_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index('ix_testcaseresult_test_case_id', table_name='testcaseresult')
    op.drop_constraint('fk_testcaseresult_test_case_id', 'testcaseresult', type_='foreignkey')
    op.drop_column('testcaseresult', 'test_case_id')
    op.drop_column('testcase', 'last_validated_at')
    op.drop_column('testcase', 'last_validated_commit')
