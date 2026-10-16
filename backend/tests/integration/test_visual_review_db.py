"""Visual review (WP14e) against a real Postgres: element vs full-page
baselines, group decisions in the audit trail, comment access control.

    ./run-tests-live.sh tests/integration/test_visual_review_db.py -q
"""
from __future__ import annotations

import os

import pytest
from fastapi import HTTPException
from sqlmodel import select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import visual_baselines, visual_review              # noqa: E402
from app.core.auth import AuthPrincipal                           # noqa: E402
from app.models import (                                          # noqa: E402
    AuditLog, TestRun, User, UserProjectAccess, UserWorkspace, VisualBaseline,
    VisualComparison, Project, TestCase,
)
from app.services.visual_review import element_key                # noqa: E402

from tests.integration.test_targeted_runs_db import (             # noqa: E402,F401
    no_queue, scene, session,
)

EL = element_key("header.site-nav")
HEADER = {"x": 0, "y": 0, "width": 1200, "height": 80, "pixels": 9000,
          "hash_candidate": "3c3c7e7e18183c3c", "hash_baseline": "0f0f0f0f0f0f0f0f"}


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch):
    from app.core.storage import minio_client
    copied = []
    monkeypatch.setattr(minio_client, "copy_object", lambda src, dst: copied.append((src, dst)))
    monkeypatch.setattr(minio_client, "object_exists", lambda key: False)
    monkeypatch.setattr(minio_client, "find_object", lambda prefix, suffix: None)
    monkeypatch.setattr(minio_client, "get_presigned_url", lambda key, *a, **kw: f"http://minio/{key}")
    monkeypatch.setattr(minio_client, "get_internal_presigned_url",
                        lambda key, *a, **kw: f"http://minio-internal/{key}", raising=False)
    return copied


def _me(scene):
    return AuthPrincipal(user=scene.user, api_key=None)


async def _run(session, scene) -> TestRun:
    run = TestRun(test_suite_id=scene.root_id, project_id=scene.project_id)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def _comparison(session, scene, run, case_id, *, step="s1", key="", regions=None, status="failed"):
    name = f"visual-{step}-{key}.png" if key else f"visual-{step}.png"
    c = VisualComparison(project_id=scene.project_id, test_run_id=run.id, test_case_id=case_id,
                         step_id=step, element_key=key, status=status,
                         candidate_key=f"runs/{run.id}/screenshots/job-{name}",
                         diff_ratio=0.05 if regions else 0.0,
                         diff_pixels=sum(r["pixels"] for r in regions or []),
                         regions=regions or [])
    session.add(c)
    await session.commit()
    await session.refresh(c)
    return c


async def test_element_and_full_page_baselines_of_one_step_never_collide(session, scene):
    case_id = scene.root_cases[0]
    run = await _run(session, scene)
    full = await _comparison(session, scene, run, case_id)
    elem = await _comparison(session, scene, run, case_id, key=EL)
    for c in (full, elem):
        await visual_baselines.promote_baseline(
            visual_baselines.PromoteBaselineRequest(
                test_case_id=case_id, step_id="s1", run_id=run.id, comparison_id=c.id),
            principal=_me(scene), session=session)

    rows = (await session.exec(select(VisualBaseline).where(
        VisualBaseline.test_case_id == case_id))).all()
    assert sorted(r.element_key for r in rows) == ["", EL]
    by_key = {r.element_key: r for r in rows}
    assert EL in by_key[EL].image_url and EL not in by_key[""].image_url

    secret = "s3cret"
    from app.core.config import settings
    settings.WEBHOOK_SECRET = secret
    got_full = await visual_baselines.internal_resolve_baseline(
        test_case_id=case_id, step_id="s1", x_worker_secret=secret, session=session)
    got_el = await visual_baselines.internal_resolve_baseline(
        test_case_id=case_id, step_id="s1", element_key=EL, x_worker_secret=secret, session=session)
    assert got_full["id"] == by_key[""].id and got_el["id"] == by_key[EL].id

    # A second accept of the element slot updates it in place, still apart.
    await visual_baselines.promote_baseline(
        visual_baselines.PromoteBaselineRequest(
            test_case_id=case_id, step_id="s1", run_id=run.id, comparison_id=elem.id),
        principal=_me(scene), session=session)
    assert len((await session.exec(select(VisualBaseline).where(
        VisualBaseline.test_case_id == case_id))).all()) == 2


