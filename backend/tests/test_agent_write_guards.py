"""Write-policy guards behind the MCP surface.

Invariants:
  1. The proposal review queue is human-only: accept rejects API-key callers
     with 403 even when the key's owner has editor access. (get_current_user
     is a shim over get_current_principal, so API keys authenticate fine on
     read endpoints — the queue guard is the one place caller *kind* matters.)
  2. GET /api/flakes refuses unscoped listings and enforces project access —
     the table is cross-tenant, so an unfiltered dump would leak case ids and
     failure messages across workspaces.

Run with:
    cd backend && pytest tests/test_agent_write_guards.py -v
"""
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.core.auth import AuthPrincipal


async def test_proposal_accept_rejects_api_keys(monkeypatch):
    from app.api import agent_ownership
    from app.models import CaseProposal

    proposal = MagicMock(spec=CaseProposal)
    proposal.status = "pending"
    proposal.project_id = 1

    class FakeSession:
        async def get(self, model, pk):
            return proposal

    async def allow_access(*a, **kw):
        return True

    monkeypatch.setattr(
        agent_ownership.access_service, "has_project_access", allow_access)

    principal = AuthPrincipal(user=MagicMock(id=1), api_key=MagicMock())
    assert principal.is_api_caller

    with pytest.raises(HTTPException) as exc:
        await agent_ownership.accept_proposal(
            proposal_id=1, note=None, principal=principal,
            session=FakeSession())
    assert exc.value.status_code == 403
    assert "API keys" in exc.value.detail


def _principal():
    p = MagicMock()
    p.user.id = 1
    return p


async def test_flakes_list_requires_scope():
    from app.api import flake_records
    with pytest.raises(HTTPException) as exc:
        await flake_records.list_flakes(
            test_case_id=None, project_id=None, quarantined_only=False,
            principal=_principal(), session=MagicMock())
    assert exc.value.status_code == 422


async def test_flakes_list_enforces_project_access(monkeypatch):
    from app.api import flake_records

    async def deny(*a, **kw):
        return False

    monkeypatch.setattr(
        flake_records.access_service, "has_project_access", deny)
    with pytest.raises(HTTPException) as exc:
        await flake_records.list_flakes(
            test_case_id=None, project_id=7, quarantined_only=False,
            principal=_principal(), session=MagicMock())
    assert exc.value.status_code == 403
