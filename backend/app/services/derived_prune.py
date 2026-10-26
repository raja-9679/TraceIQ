"""Remove run-derived summaries whose runs are gone.

Failure groups, flake scores, flake trends and dependency health are built
from runs but stored on their own, keyed by project / case / day, so deleting
runs left them behind: Failure triage and Flaky tests kept listing failures
from runs that no longer existed. After a user-initiated delete of runs,
suites or cases, `prune_after_delete` drops the rows that nothing remaining
supports, in the same transaction:

* failure groups with no result left in them (ticket/incident pointers to
  them are cleared — both are plain ids);
* flake records of cases with no result left — unless quarantined or owned:
  those are someone's decision, and quarantine expiry has its own rules;
* per-case daily flake scores of cases with no result left;
* daily flake trend points with no run on or before that day;
* daily dependency-health rows with no run that day.

Not called by the retention sweep: there, history outliving old runs is
intended (flake trends keep 180 days whatever the run retention).
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable

import sqlalchemy as sa

logger = logging.getLogger(__name__)

# Order matters only for the pointer clears, which run before the delete.
_STATEMENTS = (
    ("issueticket", "UPDATE issueticket SET cluster_id = NULL WHERE cluster_id IN ("
     " SELECT c.id FROM failurecluster c WHERE c.project_id = ANY(:p)"
     " AND NOT EXISTS (SELECT 1 FROM testcaseresult r WHERE r.cluster_id = c.id))"),
    ("incident", "UPDATE incident SET matched_cluster_id = NULL WHERE matched_cluster_id IN ("
     " SELECT c.id FROM failurecluster c WHERE c.project_id = ANY(:p)"
     " AND NOT EXISTS (SELECT 1 FROM testcaseresult r WHERE r.cluster_id = c.id))"),
    ("failurecluster", "DELETE FROM failurecluster c WHERE c.project_id = ANY(:p)"
     " AND NOT EXISTS (SELECT 1 FROM testcaseresult r WHERE r.cluster_id = c.id)"),
    ("flakerecord", "DELETE FROM flakerecord f USING testcase tc"
     " WHERE tc.id = f.test_case_id AND tc.project_id = ANY(:p)"
     " AND f.is_quarantined = false AND f.owner_user_id IS NULL"
     " AND NOT EXISTS (SELECT 1 FROM testcaseresult r WHERE r.test_case_id = f.test_case_id)"),
    ("flakecasescoredaily", "DELETE FROM flakecasescoredaily d WHERE d.project_id = ANY(:p)"
     " AND NOT EXISTS (SELECT 1 FROM testcaseresult r WHERE r.test_case_id = d.test_case_id)"),
    ("flakedailysnapshot", "DELETE FROM flakedailysnapshot s WHERE s.project_id = ANY(:p)"
     " AND NOT EXISTS (SELECT 1 FROM testrun t WHERE t.project_id = s.project_id"
     " AND t.created_at < s.day + 1)"),
    ("vendorhealthdaily", "DELETE FROM vendorhealthdaily v WHERE v.project_id = ANY(:p)"
     " AND NOT EXISTS (SELECT 1 FROM testrun t WHERE t.project_id = v.project_id"
     " AND t.created_at >= v.day AND t.created_at < v.day + 1)"),
)


async def prune_after_delete(conn, project_ids: Iterable[int]) -> Dict[str, int]:
    """Run the prunes for these projects on `conn` (caller's transaction).
    Returns rows affected per table. Postgres only (ANY over an int array,
    date + int); a no-op elsewhere so SQLite unit fixtures are unaffected."""
    pids = sorted({int(p) for p in project_ids if p is not None})
    if not pids or conn.dialect.name != "postgresql":
        return {}
    out: Dict[str, int] = {}
    for table, sql in _STATEMENTS:
        result = await conn.execute(sa.text(sql), {"p": pids})
        if result.rowcount:
            out[table] = out.get(table, 0) + int(result.rowcount)
    if out:
        logger.info("[delete] pruned derived rows for projects %s: %s", pids, out)
    return out
