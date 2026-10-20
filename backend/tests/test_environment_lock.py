"""Exclusive environments (WP16c, decision V3) — the lease, without a database.

The live-DB half (every terminal path through the real dispatcher, cancel,
and the run page view) is tests/integration/test_env_lock_validation_db.py.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models import TestStatus
from app.services import environment_lock as el
from app.services import policy
from app.services.policy import PolicyContext, evaluate_sync
from tests.fake_lease_redis import FakeLeaseRedis


class Session:
    """get() over environments and runs; exec() present so lock_target looks."""

    def __init__(self, envs=(), runs=()):
        self.envs = {e.id: e for e in envs}
        self.runs = {r.id: r for r in runs}

    def get(self, model, ident):
        return (self.envs if model.__name__ == "ProjectEnvironment" else self.runs).get(ident)

    def exec(self, *_a, **_k):  # pragma: no cover - default-env path not used here
        raise AssertionError("pinned environments only")

    def get_bind(self):
        return None


def env(exclusive=True, id=5):
    return SimpleNamespace(id=id, name="staging", project_id=1, exclusive=exclusive)


def run(id, status=TestStatus.PENDING, env_id=5, **kw):
    return SimpleNamespace(**{"id": id, "project_id": 1, "environment_id": env_id,
                              "status": status, "target_url": None, **kw})


@pytest.fixture
def r(monkeypatch):
    fake = FakeLeaseRedis()
    monkeypatch.setattr(el, "_client", fake)
    return fake


@pytest.fixture(autouse=True)
def no_deny_log(monkeypatch):
    monkeypatch.setattr(policy, "_write_sync", lambda *a, **k: None)


def test_first_run_takes_the_lease_second_waits(r):
    s = Session([env()])
    a, b = run(1), run(2)
    assert el.try_acquire(a, s)["acquired"] is True
    second = el.try_acquire(b, s)
    assert second["acquired"] is False and second["holder"]["run_id"] == 1
    assert el.holder(5)["run_id"] == 1 and el.holder(5)["since"]
    assert el.waiting(2)["holder_run_id"] == 1 and el.waiting(2)["environment"] == "staging"
    assert 0 < r.ttl("envlock:env:5") <= el.LEASE_TTL_SECONDS


def test_readmitting_the_holder_keeps_its_lease(r):
    s = Session([env()])
    a = run(1)
    el.try_acquire(a, s)
    r.advance(100)
    assert el.try_acquire(a, s)["acquired"] is True
    assert r.ttl("envlock:env:5") == el.LEASE_TTL_SECONDS     # extended, not re-taken


def test_non_exclusive_and_retargeted_runs_need_no_lease(r):
    assert el.try_acquire(run(1), Session([env(exclusive=False)])) == {"acquired": True,
                                                                       "environment_id": None}
    retarget = run(2, target_url="https://other.example.com")
    assert el.try_acquire(retarget, Session([env()]))["environment_id"] is None
    assert r.data == {}


def test_release_is_compare_and_delete(r):
    s = Session([env()])
    el.try_acquire(run(1), s)
    el.try_acquire(run(2), s)                 # waiting
    assert el.release_for_run(2) is None      # a waiter cannot drop the holder's lease
    assert el.holder(5)["run_id"] == 1
    assert el.waiting(2) is None              # but its wait note is gone
    assert el.release_for_run(1) == 5
    assert el.holder(5) is None
    assert el.try_acquire(run(2), s)["acquired"] is True


def test_stale_lease_expires_on_its_own(r):
    s = Session([env()])
    el.try_acquire(run(1), s)
    r.advance(el.LEASE_TTL_SECONDS + 1)       # beat died: nothing renewed it
    assert el.holder(5) is None
    assert el.try_acquire(run(2), s)["acquired"] is True


def test_heartbeat_renews_running_and_drops_finished_holders(r):
    holder = run(1)
    s = Session([env(), env(id=6)], [holder, run(3, env_id=6)])
    el.try_acquire(holder, s)
    el.try_acquire(s.runs[3], s)
    holder.status = TestStatus.RUNNING
    s.runs[3].status = TestStatus.CANCELLED   # finished, but its release was lost
    r.advance(150)
    assert el.refresh(s) == {"renewed": 1, "released": 1}
    assert r.ttl("envlock:env:5") == el.LEASE_TTL_SECONDS
    assert el.holder(6) is None


def test_heartbeat_gives_a_pending_holder_only_a_grace_period(r):
    stuck = run(1)                            # admitted, dispatcher died before RUNNING
    s = Session([env()], [stuck])
    el.try_acquire(stuck, s)
    later = datetime.utcnow() + timedelta(seconds=el.PENDING_GRACE_SECONDS + 5)
    assert el.refresh(s, now=later)["released"] == 1
    assert el.holder(5) is None


def test_heartbeat_drops_a_lease_whose_run_was_deleted(r):
    s = Session([env()])
    el.try_acquire(run(1), s)
    assert el.refresh(s)["released"] == 1


def test_force_release_returns_the_previous_holder(r):
    s = Session([env()])
    el.try_acquire(run(1), s)
    prev = el.force_release(5)
    assert prev["run_id"] == 1 and el.holder(5) is None
    assert el.force_release(5) is None
    assert el.held_by_run(1) == (None, None)


def test_redis_down_fails_open(monkeypatch):
    monkeypatch.setattr(el, "_client", FakeLeaseRedis(fail=True))
    out = el.try_acquire(run(1), Session([env()]))
    assert out["acquired"] is True and "error" in out
    assert el.release_for_run(1) is None      # never raises on a completion path


# --- policy engine parity: run.dispatch / admit -----------------------------

@pytest.mark.parametrize("held_by_other", [False, True])
def test_admit_rule_matches_try_acquire(monkeypatch, r, held_by_other):
    from app import worker
    monkeypatch.setattr(worker, "_workspace_at_capacity", lambda run, session: False)
    s = Session([env()])
    if held_by_other:
        el.try_acquire(run(9), s)
    legacy_allows = (not held_by_other)
    d = evaluate_sync("run.dispatch", PolicyContext(stage="admit", session=s, facts={"run": run(1)}))
    assert d.allowed is legacy_allows
    if held_by_other:
        assert d.effect == policy.DEFER and d.rule == "environment.exclusive_lock"
        assert d.data["retry_seconds"] == el.RETRY_SECONDS and d.data["holder_run_id"] == 9


def test_concurrency_cap_is_asked_first_and_takes_no_lease(monkeypatch, r):
    from app import worker
    monkeypatch.setattr(worker, "_workspace_at_capacity", lambda run, session: True)
    d = evaluate_sync("run.dispatch", PolicyContext(stage="admit", session=Session([env()]),
                                                     facts={"run": run(1)}))
    assert d.rule == "workspace.max_concurrent_runs"
    assert el.holder(5) is None
