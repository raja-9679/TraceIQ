"""Audit trail: actor context, hash chain, append-only trigger

Adds the columns `app.services.audit` writes, and a trigger that rejects UPDATE
and DELETE on the table.

Existing rows keep NULL hashes. They are reported as *unverifiable* rather than
intact — backfilling a chain over them would be worse than useless, since a
chain computed after the fact proves nothing about what the rows said before.

The trigger is the reason `workspace_service.delete_workspace` had to stop
nullifying `workspace_id` on audit rows: that was a live UPDATE against this
table, and it would now raise.

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
"""
from alembic import op
import sqlalchemy as sa


revision = 'c8d9e0f1a2b3'
down_revision = 'b7c8d9e0f1a2'
branch_labels = None
depends_on = None


_GUARD_FN = """
CREATE OR REPLACE FUNCTION traceiq_auditlog_append_only()
RETURNS TRIGGER AS $$
BEGIN
    -- UPDATE is never permitted. There is no legitimate reason to rewrite an
    -- audit row, and allowing it under any flag would give an attacker the
    -- same door as an operator.
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            'auditlog is append-only: UPDATE on row % is not permitted', OLD.id
            USING HINT = 'History is evidence. It is never corrected in place.';
    END IF;

    -- DELETE is permitted only to the retention task, which announces itself
    -- with a session-local setting. That keeps every ordinary path blocked
    -- (an ORM cascade, a careless script, an application bug) while leaving
    -- one deliberate, greppable way to expire history that is past its
    -- required retention period.
    IF current_setting('traceiq.audit_retention', true) = 'on' THEN
        RETURN OLD;
    END IF;

    RAISE EXCEPTION
        'auditlog is append-only: DELETE on row % is not permitted', OLD.id
        USING HINT = 'Only the retention task may expire audit rows; it sets '
                     'traceiq.audit_retention=on for the duration.';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    with op.batch_alter_table('auditlog') as batch:
        batch.add_column(sa.Column('actor_type', sa.String(), nullable=True))
        batch.add_column(sa.Column('actor_label', sa.String(), nullable=True))
        batch.add_column(sa.Column('ip_address', sa.String(), nullable=True))
        batch.add_column(sa.Column('user_agent', sa.String(), nullable=True))
        batch.add_column(sa.Column('prev_hash', sa.String(), nullable=True))
        batch.add_column(sa.Column('row_hash', sa.String(), nullable=True))

    op.create_index('ix_auditlog_row_hash', 'auditlog', ['row_hash'])
    # Reading the trail is now a first-class operation (workspace-scoped list,
    # CSV export), so give it an index rather than a sequential scan.
    op.create_index('ix_auditlog_workspace_timestamp', 'auditlog',
                    ['workspace_id', 'timestamp'])

    # Drop the FK from auditlog to workspace.
    #
    # It had no ON DELETE clause, so deleting a workspace with any audit
    # history raised a foreign-key violation. The old code worked around that
    # by NULLing workspace_id on every affected row — destroying the
    # association an auditor most wants — and with the append-only trigger
    # below, that workaround now raises instead.
    #
    # A foreign key from an append-only history table into a mutable entity
    # table is the design error: it forces a choice between destroying history
    # and blocking deletion. The column stays as a plain integer, so history
    # outlives the object it describes.
    op.drop_constraint('auditlog_workspace_id_fkey', 'auditlog', type_='foreignkey')
    # Same reasoning for the actor: a hard user deletion must not be blocked by
    # history, and must not be able to erase it either. GDPR erasure already
    # scrubs the users row rather than deleting it, so the audit trail keeps a
    # user_id that resolves to a scrubbed account — which is the correct
    # outcome, not a leak.
    op.drop_constraint('auditlog_user_id_fkey', 'auditlog', type_='foreignkey')

    op.execute(_GUARD_FN)
    op.execute("""
        CREATE TRIGGER traceiq_auditlog_no_update
        BEFORE UPDATE ON auditlog
        FOR EACH ROW EXECUTE FUNCTION traceiq_auditlog_append_only();
    """)
    op.execute("""
        CREATE TRIGGER traceiq_auditlog_no_delete
        BEFORE DELETE ON auditlog
        FOR EACH ROW EXECUTE FUNCTION traceiq_auditlog_append_only();
    """)


def downgrade() -> None:
    op.create_foreign_key('auditlog_user_id_fkey', 'auditlog', 'users',
                          ['user_id'], ['id'])
    op.create_foreign_key('auditlog_workspace_id_fkey', 'auditlog', 'workspace',
                          ['workspace_id'], ['id'])
    op.execute("DROP TRIGGER IF EXISTS traceiq_auditlog_no_delete ON auditlog;")
    op.execute("DROP TRIGGER IF EXISTS traceiq_auditlog_no_update ON auditlog;")
    op.execute("DROP FUNCTION IF EXISTS traceiq_auditlog_append_only();")
    op.drop_index('ix_auditlog_workspace_timestamp', table_name='auditlog')
    op.drop_index('ix_auditlog_row_hash', table_name='auditlog')
    with op.batch_alter_table('auditlog') as batch:
        for column in ('row_hash', 'prev_hash', 'user_agent',
                       'ip_address', 'actor_label', 'actor_type'):
            batch.drop_column(column)
