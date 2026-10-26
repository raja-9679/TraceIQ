"""Run Test steps against a real Postgres: per-case grants (UserTestCaseAccess)
must not leak other tests' names through "used by" or the save check.

    ./run-tests-live.sh tests/integration/test_case_calls_db.py -q
"""
from __future__ import annotations

import pytest

from app.models import TestCase, User, UserTestCaseAccess
from tests.integration.test_entity_delete_db import (  # noqa: F401  (fixtures)
    pytestmark, scene, session,
)


def _call(cid):
    return {"id": f"call-{cid}", "type": "run-test", "params": {"case_id": cid}}


async def _case(session, tree, name, steps):
    c = TestCase(name=name, steps=steps, project_id=tree.project_id,
                 test_suite_id=tree.child_suite_id)
    session.add(c)
    await session.commit()
    await session.refresh(c)
    return c


async def test_used_by_lists_only_callers_the_user_may_see(session, scene):
    from app.api.endpoints.test_cases import get_case_used_by

    t = scene.doomed
    target = await _case(session, t, "Login", [{"id": "l1", "type": "goto", "value": "x"}])
    hidden = await _case(session, t, "Hidden caller", [_call(target.id)])
    shared = await _case(session, t, "Shared caller", [_call(target.id)])
    viewer = User(email="case-only@corp.example.com", full_name="Case only",
                  hashed_password="x", is_verified=True)
    session.add(viewer)
    await session.commit()
    session.add_all([
        UserTestCaseAccess(user_id=viewer.id, test_case_id=target.id, access_level="viewer"),
        UserTestCaseAccess(user_id=viewer.id, test_case_id=shared.id, access_level="viewer"),
    ])
    await session.commit()

    full = await get_case_used_by(case_id=target.id, session=session, current_user=scene.admin)
    assert [u.id for u in full.used_by] == [hidden.id, shared.id]
    limited = await get_case_used_by(case_id=target.id, session=session, current_user=viewer)
    assert [u.id for u in limited.used_by] == [shared.id]


async def test_save_check_treats_unreadable_tests_as_missing(session, scene):
    from app.services.access_service import access_service
    from app.services.case_calls import CaseCallError, check_calls_async

    t = scene.doomed
    secret = await _case(session, t, "Very secret flow", [{"id": "s1", "type": "goto", "value": "x"}])
    outsider = User(email="nobody2@corp.example.com", full_name="Nobody",
                    hashed_password="x", is_verified=True)
    session.add(outsider)
    await session.commit()

    def can_read(user):
        return lambda cid: access_service.has_test_case_access(user.id, cid, session)

    await check_calls_async(session, None, t.project_id, "ui_playwright", [_call(secret.id)],
                            can_read=can_read(scene.admin))
    with pytest.raises(CaseCallError) as exc:
        await check_calls_async(session, None, t.project_id, "ui_playwright", [_call(secret.id)],
                                can_read=can_read(outsider))
    assert "does not exist" in str(exc.value)
    assert "Very secret" not in str(exc.value)