async def test_an_old_worker_never_gets_an_element_baseline(session, scene):
    case_id = scene.root_cases[0]
    run = await _run(session, scene)
    elem = await _comparison(session, scene, run, case_id, key=EL)
    await visual_baselines.promote_baseline(
        visual_baselines.PromoteBaselineRequest(
            test_case_id=case_id, step_id="s1", run_id=run.id, comparison_id=elem.id),
        principal=_me(scene), session=session)
    from app.core.config import settings
    settings.WEBHOOK_SECRET = "s3cret"
    with pytest.raises(HTTPException) as exc:
        # No element_key: what a pre-WP14e worker sends (it took a full page).
        await visual_baselines.internal_resolve_baseline(
            test_case_id=case_id, step_id="s1", x_worker_secret="s3cret", session=session)
    assert exc.value.status_code == 404


async def test_a_full_page_candidate_is_not_promoted_into_an_element_slot(session, scene, fake_storage):
    case_id = scene.root_cases[0]
    run = await _run(session, scene)
    full = await _comparison(session, scene, run, case_id)
    # Asking for the element slot without a matching element candidate: the
    # comparison's own (full-page) slot wins, never the requested one.
    out = await visual_baselines.promote_baseline(
        visual_baselines.PromoteBaselineRequest(
            test_case_id=case_id, step_id="s1", run_id=run.id, element_key=EL, comparison_id=full.id),
        principal=_me(scene), session=session)
    assert out.element_key == ""
    with pytest.raises(HTTPException) as exc:
        await visual_baselines.promote_baseline(
            visual_baselines.PromoteBaselineRequest(
                test_case_id=case_id, step_id="s1", run_id=run.id, element_key=EL),
            principal=_me(scene), session=session)
    assert exc.value.status_code == 404


async def test_group_accept_records_one_audit_row_per_baseline(session, scene):
    run = await _run(session, scene)
    cases = scene.root_cases + [scene.child_cases[0]]
    comps = [await _comparison(session, scene, run, cid, regions=[HEADER]) for cid in cases]
    other = await _comparison(session, scene, run, scene.child_cases[1], regions=[
        {"x": 0, "y": 2000, "width": 600, "height": 200, "pixels": 5000,
         "hash_candidate": "aaaaaaaa55555555", "hash_baseline": "5555aaaa5555aaaa"}])

    review = await visual_review.review_run(scene.project_id, run.id, principal=_me(scene), session=session)
    groups = {g["key"]: g for g in review["groups"]}
    key = f"g{comps[0].id}"
    assert groups[key]["item_ids"] == [c.id for c in comps]
    assert groups[f"g{other.id}"]["size"] == 1

    out = await visual_review.accept_group(
        scene.project_id, key, visual_review.GroupDecision(run_id=run.id),
        principal=_me(scene), session=session)
    assert len(out["accepted"]) == 3 and out["skipped"] == []
    baseline_ids = {a["baseline_id"] for a in out["accepted"]}
    # The audit log is not truncated between tests: keep to this run's rows.
    rows = [r for r in (await session.exec(select(AuditLog).where(
        AuditLog.entity_type == "visual_baseline",
        AuditLog.action == "visual_baseline.accept"))).all()
        if (r.changes or {}).get("run_id") == run.id]
    assert {r.entity_id for r in rows} == baseline_ids
    assert all(r.changes["group_key"] == key and r.changes["run_id"] == run.id for r in rows)
    assert {r.changes["test_case_id"] for r in rows} == set(cases)

    review = await visual_review.review_run(scene.project_id, run.id, principal=_me(scene), session=session)
    decided = {i["id"]: (i["decision"] or {}).get("action") for i in review["items"]}
    assert all(decided[c.id] == "accept" for c in comps) and decided[other.id] is None

    kept = await visual_review.keep_group(
        scene.project_id, key, visual_review.GroupDecision(run_id=run.id, note="regression"),
        principal=_me(scene), session=session)
    assert len(kept["kept"]) == 3
    keeps = [r for r in (await session.exec(select(AuditLog).where(
        AuditLog.action == "visual_baseline.keep"))).all()
        if (r.changes or {}).get("run_id") == run.id]
    assert {r.entity_id for r in keeps} == baseline_ids


async def _member(session, scene, email, project_id, level):
    user = User(email=email, full_name=email.split("@")[0], hashed_password="x", is_verified=True)
    session.add(user)
    await session.flush()
    ws_id = (await session.get(Project, project_id)).workspace_id
    session.add(UserWorkspace(user_id=user.id, workspace_id=ws_id, role="member"))
    session.add(UserProjectAccess(user_id=user.id, project_id=project_id, access_level=level))
    await session.commit()
    return AuthPrincipal(user=user, api_key=None)


