"""Audit trail: one write path, tamper-evident.

Before this, `AuditLog(...)` was constructed inline at seventeen call sites,
each deciding for itself what to record. The result was predictable: no
authentication events at all, no record of run deletion (which also deletes
MinIO artifacts), nothing for API-key or role changes, and no actor context
beyond a user id. `workspace_service.delete_workspace` additionally *rewrote*
existing rows, which is the one thing an audit table must never permit.

Two mechanisms, and they are complementary rather than redundant:

*A database trigger* (see the migration) rejects UPDATE and DELETE on the
table. That stops every ordinary path — application bugs, an ORM cascade, a
careless script.

*A hash chain* makes any edit that does land detectable afterwards. A trigger
can be dropped by whoever owns the database; a superuser, a restore from a
doctored dump, or a direct `ALTER TABLE ... DISABLE TRIGGER` all bypass it.
Each row commits to its predecessor, so altering row N invalidates every row
after it and `verify_chain` can say exactly where.

Neither alone is enough. A trigger you can drop is not evidence; a chain nobody
verifies is not protection.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: `prev_hash` of the first row in a chain.
ROOT_HASH = "0" * 64


def _canonical(changes: Optional[dict]) -> str:
    """Stable serialisation of the payload.

    Sorted keys because a JSON column does not preserve insertion order — a
    chain that broke on re-serialisation would cry tampering on every honest
    read, and an alarm that fires constantly is an alarm nobody looks at.
    None and {} both mean "nothing recorded" and must hash identically, since
    the ORM writes {} where a caller passed None.
    """
    return json.dumps(changes or {}, sort_keys=True, separators=(",", ":"), default=str)


def chain_hash(
    *,
    entity_type: str,
    entity_id: Any,
    action: str,
    user_id: Optional[int],
    workspace_id: Optional[int] = None,
    timestamp: Optional[datetime] = None,
    changes: Optional[dict] = None,
    prev_hash: Optional[str] = None,
    **_ignored: Any,
) -> str:
    """SHA-256 over the row's meaningful content plus its predecessor.

    `**_ignored` lets callers pass a whole row dict — including a `row_hash`
    already on it — without having to strip fields first.
    """
    material = "|".join([
        str(entity_type or ""),
        str(entity_id if entity_id is not None else ""),
        str(action or ""),
        str(user_id if user_id is not None else ""),
        str(workspace_id if workspace_id is not None else ""),
        timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp or ""),
        _canonical(changes),
        str(prev_hash or ROOT_HASH),
    ])
    return hashlib.sha256(material.encode()).hexdigest()


def _actor_from(principal: Any, user_id: Optional[int]) -> Dict[str, Any]:
    """Derive actor columns from an AuthPrincipal, if one was supplied."""
    if principal is None:
        return {"user_id": user_id, "actor_type": "user" if user_id else "system",
                "actor_label": None}
    api_key = getattr(principal, "api_key", None)
    agent_id = getattr(principal, "agent_id", None)
    user = getattr(principal, "user", None)
    resolved = user_id if user_id is not None else getattr(user, "id", None)
    if api_key is not None:
        return {"user_id": resolved, "actor_type": "api_key",
                "actor_label": getattr(api_key, "prefix", None)}
    if agent_id:
        return {"user_id": resolved, "actor_type": "agent", "actor_label": str(agent_id)}
    return {"user_id": resolved, "actor_type": "user",
            "actor_label": getattr(user, "email", None)}


def _request_context(request: Any) -> Dict[str, Optional[str]]:
    if request is None:
        return {"ip_address": None, "user_agent": None}
    try:
        client = getattr(request, "client", None)
        # X-Forwarded-For first hop, since TraceIQ usually sits behind nginx.
        forwarded = request.headers.get("x-forwarded-for") if hasattr(request, "headers") else None
        ip = (forwarded.split(",")[0].strip() if forwarded
              else (getattr(client, "host", None) if client else None))
        agent = request.headers.get("user-agent") if hasattr(request, "headers") else None
        return {"ip_address": ip, "user_agent": (agent or None)}
    except Exception:  # noqa: BLE001 — context is a nicety, never a failure
        return {"ip_address": None, "user_agent": None}


def build_entry(
    *,
    entity_type: str,
    entity_id: Any,
    action: str,
    session: Any,
    user_id: Optional[int] = None,
    workspace_id: Optional[int] = None,
    changes: Optional[dict] = None,
    principal: Any = None,
    request: Any = None,
    redact: bool = True,
) -> Any:
    """Construct a chained `AuditLog` row. Caller adds it and commits.

    Sync and async sessions differ enough that fetching the previous hash is
    the caller's concern in async paths; `record()` and `record_sync()` wrap
    this with the right lookup.
    """
    from app.models import AuditLog

    if redact:
        from app.services.redaction import redact_audit_changes
        changes = redact_audit_changes(changes)

    actor = _actor_from(principal, user_id)
    context = _request_context(request)
    timestamp = datetime.utcnow()

    entry = AuditLog(
        entity_type=entity_type,
        entity_id=int(entity_id) if entity_id is not None else 0,
        action=action,
        workspace_id=workspace_id,
        timestamp=timestamp,
        changes=changes or {},
        **actor,
        **context,
    )
    return entry


def seal(entry: Any, prev_hash: Optional[str]) -> Any:
    """Attach the chain fields to a built entry."""
    entry.prev_hash = prev_hash or ROOT_HASH
    entry.row_hash = chain_hash(
        entity_type=entry.entity_type,
        entity_id=entry.entity_id,
        action=entry.action,
        user_id=entry.user_id,
        workspace_id=entry.workspace_id,
        timestamp=entry.timestamp,
        changes=entry.changes,
        prev_hash=entry.prev_hash,
    )
    return entry


def _latest_hash_sync(session: Any) -> Optional[str]:
    from sqlmodel import select
    from app.models import AuditLog
    row = session.exec(
        select(AuditLog.row_hash).order_by(AuditLog.id.desc()).limit(1)
    ).first()
    return row if isinstance(row, str) else (row[0] if row else None)


async def _latest_hash_async(session: Any) -> Optional[str]:
    from sqlmodel import select
    from app.models import AuditLog
    result = await session.exec(
        select(AuditLog.row_hash).order_by(AuditLog.id.desc()).limit(1)
    )
    row = result.first()
    return row if isinstance(row, str) else (row[0] if row else None)


async def record(session: Any, **kwargs: Any) -> Any:
    """Append one audit entry (async session). Caller commits.

    Never raises: a failure to record must not fail the operation it documents,
    because the alternative is that people disable auditing to get work done.
    Failures are printed loudly instead.
    """
    try:
        entry = build_entry(session=session, **kwargs)
        seal(entry, await _latest_hash_async(session))
        session.add(entry)
        return entry
    except Exception as exc:  # noqa: BLE001
        print(f"[Audit] FAILED to record {kwargs.get('action')} on "
              f"{kwargs.get('entity_type')}: {exc}")
        return None


def record_sync(session: Any, **kwargs: Any) -> Any:
    """Append one audit entry (sync session). Caller commits."""
    try:
        entry = build_entry(session=session, **kwargs)
        seal(entry, _latest_hash_sync(session))
        session.add(entry)
        return entry
    except Exception as exc:  # noqa: BLE001
        print(f"[Audit] FAILED to record {kwargs.get('action')} on "
              f"{kwargs.get('entity_type')}: {exc}")
        return None


# The append-only guard, as DDL.
#
# This is duplicated in migration c8d9e0f1a2b3 on purpose, and the duplication
# is load-bearing rather than sloppy. The two paths that create this schema do
# not overlap:
#
#   - an EXISTING database is upgraded by Alembic, which runs the migration;
#   - a NEW database is built by scripts/bootstrap_db.py, which calls
#     SQLModel.metadata.create_all() and then stamps head WITHOUT running any
#     migration (the Alembic baseline is an empty stub — see CLAUDE.md).
#
# So a fresh install would get the table and no trigger, silently losing the
# guarantee on exactly the deployments most likely to be audited. Attaching it
# to the table's after_create event covers that path too.
APPEND_ONLY_GUARD_SQL = """
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

