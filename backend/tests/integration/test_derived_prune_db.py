"""Deleting runs also removes the summaries built from them (derived_prune).

Failure triage, Flaky tests, flake trends and dependency health kept showing
data from deleted runs. Reuses the entity-delete scene: two projects in one
workspace, each with a suite run (result in a failure group) and a case run.

    ./run-tests-live.sh tests/integration/test_derived_prune_db.py -q
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text

from app.models import FlakeDailySnapshot, FlakeRecord, VendorHealthDaily
from tests.integration.test_entity_delete_db import (  # noqa: F401  (fixtures)
    _resync, _scalar, pytestmark, scene, session,
)


async def _add_summaries(session, tree, *, quarantined: bool = False):
    today = datetime.utcnow().date()
    session.add_all([
        FlakeDailySnapshot(project_id=tree.project_id, day=today, flaky_cases=1),
        FlakeDailySnapshot(project_id=tree.project_id, day=today - timedelta(days=3)),
        VendorHealthDaily(project_id=tree.project_id, day=today, vendor="cdn.example"),
    ])
    if quarantined:
        session.add(FlakeRecord(test_case_id=tree.case_id, step_id="s2",
                                is_quarantined=True, quarantine_source="manual"))
    await session.commit()


async def _count(session, table, where):
    return await _scalar(session, f"SELECT count(*) FROM {table} WHERE {where}")


async def test_failure_group_goes_with_its_last_result(session, scene):
    from app.api.endpoints.test_runs import delete_run

    d, s = scene.doomed, scene.sibling
    await delete_run(run_id=d.run_id, session=session, current_user=scene.admin)
    await _resync(session)
    assert await _count(session, "failurecluster", f"id = {d.cluster_id}") == 0
    # The case still has a result (its own run), so its flake record stays.
    assert await _count(session, "flakerecord", f"test_case_id = {d.case_id}") == 1
    assert await _count(session, "failurecluster", f"id = {s.cluster_id}") == 1


async def test_deleting_every_run_clears_flakes_trends_and_vendor_health(session, scene):
    from app.api.endpoints.test_runs import delete_runs

    d, s = scene.doomed, scene.sibling
    await _add_summaries(session, d, quarantined=True)
    await _add_summaries(session, s)
    await delete_runs(run_ids=[d.run_id, d.case_run_id], all=False,
                      session=session, current_user=scene.admin)
    await _resync(session)

    # Unowned, unquarantined score: gone. A quarantine is a decision: kept.
    assert await _count(session, "flakerecord",
                        f"test_case_id = {d.case_id} AND step_id IS NULL") == 0
    assert await _count(session, "flakerecord",
                        f"test_case_id = {d.case_id} AND is_quarantined") == 1
    assert await _count(session, "flakedailysnapshot", f"project_id = {d.project_id}") == 0
    assert await _count(session, "vendorhealthdaily", f"project_id = {d.project_id}") == 0

    # The other project in the workspace keeps all of it.
    assert await _count(session, "flakerecord", f"test_case_id = {s.case_id}") == 1
    assert await _count(session, "flakedailysnapshot", f"project_id = {s.project_id}") == 2
    assert await _count(session, "vendorhealthdaily", f"project_id = {s.project_id}") == 1
    assert await _count(session, "failurecluster", f"id = {s.cluster_id}") == 1


async def test_trend_days_with_a_remaining_run_are_kept(session, scene):
    from app.api.endpoints.test_runs import delete_run

    d = scene.doomed
    await _add_summaries(session, d)
    # Both scene runs were created now; push the surviving one back two days so
    # only the 3-days-ago trend point loses everything behind it.
    await session.exec(text("UPDATE testrun SET created_at = now() - interval '2 days' "
                            "WHERE id = :i"), params={"i": d.case_run_id})
    await session.commit()
    await delete_run(run_id=d.run_id, session=session, current_user=scene.admin)
    await _resync(session)

    days = (await session.exec(text(
        "SELECT day FROM flakedailysnapshot WHERE project_id = :p ORDER BY day"),
        params={"p": d.project_id})).all()
    assert [r[0] for r in days] == [datetime.utcnow().date()]
    # No run is left on today's date, so today's dependency-health row goes.
    assert await _count(session, "vendorhealthdaily", f"project_id = {d.project_id}") == 0
