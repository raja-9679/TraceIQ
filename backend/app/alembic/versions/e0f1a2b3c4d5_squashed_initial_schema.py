"""Squashed initial schema

Revision ID: e0f1a2b3c4d5
Revises: (none — this is the root)

The whole schema as one revision, generated from the SQLModel metadata with
`alembic revision --autogenerate` against an empty PostgreSQL database and then
hand-adjusted (enum types hoisted, append-only audit trigger added, downgrade
made to leave a genuinely empty database).

Why the revision id is the OLD head
-----------------------------------
Before this file existed the chain had 49 revisions whose root,
`1f266105057e`, was an empty `pass` stub: it had been stamped onto a database
that `SQLModel.metadata.create_all()` had already built, so no revision ever
created the core tables and `alembic upgrade head` could not build a schema
from scratch. Those files now live in `app/alembic/versions_legacy/`, and
their head was `e0f1a2b3c4d5`.

This revision reuses that id on purpose. A database that finished the legacy
chain is stamped `e0f1a2b3c4d5`, which is exactly the schema this file
describes, so it is *already at the root of the new chain* — no stamp, no
operator action, no special case. `scripts/bootstrap_db.py` handles the only
other population: a database stamped at some earlier legacy revision, which it
first brings to `e0f1a2b3c4d5` through the legacy directory and then continues
here.

The known differences between "reached e0f1a2b3c4d5 by migrating" and "built
by this file" are reconciled by the next revision, which creates a few indexes
`IF NOT EXISTS` — see its docstring.

Enum types
----------
`teststatus` is shared by two tables. Alembic's `create_table` would issue
`CREATE TYPE` for each `sa.Enum` column and fail on the second, so the types
are declared once with `create_type=False` and created/dropped explicitly.
Labels are what the models declare: `executionmode`/`runtrigger`/
`caseproposalaction` use lowercase VALUES, `teststatus` uses uppercase NAMES.
Any raw SQL against those columns has to know that (see `UPPER(status::text)`
in the legacy `a2f4c6d8e0b1`).

Audit trigger
-------------
The append-only guard on `auditlog` is not model metadata, so autogenerate
cannot know about it. It is restated here (and kept in
`app/services/audit.py` for the `create_all` path the test-suite uses).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e0f1a2b3c4d5'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_EXECUTIONMODE = postgresql.ENUM('continuous', 'separate', 'parallel', name='executionmode', create_type=False)
_TESTSTATUS = postgresql.ENUM('PENDING', 'RUNNING', 'PASSED', 'FAILED', 'ERROR', name='teststatus', create_type=False)
_RUNTRIGGER = postgresql.ENUM('human', 'schedule', 'api_agent', 'ci', 'webhook', name='runtrigger', create_type=False)
_CASEPROPOSALACTION = postgresql.ENUM('create', 'update', 'delete', 'move', 'update_suite_settings', name='caseproposalaction', create_type=False)
_ENUM_TYPES = (_EXECUTIONMODE, _TESTSTATUS, _RUNTRIGGER, _CASEPROPOSALACTION)


# Append-only guard on auditlog. Duplicated from app/services/audit.py on
# purpose: a migration must be frozen text, not a live import of application
# code that may change under it.
_AUDIT_GUARD_FN = """
CREATE OR REPLACE FUNCTION traceiq_auditlog_append_only()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            'auditlog is append-only: UPDATE on row % is not permitted', OLD.id
            USING HINT = 'History is evidence. It is never corrected in place.';
    END IF;
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

