"""The row-level-security plan must cover every tenant-owned table — WP15a.

Like tests/test_purge_plan.py: a hand-written list of protected tables rots the
day someone adds a table. So this walks the foreign-key graph, finds every
table that can reach `workspace`, and fails unless each is

  * in `rls.RLS_POLICIES` (it has a policy), or
  * in `rls.RLS_EXEMPT` with a stated reason, or
  * in `rls.RLS_DEFERRED` — the backlog that existed when RLS was introduced.
    That set is frozen below: it may only shrink.

If you are reading this because the test failed: you added a table that holds
tenant data. Give it a policy (an entry in RLS_POLICIES plus a migration that
creates it — copy e012b3c4d601) or exempt it with a reason. Do not add it to
RLS_DEFERRED.
"""
import importlib.util
import pathlib

import pytest
from sqlmodel import SQLModel

from app.services import rls

_VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "app" / "alembic" / "versions"

# The backlog on the day WP15a landed (2026-10-20). Shrinks only.
_DEFERRED_AT_INTRODUCTION = frozenset({
    "accessmatrix", "accessmatrixcell", "accessmatrixrun", "accessreviewcampaign",
    "accessreviewitem", "agentcontrol", "agentsession", "assistantconversation",
    "assistantmessage", "benchmarkgroup", "businessjourney", "caseproposal",
    "compliancecontrol", "controlevidence", "controlexception", "costbudgetalert",
    "deployment", "estate", "estatedomain", "estatedomainresult", "estatescan",
    "evidencepack", "externaltestreport", "failurecluster", "flagcatalogentry",
    "flakecasescoredaily", "flakedailysnapshot", "flakerecord", "gatepolicyversion",
    "incident", "incidentwebhook", "issueticket", "issuetrackerconfig",
    "journeyconfig", "journeyimport", "llmusageevent", "maintenancewindow",
    "monitorcheck", "monitoredpage", "pagebudgetsample", "pagechangeevent",
    "pagescript", "pagingchannel", "pagingevent", "policydecisionlog",
    "projectapispec", "projectcostbudget", "projectenvironment",
    "projectnotificationpref", "releasesignoff", "reportschedule", "requirement",
    "requirementlink", "securityscan", "selectorhealproposal", "sharelink",
    "sitecrawl", "sitecrawlissue", "sitecrawlrun", "statuspage", "team",
    "testcaserevision", "testschedule", "trustcenter", "trustdocument",
    "trustincidentpost", "usagerecord", "vendorhealthdaily", "visualreviewcomment",
    "webhookcapture", "webhookdelivery", "workspacesubscription", "workspacewebhook",
})


def _fk_graph():
    import app.models  # noqa: F401
    return {t.name: {fk.column.table.name for fk in t.foreign_keys}
            for t in SQLModel.metadata.tables.values()}


def _reaches(graph, start, target, seen=None):
    seen = seen if seen is not None else set()
    if start in seen:
        return False
    seen.add(start)
    return any(r == target or _reaches(graph, r, target, seen) for r in graph.get(start, ()))


def _tenant_tables():
    graph = _fk_graph()
    return {n for n in graph if n != "workspace" and _reaches(graph, n, "workspace")}


def test_every_tenant_table_has_a_policy_or_a_stated_exemption():
    uncovered = sorted(_tenant_tables() - set(rls.RLS_POLICIES) - set(rls.RLS_EXEMPT)
                       - rls.RLS_DEFERRED)
    assert not uncovered, (
        "these tables hold tenant data but have no row-level-security policy: "
        f"{', '.join(uncovered)}. Add each to rls.RLS_POLICIES with a migration "
        "creating the policy, or to rls.RLS_EXEMPT with the reason it needs none.")


def test_the_deferred_backlog_only_shrinks():
    grown = sorted(rls.RLS_DEFERRED - _DEFERRED_AT_INTRODUCTION)
    assert not grown, (
        f"{grown} were added to RLS_DEFERRED. New tables get a policy or a "
        "reasoned exemption; the backlog is not a place to put them.")


def test_categories_do_not_overlap():
    p, e, d = set(rls.RLS_POLICIES), set(rls.RLS_EXEMPT), set(rls.RLS_DEFERRED)
    assert not (p & e) and not (p & d) and not (e & d)