# One statement per entry: asyncpg uses prepared statements and rejects
# multiple commands in a single execute ("cannot insert multiple commands into
# a prepared statement").
APPEND_ONLY_TRIGGER_STATEMENTS = (
    "DROP TRIGGER IF EXISTS traceiq_auditlog_no_update ON auditlog",
    "CREATE TRIGGER traceiq_auditlog_no_update"
    " BEFORE UPDATE ON auditlog"
    " FOR EACH ROW EXECUTE FUNCTION traceiq_auditlog_append_only()",
    "DROP TRIGGER IF EXISTS traceiq_auditlog_no_delete ON auditlog",
    "CREATE TRIGGER traceiq_auditlog_no_delete"
    " BEFORE DELETE ON auditlog"
    " FOR EACH ROW EXECUTE FUNCTION traceiq_auditlog_append_only()",
)


def install_append_only_ddl() -> None:
    """Emit the guard whenever the auditlog table is created from metadata."""
    from sqlalchemy import DDL, event
    from app.models import AuditLog

    table = AuditLog.__table__
    # SQLAlchemy's DDL() applies %-style interpolation to its text, and the
    # RAISE EXCEPTION lines contain literal `%` placeholders for plpgsql.
    # Without doubling them, create_all fails with
    # "%i format: a real number is required, not dict".
    # PostgreSQL only: SQLite has no plpgsql, and emitting this there would
    # break table creation outright.
    for statement in (APPEND_ONLY_GUARD_SQL, *APPEND_ONLY_TRIGGER_STATEMENTS):
        event.listen(
            table, "after_create",
            DDL(statement.replace("%", "%%")).execute_if(dialect="postgresql"),
        )


