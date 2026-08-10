"""The purge plan must cover every table that can reach a workspace — G1.

`workspace_service.delete_workspace` deleted the workspace row and its teams and
nothing else. Thirty-odd tables of customer data survived as orphans:
unreachable through the API, still in the database, still the wrong answer to
"have you deleted our data?".

A hand-written list of tables fixes that once and rots immediately — the next
feature adds a table and nobody updates the purge. So the guarantee lives here
instead: this walks the foreign-key graph in `SQLModel.metadata`, finds
everything that can reach `workspace`, and fails if any of it is neither in
`PURGE_PLAN` nor in `PURGE_EXEMPT` with a stated reason.

If you are reading this because the test just failed: you added a table that
holds workspace-scoped data. Either add a step to `PURGE_PLAN` or add it to
`PURGE_EXEMPT` with a reason. Do not delete the assertion.
"""
import pytest
from sqlmodel import SQLModel

from app.services.purge import PURGE_EXEMPT, PURGE_PLAN


def _fk_graph():
    """table -> set of tables it references."""
    import app.models  # noqa: F401 — registers every table on the metadata
    return {
        table.name: {fk.column.table.name for fk in table.foreign_keys}
        for table in SQLModel.metadata.tables.values()
    }


def _reaches(graph, start, target, seen=None):
    seen = seen if seen is not None else set()
    if start in seen:
        return False
    seen.add(start)
    for referenced in graph.get(start, ()):
        if referenced == target or _reaches(graph, referenced, target, seen):
            return True
    return False


def _workspace_linked():
    graph = _fk_graph()
    return {name for name in graph
            if name != "workspace" and _reaches(graph, name, "workspace")}


def test_every_workspace_linked_table_is_purged_or_exempt():
    planned = {step.table for step in PURGE_PLAN}
    uncovered = sorted(_workspace_linked() - planned - set(PURGE_EXEMPT))
    assert not uncovered, (
        "these tables hold workspace-scoped data but the purge ignores them, so "
        "deleting a workspace would leave them behind: "
        f"{', '.join(uncovered)}. Add a PURGE_PLAN step, or add each to "
        "PURGE_EXEMPT with the reason it should survive.")


def test_the_workspace_row_itself_is_purged():
    assert PURGE_PLAN[-1].table == "workspace", (
        "the workspace row must be deleted last — anything else still "
        "referencing it would violate a foreign key")


def test_the_plan_has_no_duplicate_tables():
    tables = [step.table for step in PURGE_PLAN]
    assert len(tables) == len(set(tables)), "a table appears twice in PURGE_PLAN"


def test_the_plan_only_names_real_tables():
    import app.models  # noqa: F401
    known = set(SQLModel.metadata.tables)
    unknown = sorted({step.table for step in PURGE_PLAN} - known)
    assert not unknown, f"PURGE_PLAN names tables that do not exist: {unknown}"


def test_every_exemption_states_a_reason():
    for table, reason in PURGE_EXEMPT.items():
        assert reason and len(reason) > 20, (
            f"{table} is exempt from the purge with no real explanation. An "
            "exemption without a reason is indistinguishable from an oversight.")


def test_the_audit_log_is_exempt():
    # Not incidental. The audit trail is append-only by trigger and deliberately
    # carries no foreign key to workspace, precisely so history outlives the
    # objects it describes. "What happened in the workspace that was deleted" is
    # a question an auditor asks.
    assert "auditlog" in PURGE_EXEMPT


def test_the_audit_log_is_not_in_the_plan():
    assert "auditlog" not in {step.table for step in PURGE_PLAN}


@pytest.mark.parametrize("table", [
    # A spot-check of the tables whose absence was the original finding, so the
    # graph walk above cannot pass vacuously if metadata introspection breaks.
    "project", "testsuite", "testcase", "testrun", "testcaseresult",
    "projectsecret", "persona", "visualbaseline", "mobileappbuild",
    "workspacewebhook", "apikey", "team", "userworkspace",
])
def test_named_tables_are_in_the_plan(table):
    assert table in {step.table for step in PURGE_PLAN}


def test_every_step_is_scoped_to_a_workspace():
    # A step whose SQL forgot the bind parameter would delete the whole table.
    for step in PURGE_PLAN:
        assert ":workspace_id" in step.sql, (
            f"{step.table}: DELETE is not scoped to a workspace — this would "
            "empty the table for every tenant")


def test_every_step_is_a_delete():
    for step in PURGE_PLAN:
        assert step.sql.strip().upper().startswith("DELETE FROM "), (
            f"{step.table}: purge steps must be DELETEs; the dry-run path "
            "rewrites 'DELETE FROM' into 'SELECT count(*) FROM' and would "
            "silently count nothing otherwise")
