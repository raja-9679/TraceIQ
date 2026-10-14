"""Every reference into a deletable entity must have a declared policy.

Project, suite, case and run deletes each 500'd on a foreign key added after
they were written (`testcase_project_id_fkey`,
`testcaserevision_test_case_id_fkey`, `selectorhealproposal_source_run_id_fkey`).
`app/services/entity_delete.py` now derives the cascade from the FK graph, and
this is the guarantee that keeps it that way: it walks `SQLModel.metadata` and
fails if any foreign key into a table the cascade can delete from has no
`ON_DELETE` policy.

If you are reading this because the test just failed: you added a foreign key
into a project / suite / case / run (or something that goes with them). Add
"<table>.<column>" to `ON_DELETE` as DELETE (the row goes with its parent) or
NULLIFY (the row survives, the link is cleared). Do not delete the assertion.

The planner is sans-IO, so the ordering tests below drive it without a database;
`tests/integration/test_entity_delete_db.py` runs it against Postgres.
"""
import pytest
from sqlmodel import SQLModel

from app.services.entity_delete import (
    CrossProjectReference,
    DELETE, NULLIFY, ON_DELETE, ROOTS, _cascade, delete_order, fk_edges,
    reachable_tables,
)
from app.services.purge import PURGE_EXEMPT, PURGE_PLAN


def _tables():
    import app.models  # noqa: F401
    return SQLModel.metadata.tables


# --- completeness -------------------------------------------------------------

def test_every_reference_into_a_deletable_table_has_a_policy():
    doomed = reachable_tables()
    missing = sorted(f"{child}.{col} -> {parent}"
                     for child, col, parent in fk_edges()
                     if parent in doomed and f"{child}.{col}" not in ON_DELETE)
    assert not missing, (
        "these foreign keys point at rows a project/suite/case/run delete can "
        "remove, but have no ON_DELETE policy, so that delete would 500 on them: "
        f"{', '.join(missing)}. Add each to ON_DELETE in "
        "app/services/entity_delete.py as DELETE or NULLIFY.")


def test_every_policy_names_a_real_foreign_key():
    real = {f"{child}.{col}" for child, col, _ in fk_edges()}
    stale = sorted(set(ON_DELETE) - real)
    assert not stale, f"ON_DELETE names foreign keys that do not exist: {stale}"


def test_policies_are_delete_or_nullify():
    assert set(ON_DELETE.values()) <= {DELETE, NULLIFY}


def test_nullify_only_targets_nullable_columns():
    tables = _tables()
    bad = sorted(key for key, policy in ON_DELETE.items() if policy == NULLIFY
                 and not tables[key.split(".")[0]].c[key.split(".")[1]].nullable)
    assert not bad, f"NULLIFY on a NOT NULL column would fail at runtime: {bad}"


@pytest.mark.parametrize("edge, policy", [
    # The three original findings, pinned so they cannot be reverted quietly.
    ("testcase.project_id", DELETE),
    ("testcaserevision.test_case_id", DELETE),
    ("selectorhealproposal.source_run_id", NULLIFY),
    # Nullable, but NULL means "workspace-wide": nullifying would widen access.
    ("apikey.project_id", DELETE),
    ("workspacewebhook.project_id", DELETE),
    ("persona.project_id", DELETE),
])
def test_decided_policies(edge, policy):
    assert ON_DELETE[edge] == policy


# --- containment --------------------------------------------------------------

@pytest.mark.parametrize("table", [
    "workspace", "users", "tenant", "team", "auditlog", "issuetrackerconfig",
])
def test_the_cascade_never_reaches_upward(table):
    assert table not in reachable_tables(), (
        f"a project/suite/case/run delete can reach {table} — that would delete "
        "data outside the entity being deleted")


def test_the_audit_log_is_never_touched():
    assert not any(key.startswith("auditlog.") for key in ON_DELETE)
    assert "auditlog" not in reachable_tables()


def test_project_cascade_agrees_with_the_workspace_purge():
    # Anything a project delete removes must also be removed by a workspace
    # purge; otherwise the two disagree about what "project data" is.
    purged = {step.table for step in PURGE_PLAN}
    extra = sorted(reachable_tables(["project"]) - purged - set(PURGE_EXEMPT))
    assert not extra, f"project delete removes tables the purge ignores: {extra}"


# --- ordering -----------------------------------------------------------------

def test_the_graph_has_an_order():
    order = delete_order()     # raises graphlib.CycleError on a cycle
    assert set(order) == set(_tables())


def test_tracked_tables_have_single_column_primary_keys():
    tables = _tables()
    referenced = {parent for _, _, parent in fk_edges()}
    bad = sorted(t for t in reachable_tables() & referenced
                 if len(tables[t].primary_key.columns) != 1)
    assert not bad, f"cascade tracks these by id but they have composite PKs: {bad}"


class _Result:
    def __init__(self, ids=()):
        self._ids = list(ids)
        self.rowcount = 1

    def scalars(self):
        return self

    def all(self):
        return self._ids


