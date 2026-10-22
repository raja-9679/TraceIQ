"""GET /workspaces/{id}/billing on an install with no plan rows.

Default plans were seeded by the legacy billing migration's data step, which
the 2026-09-07 squash did not carry over, so databases built from the squashed
root (or by the old create_all bootstrap) have none. Without plans nothing is
capped — check_quota treats missing limits as unlimited — and the billing page
must say so instead of answering 500 "No plans configured".
"""
from types import SimpleNamespace

from app.api import billing as billing_api
from app.models import Plan


async def _status(monkeypatch, plan=None, sub=None, ai_cap=0):
    async def member(session, workspace_id, user_id):
        return SimpleNamespace(role="admin")

    async def resolve_plan(session, workspace_id):
        return plan, sub

    async def get_usage(session, workspace_id, metric, period=None):
        return {"runs": 12}.get(metric, 0)

    monkeypatch.setattr(billing_api, "_member", member)
    monkeypatch.setattr(billing_api.billing_svc, "resolve_plan", resolve_plan)
    async def ai_today(workspace_id):
        return 3

    class _Session:
        async def get(self, model, pk):
            return SimpleNamespace(ai_generation_limit_daily=ai_cap)

    monkeypatch.setattr(billing_api.billing_svc, "get_usage", get_usage)
    monkeypatch.setattr(billing_api.billing_svc, "ai_generations_today", ai_today)
    return await billing_api.workspace_billing(
        workspace_id=5, session=_Session(), current_user=SimpleNamespace(id=1))


async def test_no_plans_reports_unlimited_instead_of_500(monkeypatch):
    status = await _status(monkeypatch)
    assert status.plan.name == "unlimited"
    assert status.plan.limits == {}
    assert status.limits == {"ai_daily": 0}
    assert status.usage["runs"] == 12
    assert status.status == "active"


async def test_a_real_plan_is_reported_unchanged(monkeypatch):
    pro = Plan(id=2, name="pro", display_name="Pro", price_cents=4900,
               limits={"monthly_runs": 10000}, is_active=True)
    status = await _status(monkeypatch, plan=pro)
    assert status.plan.name == "pro"
    assert status.limits == {"monthly_runs": 10000, "ai_daily": 0}


async def test_ai_generations_are_todays_count_against_the_enforced_cap(monkeypatch):
    # The page's "AI generations today" used to read a monthly UsageRecord
    # metric nothing ever wrote, so it was always 0, against a plan `ai_daily`
    # nothing enforced. It now shows the daily counter the cap is enforced on
    # and the workspace's ai_generation_limit_daily.
    pro = Plan(id=2, name="pro", display_name="Pro", price_cents=4900,
               limits={"ai_daily": 500}, is_active=True)
    status = await _status(monkeypatch, plan=pro, ai_cap=25)
    assert status.usage["ai_generations"] == 3
    assert status.limits["ai_daily"] == 25
