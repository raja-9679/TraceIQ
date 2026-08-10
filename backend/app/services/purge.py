"""Cascading workspace purge — workstream G1.

`workspace_service.delete_workspace` deleted the workspace row and its teams and
nothing else. Projects, suites, cases, runs, results, secrets, personas,
baselines, app builds, webhooks, API keys and every MinIO object survived as
orphans: unreachable through the API, still holding customer data, still
answering "no" to "have you deleted our data?".

Two things make this maintainable rather than a 39-line list that rots:

1. **Deletion is declared as an ordered plan** (`PURGE_PLAN`), one entry per
   table, each a DELETE scoped to the workspace through its own join path.
   Order is dependents-first so no foreign key is ever violated.

2. **A test walks the FK graph in `SQLModel.metadata`** and fails if any table
   that can reach `workspace` is neither in the plan nor explicitly exempt. A
   new table added later cannot silently leak — the test breaks. That test is
   the actual guarantee here; this file is just the implementation of it.

`auditlog` is exempt on purpose. It is append-only (a trigger rejects UPDATE
always and DELETE except for the retention task), it deliberately has no foreign
keys to workspace or users, and its whole value is outliving the objects it
describes. "The workspace that was deleted" is a question an auditor asks, and
the answer has to survive the deletion. Audit rows age out under
`AUDIT_RETENTION_DAYS` instead.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Tables that can reach `workspace` through a foreign key but are deliberately
# NOT purged. Each needs a reason, because the completeness test reads this as
# an assertion that the omission was considered.
PURGE_EXEMPT: Dict[str, str] = {
    "auditlog": "append-only history must outlive the objects it describes; "
                "ages out under AUDIT_RETENTION_DAYS instead",
}


@dataclass(frozen=True)
class PurgeStep:
    """One DELETE, scoped to a workspace. `sql` takes a :workspace_id bind."""
    table: str
    sql: str
    label: str = ""


# Scoping subqueries, written once. Nesting them keeps every step independent of
# the order the id sets happen to be gathered in.
_PROJECTS = "SELECT id FROM project WHERE workspace_id = :workspace_id"
_TEAMS = "SELECT id FROM team WHERE workspace_id = :workspace_id"
_SUITES = f"SELECT id FROM testsuite WHERE project_id IN ({_PROJECTS})"
_CASES = f"SELECT id FROM testcase WHERE project_id IN ({_PROJECTS})"
_RUNS = f"SELECT id FROM testrun WHERE project_id IN ({_PROJECTS})"
_SCANS = f"SELECT id FROM securityscan WHERE project_id IN ({_PROJECTS})"

# Dependents first. Within a level the order does not matter.
PURGE_PLAN: List[PurgeStep] = [
    # --- leaves hanging off runs / cases / scans ---
    PurgeStep("securityfinding",
              f"DELETE FROM securityfinding WHERE scan_id IN ({_SCANS}) "
              f"OR project_id IN ({_PROJECTS}) OR run_id IN ({_RUNS})"),
    PurgeStep("monitorcheck", f"DELETE FROM monitorcheck WHERE run_id IN ({_RUNS})"),
    PurgeStep("issueticket",
              f"DELETE FROM issueticket WHERE workspace_id = :workspace_id "
              f"OR run_id IN ({_RUNS})"),
    PurgeStep("testcaseresult",
              f"DELETE FROM testcaseresult WHERE test_run_id IN ({_RUNS}) "
              f"OR test_case_id IN ({_CASES})"),
    PurgeStep("selectorhealproposal",
              f"DELETE FROM selectorhealproposal WHERE source_run_id IN ({_RUNS}) "
              f"OR test_case_id IN ({_CASES})"),
    PurgeStep("caseproposal",
              f"DELETE FROM caseproposal WHERE project_id IN ({_PROJECTS})"),
    PurgeStep("flakerecord", f"DELETE FROM flakerecord WHERE test_case_id IN ({_CASES})"),
    PurgeStep("testcaserevision",
              f"DELETE FROM testcaserevision WHERE test_case_id IN ({_CASES})"),
    PurgeStep("visualbaseline",
              f"DELETE FROM visualbaseline WHERE test_case_id IN ({_CASES})"),
    PurgeStep("usertestcaseaccess",
              f"DELETE FROM usertestcaseaccess WHERE test_case_id IN ({_CASES})"),
    PurgeStep("requirementlink",
              f"DELETE FROM requirementlink WHERE project_id IN ({_PROJECTS}) "
              f"OR test_case_id IN ({_CASES})"),
    PurgeStep("authsession",
              f"DELETE FROM authsession WHERE project_id IN ({_PROJECTS}) "
              f"OR captured_by_case_id IN ({_CASES})"),
    PurgeStep("failurecluster",
              f"DELETE FROM failurecluster WHERE project_id IN ({_PROJECTS})"),

    # --- runs and schedules (reference suites/cases/projects) ---
    PurgeStep("testschedule",
              f"DELETE FROM testschedule WHERE project_id IN ({_PROJECTS})"),
    PurgeStep("testrun", f"DELETE FROM testrun WHERE project_id IN ({_PROJECTS})",
              label="runs"),

    # --- the case/suite tree. Suites are self-referential (parent_id), so the
    # whole set goes in one statement rather than leaf-first. ---
    PurgeStep("testcase", f"DELETE FROM testcase WHERE project_id IN ({_PROJECTS})",
              label="cases"),
    PurgeStep("testsuite", f"DELETE FROM testsuite WHERE project_id IN ({_PROJECTS})",
              label="suites"),

    # --- project-level configuration ---
    PurgeStep("securityscan", f"DELETE FROM securityscan WHERE project_id IN ({_PROJECTS})"),
    PurgeStep("externaltestreport",
              f"DELETE FROM externaltestreport WHERE project_id IN ({_PROJECTS})"),
    PurgeStep("mobileappbuild",
              f"DELETE FROM mobileappbuild WHERE project_id IN ({_PROJECTS})"),
    PurgeStep("projectsecret",
              f"DELETE FROM projectsecret WHERE project_id IN ({_PROJECTS})"),
    PurgeStep("projectenvironment",
              f"DELETE FROM projectenvironment WHERE project_id IN ({_PROJECTS})"),
    PurgeStep("reportschedule",
              f"DELETE FROM reportschedule WHERE project_id IN ({_PROJECTS})"),
    PurgeStep("statuspage", f"DELETE FROM statuspage WHERE project_id IN ({_PROJECTS})"),
    PurgeStep("persona",
              f"DELETE FROM persona WHERE workspace_id = :workspace_id "
              f"OR project_id IN ({_PROJECTS})"),
    PurgeStep("userprojectaccess",
              f"DELETE FROM userprojectaccess WHERE project_id IN ({_PROJECTS})"),
    PurgeStep("teamprojectaccess",
              f"DELETE FROM teamprojectaccess WHERE project_id IN ({_PROJECTS}) "
              f"OR team_id IN ({_TEAMS})"),
    PurgeStep("workspacewebhook",
              f"DELETE FROM workspacewebhook WHERE workspace_id = :workspace_id "
              f"OR project_id IN ({_PROJECTS})"),
    PurgeStep("apikey",
              f"DELETE FROM apikey WHERE workspace_id = :workspace_id "
              f"OR project_id IN ({_PROJECTS})",
              label="api keys"),

    # --- projects ---
    PurgeStep("project", "DELETE FROM project WHERE workspace_id = :workspace_id",
              label="projects"),

    # --- teams and membership ---
    PurgeStep("teaminvitation", f"DELETE FROM teaminvitation WHERE team_id IN ({_TEAMS})"),
    PurgeStep("userteam", f"DELETE FROM userteam WHERE team_id IN ({_TEAMS})"),
    PurgeStep("team", "DELETE FROM team WHERE workspace_id = :workspace_id",
              label="teams"),

    # --- workspace-level rows ---
    PurgeStep("workspaceinvitation",
              "DELETE FROM workspaceinvitation WHERE workspace_id = :workspace_id"),
    PurgeStep("userworkspace",
              "DELETE FROM userworkspace WHERE workspace_id = :workspace_id",
              label="members"),
    PurgeStep("issuetrackerconfig",
              "DELETE FROM issuetrackerconfig WHERE workspace_id = :workspace_id"),
    PurgeStep("llmusageevent",
              "DELETE FROM llmusageevent WHERE workspace_id = :workspace_id"),
    PurgeStep("usagerecord",
              "DELETE FROM usagerecord WHERE workspace_id = :workspace_id"),
    PurgeStep("workspacesubscription",
              "DELETE FROM workspacesubscription WHERE workspace_id = :workspace_id"),

    # --- finally the workspace itself ---
    PurgeStep("workspace", "DELETE FROM workspace WHERE id = :workspace_id"),
]


@dataclass
class PurgeReport:
    workspace_id: int
    workspace_name: str
    dry_run: bool
    rows: Dict[str, int] = field(default_factory=dict)
    objects_deleted: int = 0
    object_errors: List[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(self.rows.values())

    def as_dict(self) -> dict:
        return {
            "workspace_id": self.workspace_id,
            "workspace_name": self.workspace_name,
            "dry_run": self.dry_run,
            "rows_deleted": self.rows,
            "total_rows_deleted": self.total_rows,
            "objects_deleted": self.objects_deleted,
            "object_errors": self.object_errors,
            "retained": {
                table: reason for table, reason in PURGE_EXEMPT.items()
            },
        }


async def _count(session, sql: str, workspace_id: int) -> int:
    """Row count a DELETE *would* affect, for dry runs."""
    from sqlalchemy import text

    counted = sql.replace("DELETE FROM ", "SELECT count(*) FROM ", 1)
    result = await session.exec(text(counted), params={"workspace_id": workspace_id})
    return int(result.one()[0] if hasattr(result, "one") else result.scalar() or 0)


async def collect_object_keys(session, workspace_id: int) -> List[str]:
    """MinIO prefixes belonging to this workspace, gathered BEFORE the rows go.

    Object keys are derived from run and build ids, so they are unrecoverable
    once the rows are deleted. Missing this ordering is how a "purge" leaves
    every video and trace sitting in the bucket.
    """
    from sqlalchemy import text

    prefixes: List[str] = []
    runs = await session.exec(text(f"SELECT id FROM ({_RUNS}) r"),
                              params={"workspace_id": workspace_id})
    for (run_id,) in runs.all():
        prefixes.append(f"runs/{run_id}/")

    builds = await session.exec(
        text("SELECT file_key FROM mobileappbuild WHERE project_id IN "
             f"({_PROJECTS}) AND file_key IS NOT NULL"),
        params={"workspace_id": workspace_id})
    prefixes.extend(key for (key,) in builds.all() if key)

    # VisualBaseline.image_url holds either a bare object key (promoted
    # baselines under baselines/) or a full URL for externally hosted images.
    # Only the former is ours to delete — see _resolve_image_url in
    # api/visual_baselines.py, which makes the same distinction.
    baselines = await session.exec(
        text("SELECT image_url FROM visualbaseline WHERE test_case_id IN "
             f"({_CASES}) AND image_url IS NOT NULL"),
        params={"workspace_id": workspace_id})
    prefixes.extend(key for (key,) in baselines.all()
                    if key and not key.startswith(("http://", "https://")))
    return prefixes


async def purge_workspace(session, workspace_id: int, *, dry_run: bool = False,
                          actor_id: Optional[int] = None,
                          request=None) -> PurgeReport:
    """Delete a workspace and everything reachable from it.

    Audit rows are kept (see PURGE_EXEMPT) and a `purge` entry is appended
    BEFORE the deletion, so the history records that it happened even though the
    workspace it points at no longer exists.

    `dry_run=True` counts instead of deleting and rolls nothing back — it never
    issues a DELETE at all.
    """
    from sqlalchemy import text

    from app.models import Workspace

    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise ValueError(f"Workspace {workspace_id} does not exist")
    report = PurgeReport(workspace_id=workspace_id,
                         workspace_name=workspace.name, dry_run=dry_run)

    object_keys = await collect_object_keys(session, workspace_id)

    if dry_run:
        for step in PURGE_PLAN:
            count = await _count(session, step.sql, workspace_id)
            if count:
                report.rows[step.table] = count
        report.objects_deleted = len(object_keys)
        return report

    from app.services.audit import record as audit_record
    await audit_record(
        session,
        entity_type="workspace", entity_id=workspace_id, action="purge",
        workspace_id=workspace_id, user_id=actor_id, request=request,
        changes={"name": workspace.name,
                 "object_prefixes": len(object_keys),
                 "note": "cascading purge — audit history deliberately retained"},
    )
    await session.flush()

    for step in PURGE_PLAN:
        result = await session.exec(text(step.sql),
                                    params={"workspace_id": workspace_id})
        affected = getattr(result, "rowcount", 0) or 0
        if affected:
            report.rows[step.table] = affected
    await session.commit()

    # Objects last: the rows are what the API reads, so orphaned objects are a
    # storage-cost and data-retention problem, whereas rows that survive a
    # failed object delete would still be *servable*.
    report.objects_deleted, report.object_errors = _delete_objects(object_keys)
    logger.info("[purge] workspace %s (%s): %d rows, %d objects",
                workspace_id, workspace.name, report.total_rows,
                report.objects_deleted)
    return report


def _delete_objects(keys: List[str]) -> tuple:
    """Best-effort MinIO deletion. Errors are reported, never raised: the rows
    are already gone and failing here would leave the caller unable to tell what
    succeeded."""
    if not keys:
        return 0, []
    deleted, errors = 0, []
    try:
        from app.core.storage import minio_client
    except Exception as exc:  # noqa: BLE001
        return 0, [f"storage unavailable: {exc}"]
    for key in keys:
        try:
            if key.endswith("/"):
                deleted += minio_client.delete_prefix(key)
            else:
                minio_client.delete_object(key)
                deleted += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{key}: {exc}")
    return deleted, errors