def _drive(root, ids, children=None, root_projects=(), foreign=None):
    """Run the planner with canned SELECT answers; return statement summaries.

    `children` answers id lookups per table. Project-boundary queries (which
    select `project_id`) are answered separately: `root_projects` for the
    roots' own projects, `foreign` per table for rows in other projects.
    """
    children = children or {}
    foreign = foreign or {}
    gen = _cascade(root, ids)
    log, result = [], None
    try:
        while True:
            stmt = gen.send(result)
            verb = type(stmt).__name__.lower()       # select / update / delete
            table = stmt.table.name if hasattr(stmt, "table") else \
                stmt.get_final_froms()[0].name
            column = stmt.selected_columns[0].name if verb == "select" else None
            if column in ("project_id", "foreign_project_id"):
                log.append(("boundary", table))
                # The roots' own lookup vs. a foreign-row check (they can hit
                # the same table, e.g. testsuite.parent_id); the check labels
                # its column.
                is_check = column == "foreign_project_id"
                rows = foreign.get(table, ()) if is_check else root_projects
                result = _Result(rows)
                continue
            log.append((verb, table))
            result = _Result(children.get(table, ()) if verb == "select" else ())
    except StopIteration as stop:
        return log, stop.value
    except CrossProjectReference as refusal:
        refusal.log = log
        raise


def _index(log, verb, table):
    return next(i for i, entry in enumerate(log) if entry == (verb, table))


def test_run_delete_clears_heal_proposal_link_before_deleting_the_run():
    log, report = _drive("testrun", [7])
    assert _index(log, "update", "selectorhealproposal") < _index(log, "delete", "testrun")
    assert _index(log, "delete", "testcaseresult") < _index(log, "delete", "testrun")
    assert ("delete", "selectorhealproposal") not in log
    assert report.object_keys == ["runs/7/"]


def test_suite_delete_removes_revisions_before_cases_before_suites():
    log, report = _drive("testsuite", [1], children={"testcase": [5]})
    assert _index(log, "delete", "testcaserevision") < _index(log, "delete", "testcase")
    assert _index(log, "delete", "testcase") < _index(log, "delete", "testsuite")


def test_project_delete_removes_cases_before_the_project():
    log, _ = _drive("project", [3], children={"testcase": [5], "testsuite": [2]})
    assert _index(log, "delete", "testcase") < _index(log, "delete", "project")
    assert _index(log, "delete", "testsuite") < _index(log, "delete", "project")


def test_object_keys_are_collected_before_any_delete():
    log, report = _drive("testsuite", [1], children={
        "testcase": [5], "testrun": [9], "visualbaseline": [5]})
    first_delete = next(i for i, (verb, _) in enumerate(log) if verb == "delete")
    assert _index(log, "select", "visualbaseline") < first_delete
    assert "runs/9/" in report.object_keys
    assert "baselines/5/" in report.object_keys


def test_an_empty_id_list_issues_nothing():
    log, report = _drive("testrun", [])
    assert log == [] and report.total_rows == 0


def test_unknown_root_is_refused():
    with pytest.raises(ValueError):
        _drive("workspace", [1])


def test_roots_are_all_reachable():
    assert set(ROOTS) <= reachable_tables()


# --- project boundary -----------------------------------------------------------

def test_a_reference_from_another_project_refuses_the_delete():
    # Suite 1 lives in project 10; a case in project 99 hangs off it.
    with pytest.raises(CrossProjectReference) as err:
        _drive("testsuite", [1], children={"testcase": [5]},
               root_projects=[10], foreign={"testcase": [99]})
    assert err.value.table == "testcase" and err.value.project_ids == [99]


def test_a_refused_delete_issues_no_writes():
    with pytest.raises(CrossProjectReference) as err:
        _drive("testcase", [5], root_projects=[10], foreign={"testrun": [99]})
    verbs = {verb for verb, _ in err.value.log}
    assert "delete" not in verbs and "update" not in verbs


def test_same_project_references_are_deleted_normally():
    log, report = _drive("testsuite", [1], children={"testcase": [5]},
                         root_projects=[10], foreign={})
    assert ("delete", "testcase") in log


def test_project_deletes_do_not_bound_their_own_project_edges():
    log, _ = _drive("project", [3])
    # Edges ON project_id are the project's own rows by definition.
    boundary_tables = {t for verb, t in log if verb == "boundary"}
    assert "testsuite" not in boundary_tables and "testcase" not in boundary_tables


def test_roots_without_a_project_may_not_reach_any_project():
    # A legacy suite with no project must not become a way into project 99.
    with pytest.raises(CrossProjectReference):
        _drive("testsuite", [1], children={"testcase": [5]},
               root_projects=[], foreign={"testcase": [99]})


def test_roots_without_a_project_still_delete_projectless_children():
    log, _ = _drive("testsuite", [1], children={"testcase": [5]},
                    root_projects=[], foreign={})
    assert ("delete", "testcase") in log


def test_the_api_answers_a_cross_project_refusal_with_409():
    import asyncio
    from app.main import app

    handler = app.exception_handlers.get(CrossProjectReference)
    assert handler is not None, "CrossProjectReference would surface as a 500"
    resp = asyncio.run(handler(None, CrossProjectReference("testcase", "test_suite_id", {99})))
    assert resp.status_code == 409
    # The other project's id and table belong in the server log, not in a
    # response to a caller who may have no access to that project.
    assert b"99" not in resp.body and b"testcase" not in resp.body