_AUDIT_TRIGGERS = (
    "CREATE TRIGGER traceiq_auditlog_no_update"
    " BEFORE UPDATE ON auditlog"
    " FOR EACH ROW EXECUTE FUNCTION traceiq_auditlog_append_only()",
    "CREATE TRIGGER traceiq_auditlog_no_delete"
    " BEFORE DELETE ON auditlog"
    " FOR EACH ROW EXECUTE FUNCTION traceiq_auditlog_append_only()",
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in _ENUM_TYPES:
        enum_type.create(bind, checkfirst=True)

    op.create_table('auditlog',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('entity_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('entity_id', sa.Integer(), nullable=False),
    sa.Column('action', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('workspace_id', sa.Integer(), nullable=True),
    sa.Column('timestamp', sa.DateTime(), nullable=False),
    sa.Column('changes', sa.JSON(), nullable=True),
    sa.Column('actor_type', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('actor_label', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('ip_address', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('user_agent', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('prev_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('row_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_auditlog_row_hash'), 'auditlog', ['row_hash'], unique=False)
    op.create_index(op.f('ix_auditlog_user_id'), 'auditlog', ['user_id'], unique=False)
    op.create_index(op.f('ix_auditlog_workspace_id'), 'auditlog', ['workspace_id'], unique=False)
    op.create_index('ix_auditlog_workspace_timestamp', 'auditlog', ['workspace_id', 'timestamp'], unique=False)
    op.create_table('permission',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('scope', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('action', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('resource', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('plan',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('display_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('price_cents', sa.Integer(), nullable=False),
    sa.Column('stripe_price_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('limits', sa.JSON(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_plan_name'), 'plan', ['name'], unique=True)
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('full_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('hashed_password', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('last_login_at', sa.DateTime(), nullable=True),
    sa.Column('is_verified', sa.Boolean(), nullable=False),
    sa.Column('email_verified_at', sa.DateTime(), nullable=True),
    sa.Column('mfa_enabled', sa.Boolean(), nullable=False),
    sa.Column('mfa_secret', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('is_instance_admin', sa.Boolean(), nullable=False),
    sa.Column('scim_external_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_index(op.f('ix_users_scim_external_id'), 'users', ['scim_external_id'], unique=False)
    op.create_table('accounttoken',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('purpose', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('hashed_token', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_accounttoken_hashed_token'), 'accounttoken', ['hashed_token'], unique=True)
    op.create_index(op.f('ix_accounttoken_purpose'), 'accounttoken', ['purpose'], unique=False)
    op.create_index(op.f('ix_accounttoken_user_id'), 'accounttoken', ['user_id'], unique=False)
    op.create_table('instance_settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('value', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('is_secret', sa.Boolean(), nullable=False),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_instance_settings_key'), 'instance_settings', ['key'], unique=True)
    op.create_table('llm_provider_config',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('provider_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('model', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('base_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('api_key_encrypted', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_llm_provider_config_is_active'), 'llm_provider_config', ['is_active'], unique=False)
    op.create_index(op.f('ix_llm_provider_config_name'), 'llm_provider_config', ['name'], unique=True)
    op.create_table('mfarecoverycode',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('code_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_mfarecoverycode_code_hash'), 'mfarecoverycode', ['code_hash'], unique=False)
    op.create_index(op.f('ix_mfarecoverycode_user_id'), 'mfarecoverycode', ['user_id'], unique=False)
    op.create_table('refreshtoken',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('hashed_token', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('family_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.Column('user_agent', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('ip_address', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_refreshtoken_family_id'), 'refreshtoken', ['family_id'], unique=False)
    op.create_index(op.f('ix_refreshtoken_hashed_token'), 'refreshtoken', ['hashed_token'], unique=False)
    op.create_index(op.f('ix_refreshtoken_user_id'), 'refreshtoken', ['user_id'], unique=False)
    op.create_table('tenant',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tenant_name'), 'tenant', ['name'], unique=False)
    op.create_table('user_settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('theme', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('timezone', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('date_format', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('default_browser', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('default_device', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('default_timeout', sa.Integer(), nullable=False),
    sa.Column('auto_retry', sa.Boolean(), nullable=False),
    sa.Column('max_retries', sa.Integer(), nullable=False),
    sa.Column('parallel_execution', sa.Boolean(), nullable=False),
    sa.Column('max_parallel_tests', sa.Integer(), nullable=False),
    sa.Column('multi_browser_enabled', sa.Boolean(), nullable=False),
    sa.Column('selected_browsers', sa.JSON(), nullable=True),
    sa.Column('multi_device_enabled', sa.Boolean(), nullable=False),
    sa.Column('selected_devices', sa.JSON(), nullable=True),
    sa.Column('email_notifications', sa.Boolean(), nullable=False),
    sa.Column('notify_on_completion', sa.Boolean(), nullable=False),
    sa.Column('notify_on_failure', sa.Boolean(), nullable=False),
    sa.Column('daily_summary', sa.Boolean(), nullable=False),
    sa.Column('notification_email', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('video_recording', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('screenshot_on_error', sa.Boolean(), nullable=False),
    sa.Column('trace_files', sa.Boolean(), nullable=False),
    sa.Column('retention_period', sa.Integer(), nullable=False),
    sa.Column('auto_cleanup', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_settings_user_id'), 'user_settings', ['user_id'], unique=False)
    op.create_table('role',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=True),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('workspace',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=True),
    sa.Column('ai_generation_limit_daily', sa.Integer(), nullable=False),
    sa.Column('auto_apply_threshold', sa.Float(), nullable=True),
    sa.Column('require_separate_approver', sa.Boolean(), nullable=False),
    sa.Column('max_concurrent_runs', sa.Integer(), nullable=False),
    sa.Column('active_scan_enabled', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('issuetrackerconfig',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('provider', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('base_url', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('auth_user', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('auth_secret_encrypted', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('settings', sa.JSON(), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_issuetrackerconfig_workspace_id'), 'issuetrackerconfig', ['workspace_id'], unique=False)
    op.create_table('llmusageevent',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('workspace_id', sa.Integer(), nullable=True),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('provider', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('model', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('feature', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('source', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('total_tokens', sa.Integer(), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=False),
    sa.Column('success', sa.Boolean(), nullable=False),
    sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_llmusageevent_created_at'), 'llmusageevent', ['created_at'], unique=False)
    op.create_index(op.f('ix_llmusageevent_feature'), 'llmusageevent', ['feature'], unique=False)
    op.create_index(op.f('ix_llmusageevent_model'), 'llmusageevent', ['model'], unique=False)
    op.create_index(op.f('ix_llmusageevent_project_id'), 'llmusageevent', ['project_id'], unique=False)
    op.create_index(op.f('ix_llmusageevent_provider'), 'llmusageevent', ['provider'], unique=False)
    op.create_index(op.f('ix_llmusageevent_run_id'), 'llmusageevent', ['run_id'], unique=False)
    op.create_index(op.f('ix_llmusageevent_workspace_id'), 'llmusageevent', ['workspace_id'], unique=False)
    op.create_table('project',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('quality_gate_policy', sa.JSON(), nullable=True),
    sa.Column('ci_settings', sa.JSON(), nullable=True),
    sa.Column('security_settings', sa.JSON(), nullable=True),
    sa.Column('data_policy', sa.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('rolepermission',
    sa.Column('role_id', sa.Integer(), nullable=False),
    sa.Column('permission_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['permission_id'], ['permission.id'], ),
    sa.ForeignKeyConstraint(['role_id'], ['role.id'], ),
    sa.PrimaryKeyConstraint('role_id', 'permission_id')
    )
    op.create_table('team',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('scim_external_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_team_scim_external_id'), 'team', ['scim_external_id'], unique=False)
    op.create_table('usagerecord',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('period', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('metric', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'period', 'metric', name='uq_usage_ws_period_metric')
    )
    op.create_index(op.f('ix_usagerecord_period'), 'usagerecord', ['period'], unique=False)
    op.create_index(op.f('ix_usagerecord_workspace_id'), 'usagerecord', ['workspace_id'], unique=False)
    op.create_table('usersystemrole',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('role_id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['role_id'], ['role.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('user_id', 'role_id', 'tenant_id')
    )
    op.create_table('userworkspace',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('role', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('role_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['role_id'], ['role.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ),
    sa.PrimaryKeyConstraint('user_id', 'workspace_id')
    )
    op.create_table('workspaceinvitation',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('role', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('invited_by_id', sa.Integer(), nullable=False),
    sa.Column('token', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('project_role', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.ForeignKeyConstraint(['invited_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_workspaceinvitation_email'), 'workspaceinvitation', ['email'], unique=False)
    op.create_index(op.f('ix_workspaceinvitation_token'), 'workspaceinvitation', ['token'], unique=True)
    op.create_table('workspacesubscription',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('plan_id', sa.Integer(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('current_period_start', sa.DateTime(), nullable=True),
    sa.Column('current_period_end', sa.DateTime(), nullable=True),
    sa.Column('stripe_customer_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('stripe_subscription_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['plan.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_workspacesubscription_workspace_id'), 'workspacesubscription', ['workspace_id'], unique=True)
    op.create_table('apikey',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('prefix', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('hashed_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('role_id', sa.Integer(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.ForeignKeyConstraint(['role_id'], ['role.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_apikey_prefix'), 'apikey', ['prefix'], unique=False)
    op.create_index(op.f('ix_apikey_workspace_id'), 'apikey', ['workspace_id'], unique=False)
    op.create_table('externaltestreport',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('source', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('suite_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('git_commit', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('git_branch', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('tests', sa.Integer(), nullable=False),
    sa.Column('failures', sa.Integer(), nullable=False),
    sa.Column('errors', sa.Integer(), nullable=False),
    sa.Column('skipped', sa.Integer(), nullable=False),
    sa.Column('time_seconds', sa.Float(), nullable=False),
    sa.Column('failed_cases', sa.JSON(), nullable=True),
    sa.Column('uploaded_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_externaltestreport_created_at'), 'externaltestreport', ['created_at'], unique=False)
    op.create_index(op.f('ix_externaltestreport_git_commit'), 'externaltestreport', ['git_commit'], unique=False)
    op.create_index(op.f('ix_externaltestreport_project_id'), 'externaltestreport', ['project_id'], unique=False)
    op.create_table('failurecluster',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('signature', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('category', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('occurrence_count', sa.Integer(), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(), nullable=False),
    sa.Column('last_run_id', sa.Integer(), nullable=True),
    sa.Column('sample_error', sa.Text(), nullable=True),
    sa.Column('assignee_id', sa.Integer(), nullable=True),
    sa.Column('resolution_note', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['assignee_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'signature', name='uq_cluster_project_signature')
    )
    op.create_index(op.f('ix_failurecluster_project_id'), 'failurecluster', ['project_id'], unique=False)
    op.create_index(op.f('ix_failurecluster_signature'), 'failurecluster', ['signature'], unique=False)
    op.create_table('mobileappbuild',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('platform', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('app_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('version_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('build_number', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('package_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('file_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('file_size', sa.Integer(), nullable=True),
    sa.Column('original_filename', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('notes', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_mobileappbuild_project_id'), 'mobileappbuild', ['project_id'], unique=False)
    op.create_table('persona',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('session_state', sa.JSON(), nullable=True),
    sa.Column('auth_headers', sa.JSON(), nullable=True),
    sa.Column('login_steps', sa.JSON(), nullable=True),
    sa.Column('refresh_after_hours', sa.Integer(), nullable=True),
    sa.Column('last_refreshed_at', sa.DateTime(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_persona_workspace_id'), 'persona', ['workspace_id'], unique=False)
    op.create_table('projectenvironment',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('base_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('variables', sa.JSON(), nullable=True),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'name', name='uq_projectenvironment_project_name')
    )
    op.create_index(op.f('ix_projectenvironment_project_id'), 'projectenvironment', ['project_id'], unique=False)
    op.create_table('projectsecret',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('value_encrypted', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'key', name='uq_projectsecret_project_key')
    )
    op.create_index(op.f('ix_projectsecret_project_id'), 'projectsecret', ['project_id'], unique=False)
    op.create_table('reportschedule',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('cron_expression', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('window_days', sa.Integer(), nullable=False),
    sa.Column('channels', sa.JSON(), nullable=True),
    sa.Column('recipients', sa.JSON(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('next_run_at', sa.DateTime(), nullable=True),
    sa.Column('last_run_at', sa.DateTime(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_reportschedule_project_id'), 'reportschedule', ['project_id'], unique=False)
    op.create_table('securityscan',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('target_url', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('scan_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('authenticated', sa.Boolean(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('requested_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('counts', sa.JSON(), nullable=True),
    sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('openapi_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('auth_header_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('auth_header_value', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.ForeignKeyConstraint(['requested_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_securityscan_project_id'), 'securityscan', ['project_id'], unique=False)
    op.create_table('statuspage',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('slug', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_statuspage_project_id'), 'statuspage', ['project_id'], unique=True)
    op.create_index(op.f('ix_statuspage_slug'), 'statuspage', ['slug'], unique=True)
    op.create_table('teaminvitation',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('invited_by_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['invited_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['team_id'], ['team.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_teaminvitation_email'), 'teaminvitation', ['email'], unique=False)
    op.create_table('teamprojectaccess',
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('access_level', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('role_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.ForeignKeyConstraint(['role_id'], ['role.id'], ),
    sa.ForeignKeyConstraint(['team_id'], ['team.id'], ),
    sa.PrimaryKeyConstraint('team_id', 'project_id')
    )
    op.create_table('testsuite',
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('execution_mode', _EXECUTIONMODE, nullable=True),
    sa.Column('parent_id', sa.Integer(), nullable=True),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('settings', sa.JSON(), nullable=True),
    sa.Column('inherit_settings', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('created_by_agent_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('agent_session_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['parent_id'], ['testsuite.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_testsuite_agent_session_id'), 'testsuite', ['agent_session_id'], unique=False)
    op.create_table('userprojectaccess',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('access_level', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('role_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.ForeignKeyConstraint(['role_id'], ['role.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('user_id', 'project_id')
    )
    op.create_table('userteam',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['team_id'], ['team.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('user_id', 'team_id')
    )
    op.create_table('workspacewebhook',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('url', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('secret', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('event_filter', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_delivery_at', sa.DateTime(), nullable=True),
    sa.Column('last_delivery_status', sa.Integer(), nullable=True),
    sa.Column('failure_count', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_workspacewebhook_workspace_id'), 'workspacewebhook', ['workspace_id'], unique=False)
    op.create_table('testcase',
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('steps', sa.JSON(), nullable=True),
    sa.Column('executor', sa.String(), server_default='ui_playwright', nullable=False),
    sa.Column('raw_script', sa.Text(), nullable=True),
    sa.Column('run_matrix', sa.JSON(), nullable=True),
    sa.Column('test_suite_id', sa.Integer(), nullable=True),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('created_by_agent_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('agent_session_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('code_paths', sa.JSON(), nullable=True),
    sa.Column('is_ai_authored', sa.Boolean(), nullable=False),
    sa.Column('ai_confidence', sa.Float(), nullable=True),
    sa.Column('last_human_reviewed_at', sa.DateTime(), nullable=True),
    sa.Column('last_human_reviewed_by_id', sa.Integer(), nullable=True),
    sa.Column('last_validated_commit', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('last_validated_at', sa.DateTime(), nullable=True),
    sa.Column('is_auth_setup', sa.Boolean(), nullable=False),
    sa.Column('use_auth_session', sa.Boolean(), nullable=False),
    sa.Column('dataset', sa.JSON(), nullable=True),
    sa.Column('tags', sa.JSON(), nullable=True),
    sa.Column('priority', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['last_human_reviewed_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.ForeignKeyConstraint(['test_suite_id'], ['testsuite.id'], ),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_testcase_agent_session_id'), 'testcase', ['agent_session_id'], unique=False)
    op.create_table('authsession',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('storage_state', sa.JSON(), nullable=True),
    sa.Column('captured_by_case_id', sa.Integer(), nullable=True),
    sa.Column('captured_at', sa.DateTime(), nullable=False),
    sa.Column('max_age_minutes', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['captured_by_case_id'], ['testcase.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_authsession_project_id'), 'authsession', ['project_id'], unique=True)
    op.create_table('flakerecord',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('test_case_id', sa.Integer(), nullable=False),
    sa.Column('step_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('flake_score', sa.Float(), nullable=False),
    sa.Column('is_quarantined', sa.Boolean(), nullable=False),
    sa.Column('first_observed_at', sa.DateTime(), nullable=False),
    sa.Column('last_observed_at', sa.DateTime(), nullable=False),
    sa.Column('sample_count', sa.Integer(), nullable=False),
    sa.Column('last_failure_message', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.ForeignKeyConstraint(['test_case_id'], ['testcase.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_flakerecord_is_quarantined'), 'flakerecord', ['is_quarantined'], unique=False)
    op.create_index(op.f('ix_flakerecord_step_id'), 'flakerecord', ['step_id'], unique=False)
    op.create_index(op.f('ix_flakerecord_test_case_id'), 'flakerecord', ['test_case_id'], unique=False)
    op.create_table('requirementlink',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('test_case_id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('ref', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('source', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.ForeignKeyConstraint(['test_case_id'], ['testcase.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('test_case_id', 'ref', name='uq_reqlink_case_ref')
    )
    op.create_index(op.f('ix_requirementlink_project_id'), 'requirementlink', ['project_id'], unique=False)
    op.create_index(op.f('ix_requirementlink_ref'), 'requirementlink', ['ref'], unique=False)
    op.create_index(op.f('ix_requirementlink_test_case_id'), 'requirementlink', ['test_case_id'], unique=False)
    op.create_table('testcaserevision',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('test_case_id', sa.Integer(), nullable=False),
    sa.Column('revision_number', sa.Integer(), nullable=False),
    sa.Column('snapshot', sa.JSON(), nullable=True),
    sa.Column('change_source', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('changed_by_id', sa.Integer(), nullable=True),
    sa.Column('changed_by_agent_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['changed_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['test_case_id'], ['testcase.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_testcaserevision_test_case_id'), 'testcaserevision', ['test_case_id'], unique=False)
    op.create_table('testrun',
    sa.Column('test_suite_id', sa.Integer(), nullable=False),
    sa.Column('test_case_id', sa.Integer(), nullable=True),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('suite_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('test_case_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('local_worker_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('executor', sa.String(), server_default='ui_playwright', nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('finalized_at', sa.DateTime(), nullable=True),
    sa.Column('status', _TESTSTATUS, nullable=False),
    sa.Column('total_tests', sa.Integer(), nullable=False),
    sa.Column('passed_tests', sa.Integer(), nullable=False),
    sa.Column('failed_tests', sa.Integer(), nullable=False),
    sa.Column('duration_ms', sa.Float(), nullable=True),
    sa.Column('error_message', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('trace_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('video_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('har_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('screenshots', sa.JSON(), nullable=True),
    sa.Column('response_status', sa.Integer(), nullable=True),
    sa.Column('request_headers', sa.JSON(), nullable=True),
    sa.Column('request_params', sa.JSON(), nullable=True),
    sa.Column('response_headers', sa.JSON(), nullable=True),
    sa.Column('allowed_domains', sa.JSON(), nullable=True),
    sa.Column('domain_settings', sa.JSON(), nullable=True),
    sa.Column('network_events', sa.JSON(), nullable=True),
    sa.Column('execution_log', sa.JSON(), nullable=True),
    sa.Column('browser', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('device', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('environment_id', sa.Integer(), nullable=True),
    sa.Column('ai_analysis', sa.JSON(), nullable=True),
    sa.Column('git_commit', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('git_branch', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('git_pr_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('git_repo', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('triggered_by', _RUNTRIGGER, nullable=True),
    sa.Column('agent_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('api_key_id', sa.Integer(), nullable=True),
    sa.Column('baseline_run_id', sa.Integer(), nullable=True),
    sa.Column('target_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('persona_id', sa.Integer(), nullable=True),
    sa.Column('app_build_id', sa.Integer(), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['environment_id'], ['projectenvironment.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.ForeignKeyConstraint(['test_case_id'], ['testcase.id'], ),
    sa.ForeignKeyConstraint(['test_suite_id'], ['testsuite.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_testrun_finalized_at'), 'testrun', ['finalized_at'], unique=False)
    op.create_index(op.f('ix_testrun_git_commit'), 'testrun', ['git_commit'], unique=False)
    op.create_index(op.f('ix_testrun_local_worker_id'), 'testrun', ['local_worker_id'], unique=False)
    op.create_table('testschedule',
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('test_suite_id', sa.Integer(), nullable=True),
    sa.Column('test_case_id', sa.Integer(), nullable=True),
    sa.Column('browser', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('device', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('cron_expression', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('next_run_at', sa.DateTime(), nullable=True),
    sa.Column('last_run_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('is_monitor', sa.Boolean(), nullable=False),
    sa.Column('alert_after_failures', sa.Integer(), nullable=False),
    sa.Column('alert_on_recovery', sa.Boolean(), nullable=False),
    sa.Column('alert_emails', sa.JSON(), nullable=True),
    sa.Column('security_scan_target', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('monitor_state', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('last_alert_state', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('last_checked_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.ForeignKeyConstraint(['test_case_id'], ['testcase.id'], ),
    sa.ForeignKeyConstraint(['test_suite_id'], ['testsuite.id'], ),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('usertestcaseaccess',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('test_case_id', sa.Integer(), nullable=False),
    sa.Column('access_level', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.ForeignKeyConstraint(['test_case_id'], ['testcase.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('user_id', 'test_case_id')
    )
    op.create_table('visualbaseline',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('test_case_id', sa.Integer(), nullable=False),
    sa.Column('step_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('browser', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('device', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('viewport', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('image_url', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('mask_regions', sa.JSON(), nullable=True),
    sa.Column('tolerance', sa.Float(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['test_case_id'], ['testcase.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_visualbaseline_step_id'), 'visualbaseline', ['step_id'], unique=False)
    op.create_index(op.f('ix_visualbaseline_test_case_id'), 'visualbaseline', ['test_case_id'], unique=False)
    op.create_table('caseproposal',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('test_suite_id', sa.Integer(), nullable=True),
    sa.Column('target_case_id', sa.Integer(), nullable=True),
    sa.Column('action', _CASEPROPOSALACTION, nullable=True),
    sa.Column('payload', sa.JSON(), nullable=True),
    sa.Column('rationale', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('ai_confidence', sa.Float(), nullable=False),
    sa.Column('agent_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('source_run_id', sa.Integer(), nullable=True),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('decided_at', sa.DateTime(), nullable=True),
    sa.Column('decided_by_id', sa.Integer(), nullable=True),
    sa.Column('decision_note', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_by_agent_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('agent_session_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['decided_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.ForeignKeyConstraint(['source_run_id'], ['testrun.id'], ),
    sa.ForeignKeyConstraint(['target_case_id'], ['testcase.id'], ),
    sa.ForeignKeyConstraint(['test_suite_id'], ['testsuite.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_caseproposal_agent_session_id'), 'caseproposal', ['agent_session_id'], unique=False)
    op.create_index(op.f('ix_caseproposal_project_id'), 'caseproposal', ['project_id'], unique=False)
    op.create_index(op.f('ix_caseproposal_status'), 'caseproposal', ['status'], unique=False)
    op.create_table('issueticket',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('config_id', sa.Integer(), nullable=False),
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('result_id', sa.Integer(), nullable=True),
    sa.Column('cluster_id', sa.Integer(), nullable=True),
    sa.Column('provider', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('external_key', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('summary', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('attachments_uploaded', sa.Integer(), nullable=False),
    sa.Column('attachments_total', sa.Integer(), nullable=False),
    sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['config_id'], ['issuetrackerconfig.id'], ),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['testrun.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_issueticket_cluster_id'), 'issueticket', ['cluster_id'], unique=False)
    op.create_index(op.f('ix_issueticket_config_id'), 'issueticket', ['config_id'], unique=False)
    op.create_index(op.f('ix_issueticket_run_id'), 'issueticket', ['run_id'], unique=False)
    op.create_index(op.f('ix_issueticket_workspace_id'), 'issueticket', ['workspace_id'], unique=False)
    op.create_table('monitorcheck',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('schedule_id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('is_up', sa.Boolean(), nullable=False),
    sa.Column('checked_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['testrun.id'], ),
    sa.ForeignKeyConstraint(['schedule_id'], ['testschedule.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_monitorcheck_checked_at'), 'monitorcheck', ['checked_at'], unique=False)
    op.create_index(op.f('ix_monitorcheck_schedule_id'), 'monitorcheck', ['schedule_id'], unique=False)
    op.create_table('securityfinding',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('scan_id', sa.Integer(), nullable=True),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('scan_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('category', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('severity', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('evidence', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('target_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('assignee_id', sa.Integer(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.Column('fingerprint', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.ForeignKeyConstraint(['assignee_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['testrun.id'], ),
    sa.ForeignKeyConstraint(['scan_id'], ['securityscan.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_securityfinding_fingerprint'), 'securityfinding', ['fingerprint'], unique=False)
    op.create_index(op.f('ix_securityfinding_project_id'), 'securityfinding', ['project_id'], unique=False)
    op.create_index(op.f('ix_securityfinding_run_id'), 'securityfinding', ['run_id'], unique=False)
    op.create_index(op.f('ix_securityfinding_scan_id'), 'securityfinding', ['scan_id'], unique=False)
    op.create_index(op.f('ix_securityfinding_status'), 'securityfinding', ['status'], unique=False)
    op.create_table('selectorhealproposal',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('test_case_id', sa.Integer(), nullable=False),
    sa.Column('step_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('old_selector', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('new_selector', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('intent', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('rationale', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('source_run_id', sa.Integer(), nullable=True),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('decided_at', sa.DateTime(), nullable=True),
    sa.Column('decided_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['decided_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['source_run_id'], ['testrun.id'], ),
    sa.ForeignKeyConstraint(['test_case_id'], ['testcase.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_selectorhealproposal_step_id'), 'selectorhealproposal', ['step_id'], unique=False)
    op.create_index(op.f('ix_selectorhealproposal_test_case_id'), 'selectorhealproposal', ['test_case_id'], unique=False)
    op.create_table('testcaseresult',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('test_run_id', sa.Integer(), nullable=False),
    sa.Column('test_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('status', _TESTSTATUS, nullable=False),
    sa.Column('duration_ms', sa.Float(), nullable=False),
    sa.Column('error_message', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('trace_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('video_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('har_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('screenshots', sa.JSON(), nullable=True),
    sa.Column('response_status', sa.Integer(), nullable=True),
    sa.Column('response_headers', sa.JSON(), nullable=True),
    sa.Column('response_body', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('request_headers', sa.JSON(), nullable=True),
    sa.Column('request_body', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('request_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('request_method', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('request_params', sa.JSON(), nullable=True),
    sa.Column('ai_analysis', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('retry_count', sa.Integer(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('is_flaky', sa.Boolean(), nullable=False),
    sa.Column('result_kind', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('result_payload', sa.JSON(), nullable=True),
    sa.Column('cluster_id', sa.Integer(), nullable=True),
    sa.Column('test_case_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['cluster_id'], ['failurecluster.id'], ),
    sa.ForeignKeyConstraint(['test_case_id'], ['testcase.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['test_run_id'], ['testrun.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_testcaseresult_cluster_id'), 'testcaseresult', ['cluster_id'], unique=False)
    op.create_index(op.f('ix_testcaseresult_test_case_id'), 'testcaseresult', ['test_case_id'], unique=False)

    op.execute(_AUDIT_GUARD_FN)
    for statement in _AUDIT_TRIGGERS:
        op.execute(statement)


def downgrade() -> None:
    # Triggers go with their table; the function does not.
    op.execute("DROP FUNCTION IF EXISTS traceiq_auditlog_append_only() CASCADE")
    op.drop_index(op.f('ix_testcaseresult_test_case_id'), table_name='testcaseresult')
    op.drop_index(op.f('ix_testcaseresult_cluster_id'), table_name='testcaseresult')
    op.drop_table('testcaseresult')
    op.drop_index(op.f('ix_selectorhealproposal_test_case_id'), table_name='selectorhealproposal')
    op.drop_index(op.f('ix_selectorhealproposal_step_id'), table_name='selectorhealproposal')
    op.drop_table('selectorhealproposal')
    op.drop_index(op.f('ix_securityfinding_status'), table_name='securityfinding')
    op.drop_index(op.f('ix_securityfinding_scan_id'), table_name='securityfinding')
    op.drop_index(op.f('ix_securityfinding_run_id'), table_name='securityfinding')
    op.drop_index(op.f('ix_securityfinding_project_id'), table_name='securityfinding')
    op.drop_index(op.f('ix_securityfinding_fingerprint'), table_name='securityfinding')
    op.drop_table('securityfinding')
    op.drop_index(op.f('ix_monitorcheck_schedule_id'), table_name='monitorcheck')
    op.drop_index(op.f('ix_monitorcheck_checked_at'), table_name='monitorcheck')
    op.drop_table('monitorcheck')
    op.drop_index(op.f('ix_issueticket_workspace_id'), table_name='issueticket')
    op.drop_index(op.f('ix_issueticket_run_id'), table_name='issueticket')
    op.drop_index(op.f('ix_issueticket_config_id'), table_name='issueticket')
    op.drop_index(op.f('ix_issueticket_cluster_id'), table_name='issueticket')
    op.drop_table('issueticket')
    op.drop_index(op.f('ix_caseproposal_status'), table_name='caseproposal')
    op.drop_index(op.f('ix_caseproposal_project_id'), table_name='caseproposal')
    op.drop_index(op.f('ix_caseproposal_agent_session_id'), table_name='caseproposal')
    op.drop_table('caseproposal')
    op.drop_index(op.f('ix_visualbaseline_test_case_id'), table_name='visualbaseline')
    op.drop_index(op.f('ix_visualbaseline_step_id'), table_name='visualbaseline')
    op.drop_table('visualbaseline')
    op.drop_table('usertestcaseaccess')
    op.drop_table('testschedule')
    op.drop_index(op.f('ix_testrun_local_worker_id'), table_name='testrun')
    op.drop_index(op.f('ix_testrun_git_commit'), table_name='testrun')
    op.drop_index(op.f('ix_testrun_finalized_at'), table_name='testrun')
    op.drop_table('testrun')
    op.drop_index(op.f('ix_testcaserevision_test_case_id'), table_name='testcaserevision')
    op.drop_table('testcaserevision')
    op.drop_index(op.f('ix_requirementlink_test_case_id'), table_name='requirementlink')
    op.drop_index(op.f('ix_requirementlink_ref'), table_name='requirementlink')
    op.drop_index(op.f('ix_requirementlink_project_id'), table_name='requirementlink')
    op.drop_table('requirementlink')
    op.drop_index(op.f('ix_flakerecord_test_case_id'), table_name='flakerecord')
    op.drop_index(op.f('ix_flakerecord_step_id'), table_name='flakerecord')
    op.drop_index(op.f('ix_flakerecord_is_quarantined'), table_name='flakerecord')
    op.drop_table('flakerecord')
    op.drop_index(op.f('ix_authsession_project_id'), table_name='authsession')
    op.drop_table('authsession')
    op.drop_index(op.f('ix_testcase_agent_session_id'), table_name='testcase')
    op.drop_table('testcase')
    op.drop_index(op.f('ix_workspacewebhook_workspace_id'), table_name='workspacewebhook')
    op.drop_table('workspacewebhook')
    op.drop_table('userteam')
    op.drop_table('userprojectaccess')
    op.drop_index(op.f('ix_testsuite_agent_session_id'), table_name='testsuite')
    op.drop_table('testsuite')
    op.drop_table('teamprojectaccess')
    op.drop_index(op.f('ix_teaminvitation_email'), table_name='teaminvitation')
    op.drop_table('teaminvitation')
    op.drop_index(op.f('ix_statuspage_slug'), table_name='statuspage')
    op.drop_index(op.f('ix_statuspage_project_id'), table_name='statuspage')
    op.drop_table('statuspage')
    op.drop_index(op.f('ix_securityscan_project_id'), table_name='securityscan')
    op.drop_table('securityscan')
    op.drop_index(op.f('ix_reportschedule_project_id'), table_name='reportschedule')
    op.drop_table('reportschedule')
    op.drop_index(op.f('ix_projectsecret_project_id'), table_name='projectsecret')
    op.drop_table('projectsecret')
    op.drop_index(op.f('ix_projectenvironment_project_id'), table_name='projectenvironment')
    op.drop_table('projectenvironment')
    op.drop_index(op.f('ix_persona_workspace_id'), table_name='persona')
    op.drop_table('persona')
    op.drop_index(op.f('ix_mobileappbuild_project_id'), table_name='mobileappbuild')
    op.drop_table('mobileappbuild')
    op.drop_index(op.f('ix_failurecluster_signature'), table_name='failurecluster')
    op.drop_index(op.f('ix_failurecluster_project_id'), table_name='failurecluster')
    op.drop_table('failurecluster')
    op.drop_index(op.f('ix_externaltestreport_project_id'), table_name='externaltestreport')
    op.drop_index(op.f('ix_externaltestreport_git_commit'), table_name='externaltestreport')
    op.drop_index(op.f('ix_externaltestreport_created_at'), table_name='externaltestreport')
    op.drop_table('externaltestreport')
    op.drop_index(op.f('ix_apikey_workspace_id'), table_name='apikey')
    op.drop_index(op.f('ix_apikey_prefix'), table_name='apikey')
    op.drop_table('apikey')
    op.drop_index(op.f('ix_workspacesubscription_workspace_id'), table_name='workspacesubscription')
    op.drop_table('workspacesubscription')
    op.drop_index(op.f('ix_workspaceinvitation_token'), table_name='workspaceinvitation')
    op.drop_index(op.f('ix_workspaceinvitation_email'), table_name='workspaceinvitation')
    op.drop_table('workspaceinvitation')
    op.drop_table('userworkspace')
    op.drop_table('usersystemrole')
    op.drop_index(op.f('ix_usagerecord_workspace_id'), table_name='usagerecord')
    op.drop_index(op.f('ix_usagerecord_period'), table_name='usagerecord')
    op.drop_table('usagerecord')
    op.drop_index(op.f('ix_team_scim_external_id'), table_name='team')
    op.drop_table('team')
    op.drop_table('rolepermission')
    op.drop_table('project')
    op.drop_index(op.f('ix_llmusageevent_workspace_id'), table_name='llmusageevent')
    op.drop_index(op.f('ix_llmusageevent_run_id'), table_name='llmusageevent')
    op.drop_index(op.f('ix_llmusageevent_provider'), table_name='llmusageevent')
    op.drop_index(op.f('ix_llmusageevent_project_id'), table_name='llmusageevent')
    op.drop_index(op.f('ix_llmusageevent_model'), table_name='llmusageevent')
    op.drop_index(op.f('ix_llmusageevent_feature'), table_name='llmusageevent')
    op.drop_index(op.f('ix_llmusageevent_created_at'), table_name='llmusageevent')
    op.drop_table('llmusageevent')
    op.drop_index(op.f('ix_issuetrackerconfig_workspace_id'), table_name='issuetrackerconfig')
    op.drop_table('issuetrackerconfig')
    op.drop_table('workspace')
    op.drop_table('role')
    op.drop_index(op.f('ix_user_settings_user_id'), table_name='user_settings')
    op.drop_table('user_settings')
    op.drop_index(op.f('ix_tenant_name'), table_name='tenant')
    op.drop_table('tenant')
    op.drop_index(op.f('ix_refreshtoken_user_id'), table_name='refreshtoken')
    op.drop_index(op.f('ix_refreshtoken_hashed_token'), table_name='refreshtoken')
    op.drop_index(op.f('ix_refreshtoken_family_id'), table_name='refreshtoken')
    op.drop_table('refreshtoken')
    op.drop_index(op.f('ix_mfarecoverycode_user_id'), table_name='mfarecoverycode')
    op.drop_index(op.f('ix_mfarecoverycode_code_hash'), table_name='mfarecoverycode')
    op.drop_table('mfarecoverycode')
    op.drop_index(op.f('ix_llm_provider_config_name'), table_name='llm_provider_config')
    op.drop_index(op.f('ix_llm_provider_config_is_active'), table_name='llm_provider_config')
    op.drop_table('llm_provider_config')
    op.drop_index(op.f('ix_instance_settings_key'), table_name='instance_settings')
    op.drop_table('instance_settings')
    op.drop_index(op.f('ix_accounttoken_user_id'), table_name='accounttoken')
    op.drop_index(op.f('ix_accounttoken_purpose'), table_name='accounttoken')
    op.drop_index(op.f('ix_accounttoken_hashed_token'), table_name='accounttoken')
    op.drop_table('accounttoken')
    op.drop_index(op.f('ix_users_scim_external_id'), table_name='users')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_plan_name'), table_name='plan')
    op.drop_table('plan')
    op.drop_table('permission')
    op.drop_index('ix_auditlog_workspace_timestamp', table_name='auditlog')
    op.drop_index(op.f('ix_auditlog_workspace_id'), table_name='auditlog')
    op.drop_index(op.f('ix_auditlog_user_id'), table_name='auditlog')
    op.drop_index(op.f('ix_auditlog_row_hash'), table_name='auditlog')
    op.drop_table('auditlog')

    bind = op.get_bind()
    for enum_type in _ENUM_TYPES:
        enum_type.drop(bind, checkfirst=True)
