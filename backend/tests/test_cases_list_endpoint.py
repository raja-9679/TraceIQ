"""GET /api/cases — the agent-facing case listing (backs MCP list_cases).

Fake-session unit tests: filtering, tag matching, pagination clamps, access
denial. Run with:
    cd backend && pytest tests/test_cases_list_endpoint.py -v
"""
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.endpoints import test_cases as mod
from app.models import TestCase


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeAsyncSession:
    def __init__(self, rows):
        self._rows = rows

    async def exec(self, _stmt):
        return _Result(self._rows)


def _case(i, tags=None, suite=2):
    return TestCase(id=i, name=f"case {i}", project_id=1, test_suite_id=suite,
                    tags=tags or [], code_paths=[f"src/f{i}.py"],
                    last_validated_commit="abc" if i % 2 == 0 else None)


def _principal():
    p = MagicMock()
    p.user.id = 1
    return p


@pytest.fixture
def allow_access(monkeypatch):
    async def _yes(*a, **kw):
        return True
    monkeypatch.setattr(mod.access_service, "has_project_access", _yes)


async def test_lists_all_cases(allow_access):
    rows = [_case(i) for i in range(1, 4)]
    out = await mod.list_test_cases(
        project_id=1, session=FakeAsyncSession(rows), principal=_principal())
    assert out.total == 3
    assert [c.id for c in out.items] == [1, 2, 3]
    assert out.items[0].code_paths == ["src/f1.py"]
    assert out.items[1].last_validated_commit == "abc"


async def test_tag_filter(allow_access):
    rows = [_case(1, tags=["smoke"]), _case(2, tags=["regression"]),
            _case(3, tags=["smoke", "checkout"])]
    out = await mod.list_test_cases(
        project_id=1, tag="smoke",
        session=FakeAsyncSession(rows), principal=_principal())
    assert [c.id for c in out.items] == [1, 3]
    assert out.total == 2


async def test_pagination_clamps(allow_access):
    rows = [_case(i) for i in range(1, 11)]
    out = await mod.list_test_cases(
        project_id=1, limit=3, offset=8,
        session=FakeAsyncSession(rows), principal=_principal())
    assert [c.id for c in out.items] == [9, 10]
    assert out.total == 10
    # limit is clamped into [1, 200], offset floored at 0
    out = await mod.list_test_cases(
        project_id=1, limit=0, offset=-5,
        session=FakeAsyncSession(rows), principal=_principal())
    assert out.limit == 1 and out.offset == 0


async def test_access_denied(monkeypatch):
    async def _no(*a, **kw):
        return False
    monkeypatch.setattr(mod.access_service, "has_project_access", _no)
    with pytest.raises(HTTPException) as exc:
        await mod.list_test_cases(
            project_id=1, session=FakeAsyncSession([]), principal=_principal())
    assert exc.value.status_code == 403
