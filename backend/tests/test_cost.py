"""Cost intelligence math and the price-table validation (WP12d) — no database."""
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.services import cost
from app.services import instance_settings as insvc


def _prices(currency="EUR", models=None, minute=None):
    return cost.load_prices({
        "COST_CURRENCY": currency,
        "COST_LLM_PRICES": models if models is not None else {},
        "COST_BROWSER_MINUTE_PRICE": minute,
    }.get)


# --- rounding / arithmetic ------------------------------------------------------


def test_money_rounds_half_up_to_the_minor_unit_once():
    assert cost.money(Decimal("0.125"), "EUR") == 0.13
    assert cost.money(Decimal("0.124999"), "EUR") == 0.12
    assert cost.money(Decimal("2.5"), "JPY") == 3.0
    assert cost.money(Decimal("1.234"), None) is None
    assert cost.money(None, "EUR") is None


def test_token_cost_is_per_million_input_and_output():
    price = (Decimal("3"), Decimal("15"))
    assert cost.token_cost(1_000_000, 0, price) == Decimal(3)
    assert cost.token_cost(200_000, 100_000, price) == Decimal("2.1")
    assert cost.minutes(90_000) == Decimal("1.5")


def test_budget_thresholds():
    assert cost.budget_crossed(Decimal("79.99"), Decimal(100)) == []
    assert cost.budget_crossed(Decimal(80), Decimal(100)) == [80]
    assert cost.budget_crossed(Decimal(130), Decimal(100)) == [80, 100]
    assert cost.budget_crossed(Decimal(5), Decimal(0)) == []


# --- validation -----------------------------------------------------------------


def test_currency_validation():
    assert cost.validate_currency(" eur ") == "EUR"
    assert cost.validate_currency("") is None
    for bad in ("EURO", "€", "12A", 5):
        with pytest.raises(cost.PriceTableError):
            cost.validate_currency(bad)


def test_price_table_validation():
    ok = cost.validate_llm_prices('{"claude-x": {"input_per_1m": "3", "output_per_1m": 15}}')
    assert ok == {"claude-x": {"input_per_1m": 3.0, "output_per_1m": 15.0}}
    assert cost.validate_llm_prices("") == {}
    bad_tables = [
        "not json",
        [],
        {"m": 3},
        {"m": {"input_per_1m": 1}},
        {"m": {"input_per_1m": -1, "output_per_1m": 1}},
        {"m": {"input_per_1m": "NaN", "output_per_1m": 1}},
        {"m": {"input_per_1m": True, "output_per_1m": 1}},
        {"m": {"input_per_1m": 1, "output_per_1m": 1, "cached": 1}},
        {"M": {"input_per_1m": 1, "output_per_1m": 1}, "m": {"input_per_1m": 1, "output_per_1m": 1}},
        {" ": {"input_per_1m": 1, "output_per_1m": 1}},
        {"m": {"input_per_1m": 10_000_000, "output_per_1m": 1}},
    ]
    for bad in bad_tables:
        with pytest.raises(cost.PriceTableError):
            cost.validate_llm_prices(bad)


def test_minute_price_validation():
    assert cost.validate_minute_price("0.008") == "0.008"
    assert cost.validate_minute_price(None) is None
    with pytest.raises(cost.PriceTableError):
        cost.validate_minute_price("-1")
    with pytest.raises(cost.PriceTableError):
        cost.validate_minute_price("cheap")


def test_bad_stored_values_are_ignored_not_fatal():
    p = cost.load_prices({"COST_CURRENCY": "EURO", "COST_LLM_PRICES": "{",
                          "COST_BROWSER_MINUTE_PRICE": "x"}.get)
    assert not p.enabled and p.models == {} and p.browser_minute is None


def test_json_setting_type_round_trips():
    d = insvc.REGISTRY["COST_LLM_PRICES"]
    assert d.type == "json" and d.group == "cost"
    raw = insvc.serialize(d, {"m": {"input_per_1m": 1.0, "output_per_1m": 2.0}})
    assert insvc._parse(d, raw) == {"m": {"input_per_1m": 1.0, "output_per_1m": 2.0}}
    assert insvc._parse(d, "") is None
    # Nothing ships with a price: no money until an admin sets one.
    for key in ("COST_CURRENCY", "COST_LLM_PRICES", "COST_BROWSER_MINUTE_PRICE"):
        assert insvc.env_default(key) in (None, "", {})


# --- report ---------------------------------------------------------------------

DAYS = ["2026-10-14", "2026-10-15", "2026-10-16"]


def _llm(day, model, tin, tout, project=1, feature="failure_analysis", provider="anthropic"):
    return {"day": date.fromisoformat(day), "project_id": project, "provider": provider,
            "model": model, "feature": feature, "input_tokens": tin, "output_tokens": tout}


def _report(prices, llm=(), browser=(), llm_runs=(), browser_runs=(), meta=None):
    return cost.build_report(prices, days=DAYS, llm_rows=list(llm), browser_rows=list(browser),
                             llm_run_rows=list(llm_runs), browser_run_rows=list(browser_runs),
                             run_meta=meta or {})


def test_missing_prices_are_not_priced_not_zero():
    p = _prices(models={"claude-x": {"input_per_1m": 3, "output_per_1m": 15}})
    r = _report(p, llm=[_llm("2026-10-15", "claude-x", 1_000_000, 0),
                        _llm("2026-10-15", "mystery", 500, 500)])
    by_model = {m["model"]: m for m in r["llm"]["by_model"]}
    assert by_model["claude-x"]["amount"] == 3.0 and by_model["claude-x"]["priced"]
    assert by_model["mystery"]["amount"] is None and by_model["mystery"]["unpriced_tokens"] == 1000
    assert r["llm"]["amount"] == 3.0 and not r["llm"]["priced"]
    assert r["total"] == 3.0 and r["total_is_partial"]
    assert r["browser"]["amount"] is None and not r["browser"]["priced"]


