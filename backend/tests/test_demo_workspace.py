"""Demo workspace generator + guards (WP13c, decision C5)."""
from datetime import date
from types import SimpleNamespace

from app.services import demo_workspace as dw
from app.services.test_packs import catalogue_step_types

TODAY = date(2026, 10, 16)


def test_same_seed_same_plan():
    assert dw.build_demo_plan(42, TODAY) == dw.build_demo_plan(42, TODAY)


def test_different_seed_different_history():
    a = dw.build_demo_plan(1, TODAY)
    b = dw.build_demo_plan(2, TODAY)
    outcomes = lambda p: [x["status"] for r in p["runs"] for x in r["results"]]  # noqa: E731
    assert outcomes(a) != outcomes(b)


def test_history_spans_thirty_days_ending_today():
    plan = dw.build_demo_plan(dw.DEFAULT_SEED, TODAY)
    days = {r["created_at"].date() for r in plan["runs"]}
    assert len(days) == 30 and max(days) == TODAY
    assert len(plan["runs"]) == 30 * 2 * 2        # 2 projects x nightly + CI
    assert len(plan["monitor"]["checks"]) == 30 * 4


def test_history_is_mostly_green_with_the_story_reports_need():
    plan = dw.build_demo_plan(dw.DEFAULT_SEED, TODAY)
    results = [x for r in plan["runs"] for x in r["results"]]
    failed = [x for x in results if x["status"] == "failed"]
    assert 0.02 < len(failed) / len(results) < 0.15
    assert {x["case"] for x in results if x["is_flaky"]} == {"add_todo"}
    cluster = [x for x in results if x["cluster"]]
    assert len(cluster) == plan["clusters"][0]["occurrence_count"] >= 4
    assert plan["findings"] and plan["flakes"] and plan["flakes"][0]["flake_score"] > 0.33
    assert any(not c["up"] for c in plan["monitor"]["checks"])


def test_steps_only_name_allowed_hosts_and_known_step_types():
    plan = dw.build_demo_plan(dw.DEFAULT_SEED, TODAY)
    assert set(dw.plan_hosts(plan)) <= set(dw.ALLOWED_HOSTS)
    known = catalogue_step_types()
    for proj in plan["projects"]:
        for suite in proj["suites"]:
            for case in suite["cases"]:
                assert {s["type"] for s in case["steps"]} <= known, case["name"]


def test_step_ids_are_stable_and_unique():
    plan = dw.build_demo_plan(dw.DEFAULT_SEED, TODAY)
    ids = [s["id"] for p in plan["projects"] for su in p["suites"] for c in su["cases"] for s in c["steps"]]
    assert len(ids) == len(set(ids))


class _Session:
    def __init__(self, rows):
        self.rows = rows

    def get(self, model, key):
        return self.rows.get((model.__name__, key))


def test_guard_flags_projects_in_a_demo_workspace_only():
    s = _Session({("Project", 1): SimpleNamespace(workspace_id=10),
                  ("Project", 2): SimpleNamespace(workspace_id=20),
                  ("Workspace", 10): SimpleNamespace(is_demo=True),
                  ("Workspace", 20): SimpleNamespace(is_demo=False)})
    assert dw.is_demo_project_sync(s, 1) is True
    assert dw.is_demo_project_sync(s, 2) is False
    assert dw.is_demo_project_sync(s, 3) is False
    assert dw.is_demo_project_sync(s, None) is False


def test_notification_settings_are_off_for_a_demo_project(monkeypatch):
    from app.tasks import notification_tasks as nt
    monkeypatch.setattr(nt.insvc, "effective", lambda key: True)
    s = _Session({("Project", 1): SimpleNamespace(id=1, workspace_id=10, notification_settings=None),
                  ("Workspace", 10): SimpleNamespace(is_demo=True)})
    cfg = nt.get_notification_settings(s.get(type("Project", (), {}), 1), s)
    assert cfg["enabled"] is False and cfg["email_enabled"] is False and cfg["demo"] is True
