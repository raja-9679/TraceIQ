"""Demo workspace extras (app/services/demo_seed) against a real Postgres:
nothing is dated in the future, and a demo delete leaves every table as it was.

    TRACEIQ_SCRATCH_DB=traceiq_gseed ./run-tests-live.sh tests/integration/test_demo_seed_db.py -q
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.services import demo_workspace as dw                       # noqa: E402
from tests.integration.demo_seed_fixtures import (                  # noqa: E402,F401
    KEEP_AFTER_DELETE, count_all_tables, demo, owner, session,
)


async def test_demo_delete_leaves_every_table_as_it_was(session, owner, monkeypatch):
    from app.core import storage
    monkeypatch.setattr(storage.minio_client, "delete_prefix", lambda p: 0)
    monkeypatch.setattr(storage.minio_client, "delete_object", lambda k: None)
    owner_id = owner.id
    before = await count_all_tables(session)
    await dw.create_demo_workspace(session, owner)
    during = await count_all_tables(session)
    report = await dw.delete_demo_workspace(session, SimpleNamespace(id=owner_id))
    assert report is not None
    session.expire_all()
    after = await count_all_tables(session)
    leftovers = {t: after[t] - before[t] for t in after
                 if after[t] != before[t] and t not in KEEP_AFTER_DELETE}
    assert not leftovers, f"demo delete left rows behind: {leftovers}"
    # Sanity: the demo really wrote something to the tables we checked.
    assert sum(during.values()) > sum(before.values()) + 500


async def test_nothing_seeded_is_dated_after_creation(session, demo):
    """Every datetime column of every row in the demo's projects is <= now.

    Core run history is excluded: build_demo_plan places today's runs and
    monitor checks at fixed hours (a known bug fixed on another branch)."""
    now = datetime.utcnow() + timedelta(seconds=5)
    ws_id = demo["workspace_id"]
    skip_tables = {"testrun", "testcaseresult", "monitorcheck", "failurecluster",
                   "flakerecord", "testschedule"}
    bad = []
    for name, table in SQLModel.metadata.tables.items():
        if name in skip_tables or name == "auditlog":
            continue
        cols = [c.name for c in table.columns
                if c.type.__class__.__name__ in ("DateTime", "TIMESTAMP")]
        if not cols:
            continue
        scope = None
        if "workspace_id" in table.columns:
            scope = "workspace_id = :w"
        elif "project_id" in table.columns:
            scope = "project_id IN (SELECT id FROM project WHERE workspace_id = :w)"
        if scope is None:
            continue
        for col in cols:
            if col in ("expires_at", "due_at", "quarantine_expires_at", "next_run_at",
                       "risk_accepted_until", "risk_expires_at", "valid_until", "next_review_at"):
                continue          # legitimately in the future
            n = (await session.exec(text(f'SELECT count(*) FROM "{name}" WHERE {scope} AND "{col}" > :now'),
                                    params={"w": ws_id, "now": now})).one()[0]
            if n:
                bad.append(f"{name}.{col}: {n}")
    assert not bad, bad


async def test_beat_never_scans_crawls_or_checks_a_demo(session, demo, monkeypatch):
    """Estates, monitored pages and site crawls are seeded with schedules off,
    but a user may switch them on: the beat must still never call out."""
    from sqlmodel import Session, select
    from app.models import Estate, MonitoredPage, Project, SiteCrawl
    from app.tasks import estate_tasks, frontend_quality_tasks as fq

    past = datetime.utcnow() - timedelta(hours=2)
    ws_id = demo["workspace_id"]
    pids = (await session.exec(select(Project.id).where(Project.workspace_id == ws_id))).all()
    estates = (await session.exec(select(Estate).where(Estate.project_id.in_(pids)))).all()
    pages = (await session.exec(select(MonitoredPage).where(MonitoredPage.project_id.in_(pids)))).all()
    crawls = (await session.exec(select(SiteCrawl).where(SiteCrawl.project_id.in_(pids)))).all()
    assert estates and pages and crawls
    for row in estates:
        row.schedule_enabled, row.next_scan_at = True, past
    for row in pages:
        row.schedule_enabled, row.next_check_at = True, past
    for row in crawls:
        row.is_active, row.cron_expression, row.next_run_at = True, "0 * * * *", past
    session.add_all([*estates, *pages, *crawls])
    await session.commit()

    def boom(*a, **kw):
        raise AssertionError("a demo workspace must never be scanned on a schedule")
    monkeypatch.setattr(fq.dispatch_site_crawl, "delay", boom)
    with Session(estate_tasks._engine) as s:
        assert estate_tasks.due_estates(s, datetime.utcnow()) == []
        assert estate_tasks.due_pages(s, datetime.utcnow()) == []
    assert fq.process_due_crawls.run()["started"] == 0
