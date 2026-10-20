"""OpenAPI spec history (V2) and test ownership (V5) — WP16b

* `projectapispecversion`: numbered, hashed revisions of the project's
  OpenAPI document. Every existing `projectapispec` row is backfilled as
  version 1 (via='backfill') so the next store has something to diff with.
* `testcase.owner_user_id` / `backup_owner_user_id` / `last_reviewed_at` /
  `last_reviewed_by_id`.
* `caseownernotice`: dedupe rows for owner "your test started failing" email.

Revision ID: e122b3c4d612
Revises: e042b3c4d604
Create Date: 2026-10-20 21:00:00.000000
"""
import hashlib
import json
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'e122b3c4d612'
down_revision: Union[str, None] = 'e042b3c4d604'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def _ops(spec) -> int:
    n = 0
    for item in ((spec or {}).get("paths") or {}).values():
        if isinstance(item, dict):
            n += sum(1 for m, o in item.items() if m.lower() in HTTP_METHODS and isinstance(o, dict))
    return n


def upgrade() -> None:
    op.create_table(
        'projectapispecversion',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('sha256', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('spec', sa.JSON(), nullable=False),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('spec_version', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('source_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('operation_count', sa.Integer(), nullable=False),
        sa.Column('via', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'version', name='uq_projectapispecversion_project_version'),
    )
    op.create_index(op.f('ix_projectapispecversion_project_id'), 'projectapispecversion', ['project_id'], unique=False)

    op.create_table(
        'caseownernotice',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('test_case_id', sa.Integer(), nullable=False),
        sa.Column('test_run_id', sa.Integer(), nullable=False),
        sa.Column('after_result_id', sa.Integer(), nullable=False),
        sa.Column('recipients', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['test_case_id'], ['testcase.id']),
        sa.ForeignKeyConstraint(['test_run_id'], ['testrun.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('test_case_id', 'after_result_id', name='uq_caseownernotice_case_after'),
    )
    op.create_index(op.f('ix_caseownernotice_project_id'), 'caseownernotice', ['project_id'], unique=False)
    op.create_index(op.f('ix_caseownernotice_test_case_id'), 'caseownernotice', ['test_case_id'], unique=False)
    op.create_index(op.f('ix_caseownernotice_test_run_id'), 'caseownernotice', ['test_run_id'], unique=False)

    op.add_column('testcase', sa.Column('owner_user_id', sa.Integer(), nullable=True))
    op.add_column('testcase', sa.Column('backup_owner_user_id', sa.Integer(), nullable=True))
    op.add_column('testcase', sa.Column('last_reviewed_at', sa.DateTime(), nullable=True))
    op.add_column('testcase', sa.Column('last_reviewed_by_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_testcase_owner_user_id'), 'testcase', ['owner_user_id'], unique=False)
    op.create_foreign_key(None, 'testcase', 'users', ['owner_user_id'], ['id'])
    op.create_foreign_key(None, 'testcase', 'users', ['backup_owner_user_id'], ['id'])
    op.create_foreign_key(None, 'testcase', 'users', ['last_reviewed_by_id'], ['id'])

    # Backfill: the current document becomes version 1.
    conn = op.get_bind()
    versions = sa.table(
        'projectapispecversion',
        sa.column('project_id', sa.Integer), sa.column('version', sa.Integer),
        sa.column('sha256', sa.String), sa.column('spec', sa.JSON),
        sa.column('title', sa.String), sa.column('spec_version', sa.String),
        sa.column('source_url', sa.String), sa.column('operation_count', sa.Integer),
        sa.column('via', sa.String), sa.column('created_by_id', sa.Integer),
        sa.column('created_at', sa.DateTime))
    rows = conn.execute(sa.text(
        "SELECT project_id, spec, title, spec_version, source_url, updated_by_id, updated_at "
        "FROM projectapispec")).fetchall()
    for project_id, spec, title, spec_version, source_url, by, at in rows:
        if isinstance(spec, str):
            spec = json.loads(spec)
        digest = hashlib.sha256(json.dumps(spec or {}, sort_keys=True, separators=(",", ":"),
                                           default=str).encode("utf-8")).hexdigest()
        conn.execute(versions.insert().values(
            project_id=project_id, version=1, sha256=digest, spec=spec or {}, title=title,
            spec_version=spec_version, source_url=source_url, operation_count=_ops(spec),
            via='backfill', created_by_id=by, created_at=at or datetime.utcnow()))


def downgrade() -> None:
    op.drop_constraint('testcase_last_reviewed_by_id_fkey', 'testcase', type_='foreignkey')
    op.drop_constraint('testcase_backup_owner_user_id_fkey', 'testcase', type_='foreignkey')
    op.drop_constraint('testcase_owner_user_id_fkey', 'testcase', type_='foreignkey')
    op.drop_index(op.f('ix_testcase_owner_user_id'), table_name='testcase')
    op.drop_column('testcase', 'last_reviewed_by_id')
    op.drop_column('testcase', 'last_reviewed_at')
    op.drop_column('testcase', 'backup_owner_user_id')
    op.drop_column('testcase', 'owner_user_id')
    op.drop_index(op.f('ix_caseownernotice_test_run_id'), table_name='caseownernotice')
    op.drop_index(op.f('ix_caseownernotice_test_case_id'), table_name='caseownernotice')
    op.drop_index(op.f('ix_caseownernotice_project_id'), table_name='caseownernotice')
    op.drop_table('caseownernotice')
    op.drop_index(op.f('ix_projectapispecversion_project_id'), table_name='projectapispecversion')
    op.drop_table('projectapispecversion')
