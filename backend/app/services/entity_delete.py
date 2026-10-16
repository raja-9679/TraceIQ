"""Per-entity cascading deletes — projects, suites, cases, runs.

The per-entity delete handlers were each written before tables that reference
their entity existed, and every later table broke one of them: project delete
500'd on `testcase_project_id_fkey`, suite delete on
`testcaserevision_test_case_id_fkey`, run delete on
`selectorhealproposal_source_run_id_fkey`. Each handler carried its own
hand-written list of dependents, and each list was stale.

`app/services/purge.py` solved the same problem for whole workspaces with an
ordered plan plus a test that walks the FK graph. A per-entity delete has a
different shape — the scope is a set of ids, not a workspace, and which tables
are touched depends on where you start — so this module derives the plan from
the graph itself instead of listing statements:

1. **Every foreign key that can be reached from a deletable entity declares a
   policy** in `ON_DELETE`: `DELETE` (the referencing row goes too, and the walk
   continues from it) or `NULLIFY` (the referencing row survives, the link is
   cleared). The walk refuses to run past an undeclared reference
   (`UnplannedReference`), and `tests/test_entity_delete_plan.py` fails the
   build for one — so a table added later cannot reintroduce the 500.

2. **Order comes from the graph**, not from the author: rows are deleted in a
   topological order of the FK graph (referencing tables first), and each
   NULLIFY runs immediately before the rows it points at are deleted — by
   which point referencing rows that were going anyway are already gone.

Policy is per *edge*, not per table, because the same table is both: a heal
proposal goes with its test case (it is a suggestion to edit that case) but
outlives the run it was derived from (it is still a valid suggestion).

The planner is written sans-IO (a generator that yields statements and is sent
results) so the async API handlers and the sync Celery retention task share one
implementation rather than two copies that drift.

`auditlog` has no foreign keys and is never touched: history outlives the
objects it describes, exactly as in the workspace purge.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from graphlib import TopologicalSorter
from typing import Dict, Generator, Iterable, List, Set, Tuple, Optional

import sqlalchemy as sa

logger = logging.getLogger(__name__)

DELETE = "delete"
NULLIFY = "nullify"

# Entities a caller may hand to `cascade_delete`. Anything reachable from them
# by a DELETE edge is deleted too; the guard test requires a policy on every
# edge into any of those tables.
ROOTS = ("project", "testsuite", "testcase", "testrun")

# "<referencing table>.<column>" -> policy. Reasons are inline because the
# guard test reads this dict as "each of these was considered".
ON_DELETE: Dict[str, str] = {
    # --- into project: project-scoped configuration and data all go. ---------
    "testsuite.project_id": DELETE,
    "testcase.project_id": DELETE,
    "testrun.project_id": DELETE,
    "testschedule.project_id": DELETE,
    "caseproposal.project_id": DELETE,
    "authsession.project_id": DELETE,
    "projectapispec.project_id": DELETE,
    # A person's notification choice for a deleted project means nothing.
    "projectnotificationpref.project_id": DELETE,
    # A sign-off is a decision about this project's release; budgets and their
    # alert dedupe rows are project configuration (WP12d).
    "releasesignoff.project_id": DELETE,
    "projectcostbudget.project_id": DELETE,
    # WP14a: the project was only the chat's focus; the conversation is the
    # user's and survives. Answers that used the project's data become
    # unreadable anyway (reading one requires access to every project it used)
    # and age out with the 30-day retention.
    "assistantconversation.project_id": NULLIFY,
    "costbudgetalert.project_id": DELETE,
    "externaltestreport.project_id": DELETE,
    "failurecluster.project_id": DELETE,
    "mobileappbuild.project_id": DELETE,
    "projectenvironment.project_id": DELETE,
    "projectsecret.project_id": DELETE,
    "reportschedule.project_id": DELETE,
    "securityscan.project_id": DELETE,
    "statuspage.project_id": DELETE,
    # A public link to a deleted project's report must stop working.
    "sharelink.project_id": DELETE,
    "teamprojectaccess.project_id": DELETE,
    "userprojectaccess.project_id": DELETE,
    "securityfinding.project_id": DELETE,
    "requirementlink.project_id": DELETE,
    "requirement.project_id": DELETE,
    "incident.project_id": DELETE,
    # Reporting data (WP12b): deployment events and roll-ups of the project.
    "deployment.project_id": DELETE,
    "flakedailysnapshot.project_id": DELETE,
    "flakecasescoredaily.project_id": DELETE,
    "vendorhealthdaily.project_id": DELETE,
    "incidentwebhook.project_id": DELETE,
    # The matrix is project configuration; its runs/cells are its history.
    "accessmatrix.project_id": DELETE,
    "accessmatrixrun.project_id": DELETE,
    # Estates and monitored pages (WP9) are project configuration; their scans,
    # domains, score history, script inventory and change log go with them.
    "estate.project_id": DELETE,
    "monitoredpage.project_id": DELETE,
    "pagingchannel.project_id": DELETE,
    "maintenancewindow.project_id": DELETE,
    "pagingevent.project_id": DELETE,
    # Crawl configuration and history, and page-budget trend rows (WP10).
    "sitecrawl.project_id": DELETE,
    "sitecrawlrun.project_id": DELETE,
    "pagebudgetsample.project_id": DELETE,
    # Visual review (WP14e): a run's comparisons and the threads about them.
    "visualcomparison.project_id": DELETE,
    "visualreviewcomment.project_id": DELETE,
    # Reporting (WP12c): imports, path rules, journeys, flag catalogue and
    # benchmark groups are all project data.
    "journeyimport.project_id": DELETE,
    "journeyconfig.project_id": DELETE,
    "businessjourney.project_id": DELETE,
    "flagcatalogentry.project_id": DELETE,
    "benchmarkgroup.project_id": DELETE,
    # Nullable, but NULL means "workspace-wide" for these three: clearing the
    # link would WIDEN a project-scoped API key / webhook / persona (with its
    # stored session) to every project in the workspace.
    "apikey.project_id": DELETE,
    "workspacewebhook.project_id": DELETE,
    "persona.project_id": DELETE,

    # --- into testsuite --------------------------------------------------------
    "testsuite.parent_id": DELETE,          # sub-modules go with their parent
    "testcase.test_suite_id": DELETE,
    "testrun.test_suite_id": DELETE,        # NOT NULL
    # A CREATE proposal aimed at a deleted suite can never be applied.
    "caseproposal.test_suite_id": DELETE,
    # Suite/case handlers 409 on a referencing schedule before getting here (a
    # schedule is user configuration and shouldn't silently vanish); this only
    # fires for a whole-project delete, where the schedule is the project's.
    "testschedule.test_suite_id": DELETE,
    # The webhook keeps working; its proposals fall back to the first suite.
    "incidentwebhook.default_suite_id": NULLIFY,
    # A benchmark of a deleted suite cannot be re-run; its runs go anyway
    # (testrun.test_suite_id).
    "benchmarkgroup.test_suite_id": DELETE,

    # --- into testcase ---------------------------------------------------------
    "testrun.test_case_id": DELETE,         # a single-case run of that case
    "testcaserevision.test_case_id": DELETE,
    "visualbaseline.test_case_id": DELETE,
    "flakerecord.test_case_id": DELETE,
    "flakecasescoredaily.test_case_id": DELETE,  # that case's score history
    "selectorhealproposal.test_case_id": DELETE,  # a proposal to edit the case
    "caseproposal.target_case_id": DELETE,  # update/delete of a case that's gone
    "usertestcaseaccess.test_case_id": DELETE,
    "requirementlink.test_case_id": DELETE,
    "testschedule.test_case_id": DELETE,    # see testschedule.test_suite_id
    # Results belong to their RUN; a suite run keeps its result rows when one
    # of its cases is deleted (the FK is ON DELETE SET NULL in the schema too).
    "testcaseresult.test_case_id": NULLIFY,
    # An incident is history about production, not about the case: it
    # outlives a deleted matched / regression / "should have caught it" case.
    "incident.matched_case_id": NULLIFY,
    "incident.regression_case_id": NULLIFY,
    "incident.escaped_case_id": NULLIFY,
    # The stored login session is the project's; it stays usable after the
    # case that captured it is deleted.
    "authsession.captured_by_case_id": NULLIFY,
    # A page's budget trend is about the page, not the case that measured it.
    "pagebudgetsample.test_case_id": NULLIFY,
    # A comparison is evidence about that case's baseline; it cannot be
    # accepted once the case is gone.
    "visualcomparison.test_case_id": DELETE,

    # --- into testrun ----------------------------------------------------------
    "testcaseresult.test_run_id": DELETE,   # NOT NULL; the run's own output
    # Findings from passive analysis of the run's own traffic are run evidence.
    "securityfinding.run_id": DELETE,
    # Requests captured for the run's wait-for-webhook steps; meaningless
    # without it (and swept after 24 h anyway).
    "webhookcapture.run_id": DELETE,
    # These outlive the run: a pending proposal is still a valid suggestion,
    # an issue ticket exists in the external tracker regardless, and a monitor
    # check is a point in the schedule's uptime history.
    "selectorhealproposal.source_run_id": NULLIFY,
    "caseproposal.source_run_id": NULLIFY,
    "issueticket.run_id": NULLIFY,
    "monitorcheck.run_id": NULLIFY,
    # A matrix cell's verdict outlives the TestRun a case row executed as.
    "accessmatrixcell.test_run_id": NULLIFY,
    # A budget sample is that run's measurement (NOT NULL).
    "pagebudgetsample.test_run_id": DELETE,
    # The run's own comparisons, and comments on them / on its groups.
    "visualcomparison.test_run_id": DELETE,
    "visualreviewcomment.test_run_id": DELETE,

    # --- into tables only reachable through project ---------------------------
    "monitorcheck.schedule_id": DELETE,     # NOT NULL; history of that monitor
    "securityfinding.scan_id": DELETE,      # a scan's own findings
    "accessmatrixrun.matrix_id": DELETE,    # a matrix's own executions
    "accessmatrixcell.matrix_run_id": DELETE,
    "estatedomain.estate_id": DELETE,
    "estatescan.estate_id": DELETE,
    "estatedomainresult.scan_id": DELETE,
    "estatedomainresult.domain_id": DELETE,
    "pagescript.page_id": DELETE,
    "pagechangeevent.page_id": DELETE,
    "sitecrawlrun.crawl_id": DELETE,        # a crawl's own executions
    "sitecrawlissue.crawl_run_id": DELETE,
    "visualreviewcomment.comparison_id": DELETE,   # a thread about that item
    "testrun.environment_id": NULLIFY,      # a run outlives an environment
    "deployment.environment_id": NULLIFY,   # a deployment record outlives it too
    "testrun.benchmark_group_id": NULLIFY,  # a run outlives its benchmark group
    "testcaseresult.cluster_id": NULLIFY,   # a result outlives its cluster
}

# Postgres caps bind parameters per statement (asyncpg: 32767). Projects with
# years of runs exceed that easily.
_CHUNK = 5000


class UnplannedReference(RuntimeError):
    """A foreign key into a table being deleted has no policy in ON_DELETE."""


class CrossProjectReference(RuntimeError):
    """A row in ANOTHER project points at something this delete would remove.

    The cascade walks foreign keys by id, so without a boundary an editor's
    delete in project A would silently remove project B's rows that happen to
    reference A's suite/case/run (a child suite re-parented across projects, a
    run or proposal naming another project's case). Access was checked for A
    only, so the delete is refused instead; the API turns this into a 409.

    The message names the other project(s) and is for SERVER logs only — the
    caller may have no access to those projects, so the API response must not
    repeat it (see the handler in app/main.py).
    """

    def __init__(self, table: str, column: str, project_ids: Set[int]):
        self.table, self.column = table, column
        self.project_ids = sorted(project_ids)
        super().__init__(
            f"{table}.{column} rows in project(s) {self.project_ids} reference "
            "what this delete would remove; remove those references first")


# --- graph -------------------------------------------------------------------

def _metadata():
    from sqlmodel import SQLModel

    import app.models  # noqa: F401 — registers every table
    return SQLModel.metadata


def fk_edges() -> List[Tuple[str, str, str]]:
    """(referencing table, column, referenced table) for every FK."""
    edges = []
    for table in _metadata().tables.values():
        for fk in table.foreign_keys:
            edges.append((table.name, fk.parent.name, fk.column.table.name))
    return edges


def _referrers() -> Dict[str, List[Tuple[str, str]]]:
    out: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for child, col, parent in fk_edges():
        out[parent].append((child, col))
    return out


def delete_order() -> List[str]:
    """Every table, referencing tables before the tables they reference.

    Self-references (testsuite.parent_id) are left out of the sort; they are
    handled by clearing the column before the table's own DELETE.
    """
    sorter: TopologicalSorter = TopologicalSorter()
    for name in _metadata().tables:
        sorter.add(name)
    for child, _col, parent in fk_edges():
        if child != parent:
            sorter.add(parent, child)   # child is a predecessor: deleted first
    return list(sorter.static_order())


def reachable_tables(roots: Iterable[str] = ROOTS) -> Set[str]:
    """Tables a cascade from `roots` can delete rows from (DELETE edges only)."""
    refs = _referrers()
    seen: Set[str] = set()
    frontier = list(roots)
    while frontier:
        table = frontier.pop()
        if table in seen:
            continue
        seen.add(table)
        for child, col in refs.get(table, ()):
            if ON_DELETE.get(f"{child}.{col}") == DELETE:
                frontier.append(child)
    return seen


def _chunks(ids: Iterable[int]) -> Iterable[List[int]]:
    ordered = sorted(ids)
    for i in range(0, len(ordered), _CHUNK):
        yield ordered[i:i + _CHUNK]


def _pk(table: sa.Table) -> sa.Column:
    cols = list(table.primary_key.columns)
    if len(cols) != 1:
        raise UnplannedReference(
            f"{table.name} is referenced by a foreign key but has a composite "
            "primary key; the cascade cannot track its rows by id")
    return cols[0]


# --- plan + execute (sans-IO) --------------------------------------------------

@dataclass
class DeleteReport:
    root: str
    root_ids: List[int]
    rows: Dict[str, int] = field(default_factory=dict)
    nullified: Dict[str, int] = field(default_factory=dict)
    ids: Dict[str, Set[int]] = field(default_factory=dict)
    object_keys: List[str] = field(default_factory=list)
    objects_deleted: int = 0
    object_errors: List[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(self.rows.values())

    def deleted(self, table: str) -> int:
        return self.rows.get(table, 0)


def _cascade(root: str, root_ids: Iterable[int]) -> Generator:
    """Yields SQLAlchemy statements; is sent each statement's Result.

    Returns a DeleteReport (minus object deletion) via StopIteration.
    """
    if root not in ROOTS:
        raise ValueError(f"cascade_delete does not handle {root!r}")
    md = _metadata()
    refs = _referrers()
    report = DeleteReport(root=root, root_ids=sorted(set(root_ids)))
    if not report.root_ids:
        return report

    # Tables whose rows must be tracked by id: anything something else points at.
    ids: Dict[str, Set[int]] = defaultdict(set)
    leaf_edges: Set[Tuple[str, str, str]] = set()   # DELETE child WHERE col IN parent ids
    nullify: Dict[str, Set[Tuple[str, str]]] = defaultdict(set)  # parent -> (child, col)

    # The projects this delete was authorised for: the roots' own. A DELETE
    # edge must not reach a row that belongs to any other project. Child rows
    # with no project_id (legacy, or tables bound only through their parent)
    # are judged by the parent they hang off. Roots WITHOUT a project get the
    # strictest bound, not none: an empty allowed set, so they may only reach
    # project-less rows — otherwise a legacy suite/case/run would be a way to
    # cascade into any project, or any tenant's.
    allowed: Set[int] = set()
    if root == "project":
        allowed = set(report.root_ids)
    else:
        rtable = md.tables[root]
        for chunk in _chunks(report.root_ids):
            result = yield (sa.select(rtable.c.project_id).distinct()
                            .where(_pk(rtable).in_(chunk)))
            allowed.update(p for p in result.scalars().all() if p is not None)

    def _foreign_rows(child: str, col: str, parent_ids: Set[int]):
        """Yield-driven check: project ids of `child` rows outside `allowed`."""
        table = md.tables[child]
        if col == "project_id" or "project_id" not in table.c:
            return set()
        foreign: Set[int] = set()
        for chunk in _chunks(parent_ids):
            outside = (table.c.project_id.notin_(sorted(allowed)) if allowed
                       else sa.true())
            result = yield (sa.select(table.c.project_id.label("foreign_project_id"))
                            .distinct()
                            .where(table.c[col].in_(chunk),
                                   table.c.project_id.isnot(None),
                                   outside))
            foreign.update(p for p in result.scalars().all() if p is not None)
        return foreign

    ids[root] = set(report.root_ids)
    queue: List[Tuple[str, Set[int]]] = [(root, set(report.root_ids))]
    while queue:
        parent, new_ids = queue.pop()
        for child, col in refs.get(parent, ()):
            policy = ON_DELETE.get(f"{child}.{col}")
            if policy is None:
                raise UnplannedReference(
                    f"{child}.{col} references {parent} but has no ON_DELETE "
                    "policy in app/services/entity_delete.py")
            if policy == NULLIFY:
                nullify[parent].add((child, col))
                continue
            foreign = yield from _foreign_rows(child, col, new_ids)
            if foreign:
                raise CrossProjectReference(child, col, foreign)
            if child in refs:   # referenced itself → track its ids, keep walking
                table = md.tables[child]
                pk = _pk(table)
                found: Set[int] = set()
                for chunk in _chunks(new_ids):
                    result = yield sa.select(pk).where(table.c[col].in_(chunk))
                    found.update(result.scalars().all())
                fresh = found - ids[child]
                if fresh:
                    ids[child] |= fresh
                    queue.append((child, fresh))
            else:
                leaf_edges.add((child, col, parent))

    report.ids = {t: set(v) for t, v in ids.items() if v}
    report.object_keys = yield from _object_keys(md, report.ids)

    for name in delete_order():
        table = md.tables[name]
        # Clear surviving references to this table's doomed rows first.
        if ids.get(name):
            for child, col in sorted(nullify.get(name, ())):
                ctable = md.tables[child]
                for chunk in _chunks(ids[name]):
                    result = yield (sa.update(ctable)
                                    .where(ctable.c[col].in_(chunk))
                                    .values({col: None}))
                    if result.rowcount:
                        key = f"{child}.{col}"
                        report.nullified[key] = report.nullified.get(key, 0) + result.rowcount
        # Leaf tables: delete by the FK.
        for child, col, parent in sorted(e for e in leaf_edges if e[0] == name):
            for chunk in _chunks(ids.get(parent, ())):
                result = yield sa.delete(table).where(table.c[col].in_(chunk))
                if result.rowcount:
                    report.rows[name] = report.rows.get(name, 0) + result.rowcount
        # Tracked tables: delete by primary key.
        if ids.get(name):
            pk = _pk(table)
            for child, col in refs.get(name, ()):
                if child == name:  # self-reference: unhook so chunks can't trip it
                    for chunk in _chunks(ids[name]):
                        yield (sa.update(table)
                               .where(pk.in_(chunk), table.c[col].isnot(None))
                               .values({col: None}))
            for chunk in _chunks(ids[name]):
                result = yield sa.delete(table).where(pk.in_(chunk))
                if result.rowcount:
                    report.rows[name] = report.rows.get(name, 0) + result.rowcount

    return report


def _object_keys(md, ids: Dict[str, Set[int]]) -> Generator:
    """MinIO keys owned by the doomed rows. Runs BEFORE any DELETE: keys are
    derived from run and case ids, and the visualbaseline rows that say which
    cases have baselines are about to go. Mirrors `purge.collect_object_keys`;
    the deletion itself reuses the purge's `_delete_objects`.
    """
    from app.services.purge import baseline_purge_prefix

    keys: List[str] = [f"runs/{rid}/" for rid in sorted(ids.get("testrun", ()))]
    keys.extend(f"access-matrix/{rid}/" for rid in sorted(ids.get("accessmatrixrun", ())))
    case_ids = ids.get("testcase", set())
    if case_ids:
        vb = md.tables["visualbaseline"]
        with_baselines: Set[int] = set()
        for chunk in _chunks(case_ids):
            result = yield (sa.select(vb.c.test_case_id).distinct()
                            .where(vb.c.test_case_id.in_(chunk)))
            with_baselines.update(result.scalars().all())
        keys.extend(baseline_purge_prefix(cid) for cid in sorted(with_baselines))
    return keys


async def _drive_async(gen, conn):
    result = None
    try:
        while True:
            stmt = gen.send(result)
            result = await conn.execute(stmt)
    except StopIteration as stop:
        return stop.value


def _drive_sync(gen, conn):
    result = None
    try:
        while True:
            stmt = gen.send(result)
            result = conn.execute(stmt)
    except StopIteration as stop:
        return stop.value


async def _collect_build_keys(conn, project_ids: Set[int]) -> List[str]:
    keys: List[str] = []
    if not project_ids:
        return keys
    table = _metadata().tables["mobileappbuild"]
    for chunk in _chunks(project_ids):
        result = await conn.execute(
            sa.select(table.c.file_key).where(table.c.project_id.in_(chunk),
                                              table.c.file_key.isnot(None)))
        keys.extend(k for k in result.scalars().all() if k)
    return keys


async def cascade_delete(session, root: str, ids: Iterable[int]) -> DeleteReport:
    """Delete `root` rows with these ids and everything ON_DELETE says goes with
    them, inside the session's transaction. Does NOT commit and does NOT touch
    object storage — the caller commits, then calls `delete_objects(report)`,
    so a rolled-back delete never destroys artifacts.

    Access checks are the caller's job; this deletes whatever it is given.
    """
    conn = await session.connection()
    ids = list(ids)
    build_keys: List[str] = []
    if root == "project":
        # mobileappbuild is a leaf (nothing references it), so its rows are not
        # id-tracked; its file keys must be read before the rows go.
        build_keys = await _collect_build_keys(conn, set(ids))
    report = await _drive_async(_cascade(root, ids), conn)
    report.object_keys.extend(build_keys)
    logger.info("[delete] %s %s: %d rows, %d nullified, %d object keys",
                root, report.root_ids, report.total_rows,
                sum(report.nullified.values()), len(report.object_keys))
    return report


def cascade_delete_sync(session, root: str, ids: Iterable[int]) -> DeleteReport:
    """`cascade_delete` for sync sessions (Celery tasks). Same contract."""
    if root == "project":
        raise ValueError("project deletes go through the async API path")
    return _drive_sync(_cascade(root, list(ids)), session.connection())


def delete_objects(report: DeleteReport) -> DeleteReport:
    """Best-effort artifact deletion after the rows are committed."""
    from app.services.purge import _delete_objects

    report.objects_deleted, report.object_errors = _delete_objects(report.object_keys)
    for err in report.object_errors:
        logger.warning("[delete] object cleanup: %s", err)
    return report
