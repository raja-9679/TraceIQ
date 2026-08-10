"""Separation of duties on agent proposals (workstream F4)

`caseproposal` recorded who *decided* a proposal but not who *filed* it, so the
accept path had nothing to compare against and an editor could accept their own
proposal — a review queue in shape only.

`created_by_id` is nullable because rows written before this migration are
genuinely unattributed. The policy treats unknown as "not a conflict"; treating
it as a violation would freeze every queue that already exists.

`workspace.require_separate_approver` defaults to false. A solo user whose agent
files proposals and who then accepts them in the UI is a normal workflow, and
turning enforcement on for every existing install would break it. The
REQUIRE_SEPARATE_APPROVER instance setting is the floor for operators who want
it everywhere.

**The foreign key is deliberately unnamed.** `scripts/bootstrap_db.py` builds
fresh schemas from model metadata rather than running migrations, so SQLAlchemy
names the constraint `caseproposal_created_by_id_fkey`. An explicitly named
constraint here would exist only on databases that were *migrated*, and the
downgrade would then fail on every fresh install with "constraint ... does not
exist" — which is exactly what happened while writing this. Passing None gets
the same default name on both paths. This is the same family of trap as the
audit trigger in c8d9e0f1a2b3.

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
"""
from alembic import op
import sqlalchemy as sa

revision = 'e0f1a2b3c4d5'
down_revision = 'd9e0f1a2b3c4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('caseproposal',
                  sa.Column('created_by_id', sa.Integer(), nullable=True))
    op.create_foreign_key(None, 'caseproposal', 'users', ['created_by_id'], ['id'])
    op.add_column('workspace',
                  sa.Column('require_separate_approver', sa.Boolean(),
                            nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('workspace', 'require_separate_approver')
    # Dropping the column takes its foreign key with it, so there is no
    # constraint name to get wrong.
    op.drop_column('caseproposal', 'created_by_id')