def test_only_real_tables_are_named():
    import app.models  # noqa: F401
    known = set(SQLModel.metadata.tables)
    named = set(rls.RLS_POLICIES) | set(rls.RLS_EXEMPT) | set(rls.RLS_DEFERRED)
    assert not sorted(named - known)


def test_every_exemption_states_a_reason():
    for table, reason in rls.RLS_EXEMPT.items():
        assert reason and len(reason) > 20, f"{table}: an exemption needs a real reason"


@pytest.mark.parametrize("table", [
    # The decision (H1) names these; the graph walk must not pass vacuously.
    "testrun", "testcaseresult", "testcase", "testsuite", "projectsecret",
    "securityfinding", "project",
])
def test_the_high_value_tables_have_policies(table):
    assert table in rls.RLS_POLICIES


def _migration_policies():
    """Union of POLICIES dicts in every migration that defines one."""
    out = {}
    for path in sorted(_VERSIONS.glob("*.py")):
        src = path.read_text()
        if "POLICIES = {" not in src or "traceiq_tenant" not in src:
            continue
        spec = importlib.util.spec_from_file_location(f"_mig_{path.stem}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out.update(getattr(mod, "POLICIES", {}))
    return out


def test_every_planned_table_has_a_migration_creating_its_policy():
    created = _migration_policies()
    missing = sorted(set(rls.RLS_POLICIES) - set(created))
    assert not missing, f"no migration creates a policy for {missing}"


def test_the_frozen_migration_text_matches_the_plan():
    # The log-mode shadow check uses rls.shadow_predicate(); the database uses
    # the migration's frozen copy. If they drift, log mode measures a different
    # wall from the one enforce mode builds.
    created = _migration_policies()
    for table in rls.RLS_POLICIES:
        assert created[table] == rls.policy_sql(table), f"{table}: migration text drifted"


def test_policies_only_restrict_in_enforce_mode():
    for table in rls.RLS_POLICIES:
        sql = rls.policy_sql(table)
        assert sql.startswith("CASE (SELECT current_setting('traceiq.rls_mode', true)) "
                              "WHEN 'enforce' THEN COALESCE(")
        assert sql.endswith(", false) ELSE true END"), (
            f"{table}: an unknown row (NULL predicate) must be hidden under enforce, "
            "and every non-enforcing session must see everything")


def test_outer_columns_are_table_qualified():
    # Inside a subquery an unqualified `project_id` binds to the subquery's own
    # table — testsuite.project_id instead of the row's — and the policy would
    # silently test the wrong thing.
    import re
    for table, template in rls.RLS_POLICIES.items():
        outside = re.sub(r"\(SELECT .*?\)", "", template)
        for col in re.findall(r"(?<![\w.])(project_id|workspace_id|test_suite_id|"
                              r"test_run_id|test_case_id)\b", outside):
            pytest.fail(f"{table}: unqualified {col} in {template}")


def test_shadow_predicate_uses_parameters_not_gucs():
    for table in rls.RLS_POLICIES:
        sql = rls.shadow_predicate(table)
        assert "current_setting" not in sql and "traceiq_rls_ids" not in sql
        assert "CAST(:ws AS integer[])" in sql


@pytest.mark.parametrize("raw,mode", [
    ("off", "off"), ("LOG", "log"), (" enforce ", "enforce"), (None, "off"),
    ("", "off"), ("enfroce", "off"), ("true", "off"),
])
def test_mode_normalisation_never_turns_a_typo_into_enforcement(raw, mode):
    assert rls.normalise_mode(raw) == mode


def test_the_settings_are_registered_for_the_admin_ui():
    from app.services.instance_settings import REGISTRY
    assert REGISTRY["RLS_MODE"].group == "tenant_isolation"
    assert REGISTRY["RLS_LOG_SAMPLE_PERCENT"].type == "int"
    assert "enforce" in REGISTRY["RLS_MODE"].description


def test_off_mode_binds_nothing(monkeypatch):
    import asyncio
    monkeypatch.setattr(rls, "current_mode", lambda: "off")

    class Boom:
        info = {}

        def __getattr__(self, name):
            raise AssertionError(f"off mode touched the session ({name})")

    assert asyncio.run(rls.bind_principal(Boom(), object())) is None