_listener_installed = False


def install_chain_listener() -> None:
    """Seal every AuditLog row automatically as it is flushed.

    There are seventeen places that construct `AuditLog(...)` inline, across
    sync and async sessions and with differing shapes. Rewriting all of them to
    call `record()` would be a large, risky diff — and would still leave the
    chain breakable by the eighteenth one somebody writes next month.

    A `before_flush` hook is the durable version: any audit row, however it was
    created, gets chained. `record()` remains the preferred API because it also
    captures actor and request context, which a bare constructor cannot.

    Rows are sealed in insertion order within the flush, so several audit
    entries committed together still form a valid sequence.
    """
    global _listener_installed
    if _listener_installed:
        return
    _listener_installed = True

    from sqlalchemy import event
    from sqlalchemy.orm import Session as SASession
    from sqlmodel import select
    from app.models import AuditLog

    @event.listens_for(SASession, "before_flush")
    def _seal_audit_rows(session, flush_context, instances):  # noqa: ANN001
        pending = [obj for obj in session.new
                   if isinstance(obj, AuditLog) and not obj.row_hash]
        if not pending:
            return

        # no_autoflush: querying inside before_flush would otherwise recurse
        # into the flush we are already in.
        with session.no_autoflush:
            last = session.execute(
                select(AuditLog.row_hash)
                .where(AuditLog.row_hash.is_not(None))
                .order_by(AuditLog.id.desc()).limit(1)
            ).scalars().first()

        prev = last or ROOT_HASH
        for entry in pending:
            if entry.timestamp is None:
                entry.timestamp = datetime.utcnow()
            seal(entry, prev)
            prev = entry.row_hash


def verify_chain(rows: Sequence[Any]) -> Tuple[bool, Optional[int]]:
    """Check a chain in ascending id order.

    Returns `(ok, index_of_first_bad_row)`. Rows may be dicts or ORM objects.

    Rows predating the chain (no `row_hash`) verify as FALSE, not True: an
    unverifiable row is not an intact one, and reporting it as verified would
    make the whole mechanism a rubber stamp for exactly the period an attacker
    would target.
    """
    def field(row: Any, name: str) -> Any:
        return row.get(name) if isinstance(row, dict) else getattr(row, name, None)

    expected_prev = ROOT_HASH
    for index, row in enumerate(rows):
        stored = field(row, "row_hash")
        if not stored:
            return False, index
        if (field(row, "prev_hash") or ROOT_HASH) != expected_prev:
            return False, index

        recomputed = chain_hash(
            entity_type=field(row, "entity_type"),
            entity_id=field(row, "entity_id"),
            action=field(row, "action"),
            user_id=field(row, "user_id"),
            workspace_id=field(row, "workspace_id"),
            timestamp=field(row, "timestamp"),
            changes=field(row, "changes"),
            prev_hash=field(row, "prev_hash"),
        )
        if recomputed != stored:
            return False, index
        expected_prev = stored

    return True, None