async def test_comments_viewer_reads_editor_writes_other_project_denied(session, scene):
    run = await _run(session, scene)
    comp = await _comparison(session, scene, run, scene.root_cases[0], regions=[HEADER])
    viewer = await _member(session, scene, "viewer@corp.example.com", scene.project_id, "viewer")
    editor = await _member(session, scene, "editor@corp.example.com", scene.project_id, "editor")
    outsider = await _member(session, scene, "out@corp.example.com", scene.other_project_id, "editor")

    made = await visual_review.add_comment(
        scene.project_id,
        visual_review.CommentCreate(body="<b>header</b> moved\x07 on purpose?", comparison_id=comp.id),
        principal=editor, session=session)
    assert made["body"] == "<b>header</b> moved on purpose?"    # stored as plain text
    group_comment = await visual_review.add_comment(
        scene.project_id,
        visual_review.CommentCreate(body="whole group", run_id=run.id, group_key=f"g{comp.id}"),
        principal=editor, session=session)
    assert group_comment["group_key"] == f"g{comp.id}"

    seen = await visual_review.list_comments(scene.project_id, comparison_id=comp.id,
                                             principal=viewer, session=session)
    assert [c["id"] for c in seen] == [made["id"]]
    assert seen[0]["author"]["name"] == "editor"

    with pytest.raises(HTTPException) as exc:
        await visual_review.add_comment(
            scene.project_id, visual_review.CommentCreate(body="me too", comparison_id=comp.id),
            principal=viewer, session=session)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await visual_review.resolve_comment(scene.project_id, made["id"],
                                            visual_review.CommentResolve(), principal=viewer,
                                            session=session)
    assert exc.value.status_code == 403
    for call in (
        visual_review.list_comments(scene.project_id, comparison_id=comp.id,
                                    principal=outsider, session=session),
        visual_review.review_run(scene.project_id, run.id, principal=outsider, session=session),
    ):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 403
    # An editor elsewhere cannot hang a comment on this project's item via
    # their own project either.
    with pytest.raises(HTTPException) as exc:
        await visual_review.add_comment(
            scene.other_project_id, visual_review.CommentCreate(body="x", comparison_id=comp.id),
            principal=outsider, session=session)
    assert exc.value.status_code == 404

    resolved = await visual_review.resolve_comment(
        scene.project_id, made["id"], visual_review.CommentResolve(resolved=True),
        principal=editor, session=session)
    assert resolved["resolved"] is True
    actions = [r.action for r in (await session.exec(select(AuditLog).where(
        AuditLog.entity_type == "visual_review_comment"))).all()
        if (r.changes or {}).get("project_id") == scene.project_id]
    assert sorted(actions) == sorted(["visual_review_comment.create"] * 2
                                     + ["visual_review_comment.resolve"])

    review = await visual_review.review_run(scene.project_id, run.id, principal=viewer, session=session)
    item = next(i for i in review["items"] if i["id"] == comp.id)
    assert item["comment_count"] == 1 and item["open_comment_count"] == 0
    assert review["groups"][0]["comment_count"] == 1


async def test_ingest_from_a_job_result_and_case_delete_cleans_up(session, scene):
    """The aggregator path (sync session) stores comparisons; deleting the
    case removes them and their threads."""
    from sqlmodel import Session, create_engine
    from app.core.config import db_url_for, settings
    from app.services.visual_review import persist_comparisons
    run = await _run(session, scene)
    case_id = scene.root_cases[0]
    engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True))
    with Session(engine) as s:
        r = s.get(TestRun, run.id)
        persist_comparisons(s, r, case_id, "cart", [
            {"step_id": "s1", "candidate_file": f"visual-s1-{EL}.png", "element_key": EL,
             "selector": "header.site-nav", "status": "failed", "diff_ratio": 0.2, "diff_pixels": 9000,
             "regions": [HEADER]},
        ], [f"runs/{run.id}/screenshots/job-visual-s1-{EL}.png"])
        s.commit()
    engine.dispose()
    rows = (await session.exec(select(VisualComparison).where(
        VisualComparison.test_run_id == run.id))).all()
    assert len(rows) == 1 and rows[0].element_key == EL and rows[0].candidate_key.endswith(f"{EL}.png")
    await visual_review.add_comment(
        scene.project_id, visual_review.CommentCreate(body="hm", comparison_id=rows[0].id),
        principal=_me(scene), session=session)

    from app.services.entity_delete import cascade_delete
    await cascade_delete(session, "testcase", [case_id])
    await session.commit()
    assert (await session.exec(select(VisualComparison).where(
        VisualComparison.test_case_id == case_id))).all() == []
    assert await session.get(TestCase, case_id) is None
