"""Test-case revision history.

`record_revision` appends an immutable post-change snapshot of a case; the
restore endpoint copies a snapshot back onto the live case (appending yet
another revision, so history is never rewritten). Failures here must never
break the mutation being recorded — versioning is a safety net, not a gate.
"""
from typing import Any, Dict, Optional

from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import TestCase, TestCaseRevision

# The editable state captured per revision. Deliberately excludes ownership /
# provenance columns (created_by, agent ids) — those describe the row, not
# the test — and runtime metadata like last_human_reviewed_at.
SNAPSHOT_FIELDS = (
    "name",
    "steps",
    "executor",
    "raw_script",
    "test_suite_id",
    "is_auth_setup",
    "use_auth_session",
    "dataset",
    "tags",
    "priority",
    "code_paths",
)


def snapshot_case(case: TestCase) -> Dict[str, Any]:
    """JSON-safe snapshot of the case's editable state."""
    snap: Dict[str, Any] = {}
    for field in SNAPSHOT_FIELDS:
        value = getattr(case, field, None)
        if field == "steps":
            value = [
                s.dict() if hasattr(s, "dict") else s
                for s in (value or [])
            ]
        elif hasattr(value, "value"):  # enums (executor)
            value = value.value
        snap[field] = value
    return snap


async def record_revision(
    session: AsyncSession,
    case: TestCase,
    source: str,
    user_id: Optional[int] = None,
    agent_id: Optional[str] = None,
) -> Optional[TestCaseRevision]:
    """Append a revision for `case`'s current state. Best-effort: returns
    None (and logs) rather than raising, so a history failure can't fail
    the save it documents. Caller commits."""
    try:
        result = await session.exec(
            select(func.max(TestCaseRevision.revision_number)).where(
                TestCaseRevision.test_case_id == case.id
            )
        )
        latest = result.one_or_none() or 0
        revision = TestCaseRevision(
            test_case_id=case.id,
            revision_number=int(latest) + 1,
            snapshot=snapshot_case(case),
            change_source=source,
            changed_by_id=user_id,
            changed_by_agent_id=agent_id,
        )
        session.add(revision)
        return revision
    except Exception as exc:  # noqa: BLE001
        print(f"[CaseRevisions] failed to record revision for case {case.id}: {exc}")
        return None


def apply_snapshot(case: TestCase, snapshot: Dict[str, Any]) -> None:
    """Copy a snapshot's fields back onto the live case (restore)."""
    for field in SNAPSHOT_FIELDS:
        if field in snapshot:
            setattr(case, field, snapshot[field])