def test_no_currency_means_no_money_anywhere():
    p = _prices(currency="", models={"claude-x": {"input_per_1m": 3, "output_per_1m": 15}}, minute="0.01")
    r = _report(p, llm=[_llm("2026-10-15", "claude-x", 1_000_000, 0)],
                browser=[{"day": date(2026, 10, 15), "project_id": 1, "ms": 60_000}])
    assert r["currency"] is None and not r["priced"]
    assert r["total"] is None and r["llm"]["amount"] is None and r["browser"]["amount"] is None
    assert all(d["total"] is None for d in r["daily"])
    assert r["browser"]["minutes"] == 1.0          # usage is still shown


def test_totals_trend_and_top_runs():
    p = _prices(currency="USD", models={"m": {"input_per_1m": 1, "output_per_1m": 2}}, minute="0.5")
    r = _report(
        p,
        llm=[_llm("2026-10-14", "m", 1_000_000, 1_000_000), _llm("2026-10-16", "m", 0, 500_000, feature="selector_heal")],
        browser=[{"day": date(2026, 10, 16), "project_id": 1, "ms": 120_000}],
        llm_runs=[{"run_id": 7, "model": "m", "input_tokens": 1_000_000, "output_tokens": 0}],
        browser_runs=[{"run_id": 8, "ms": 600_000}, {"run_id": 7, "ms": 60_000}],
        meta={7: {"suite_name": "Checkout", "project_id": 1, "created_at": None}})
    assert r["llm"]["amount"] == 4.0                      # 1 + 2 + 1
    assert r["browser"]["amount"] == 1.0 and r["browser"]["minutes"] == 2.0
    assert r["total"] == 5.0 and not r["total_is_partial"]
    daily = {d["date"]: d for d in r["daily"]}
    assert daily["2026-10-14"]["total"] == 3.0
    assert daily["2026-10-15"]["total"] == 0.0             # gaps are zero, not missing
    assert daily["2026-10-16"]["total"] == 2.0
    feats = {f["feature"]: f["amount"] for f in r["llm"]["by_feature"]}
    assert feats == {"failure_analysis": 3.0, "selector_heal": 1.0}
    # run 8: 10 min x 0.5 = 5.0 ; run 7: 1.0 llm + 0.5 browser = 1.5
    assert [t["run_id"] for t in r["top_runs"]] == [8, 7]
    assert r["top_runs"][1]["total"] == 1.5 and r["top_runs"][1]["suite_name"] == "Checkout"
    assert "approximat" in r["browser"]["note"]


def test_project_spend_groups_by_project():
    p = _prices(models={"m": {"input_per_1m": 1, "output_per_1m": 1}}, minute="1")
    spend = cost.project_spend(p, [_llm("2026-10-14", "m", 1_000_000, 0, project=2),
                                   _llm("2026-10-14", "m", 1_000_000, 0, project=None)],
                               [{"project_id": 2, "ms": 60_000}])
    assert spend == {2: Decimal(2), None: Decimal(1)}


def test_window_is_calendar_days_ending_today():
    since, until, days = cost.window(3, now=datetime(2026, 10, 16, 15, 0))
    assert since == datetime(2026, 10, 14) and days == DAYS
    assert cost.month_start(datetime(2026, 10, 16, 9)) == datetime(2026, 10, 1)


def test_budget_task_and_routing_are_registered():
    from app.core.celery_app import celery_app
    assert celery_app.conf.task_routes["app.tasks.cost_tasks.check_project_budgets"] == "main-queue"
    assert any(e["task"] == "app.tasks.cost_tasks.check_project_budgets"
               for e in celery_app.conf.beat_schedule.values())


def test_budget_alert_goes_through_the_project_rules_only(monkeypatch):
    from types import SimpleNamespace as NS
    from app.services import project_notifications as pn
    from app.tasks import cost_tasks, notification_tasks as nt

    project = NS(id=3, name="Shop")
    monkeypatch.setattr(nt, "get_notification_settings", lambda p, s: {
        "enabled": True, "email_enabled": True, "slack_enabled": True,
        "slack_webhook_url": "https://hooks.example.com/project-3",
        "teams_enabled": False, "teams_webhook_url": None})
    asked = {}

    def recipients(session, proj, starter, run_failed):
        asked.update(project=proj.id, starter=starter, run_failed=run_failed)
        return ["owner@corp.example.com"]

    monkeypatch.setattr(pn, "email_recipients", recipients)
    mails, posts = [], []
    monkeypatch.setattr(cost_tasks, "_send_email", lambda to, subj, text: mails.append((to, subj)))
    monkeypatch.setattr(nt, "post_webhook", lambda url, payload: posts.append(url) or NS(raise_for_status=lambda: None))
    used = cost_tasks.deliver_budget_alert(None, project, 80, 8.0, 10.0, "EUR", "2026-10")
    assert used == ["email", "slack"]
    assert asked == {"project": 3, "starter": None, "run_failed": True}
    assert mails[0][0] == ["owner@corp.example.com"] and "80%" in mails[0][1]
    assert posts == ["https://hooks.example.com/project-3"]

    monkeypatch.setattr(nt, "get_notification_settings", lambda p, s: {"enabled": False})
    assert cost_tasks.deliver_budget_alert(None, project, 100, 11.0, 10.0, "EUR", "2026-10") == []
