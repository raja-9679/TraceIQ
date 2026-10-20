"""Test ownership rules (WP16b, V5) that need no database.

Run with:
    cd backend && ./run-tests.sh tests/test_test_ownership.py -q
"""
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from app.services import ownership_review as svc
from app.services.suite_hooks import SuiteHookError, validate_suite_settings
from app.services.test_service import _execution_flags


def suite(id, parent=None, interval=None, inherit=True):
    settings = {} if interval is None else {"review_interval_days": interval}
    return NS(id=id, parent_id=parent, settings=settings, inherit_settings=inherit)


def test_interval_is_inherited_nearest_wins_and_default_applies():
    got = svc.effective_intervals([suite(1, interval=30), suite(2, 1), suite(3, 2, interval=7),
                                   suite(4), suite(5, 1, inherit=False)])
    assert got == {1: 30, 2: 30, 3: 7, 4: svc.DEFAULT_REVIEW_INTERVAL_DAYS,
                   5: svc.DEFAULT_REVIEW_INTERVAL_DAYS}


def test_zero_interval_switches_review_off_for_the_subtree():
    got = svc.effective_intervals([suite(1, interval=0), suite(2, 1)])
    assert got == {1: 0, 2: 0}
    assert svc.review_status(None, 0)["status"] == "not_required"


def test_garbage_interval_on_a_stored_suite_falls_back_to_inheritance():
    got = svc.effective_intervals([suite(1, interval=14), suite(2, 1, interval="soon")])
    assert got[2] == 14


def test_cycle_in_suite_parents_does_not_hang():
    got = svc.effective_intervals([suite(1, 2), suite(2, 1)])
    assert got == {1: svc.DEFAULT_REVIEW_INTERVAL_DAYS, 2: svc.DEFAULT_REVIEW_INTERVAL_DAYS}


def test_review_status():
    now = datetime(2026, 10, 20)
    assert svc.review_status(None, 30, now)["status"] == "never"
    assert svc.review_status(now - timedelta(days=10), 30, now) == {"status": "ok", "due_at": now + timedelta(days=20)}
    assert svc.review_status(now - timedelta(days=30), 30, now)["status"] == "overdue"


@pytest.mark.parametrize("value,expected", [(None, None), ("", None), (0, 0), (30, 30), ("45", 45), (30.0, 30)])
def test_normalize_interval_accepts(value, expected):
    assert svc.normalize_interval(value) == expected


@pytest.mark.parametrize("value", [True, -1, 3651, 1.5, "abc", [1]])
def test_normalize_interval_refuses(value):
    with pytest.raises(svc.OwnershipError):
        svc.normalize_interval(value)


def test_suite_settings_validation_normalises_and_refuses():
    assert validate_suite_settings({"review_interval_days": "30"})["review_interval_days"] == 30
    assert "review_interval_days" not in validate_suite_settings({"review_interval_days": None})
    with pytest.raises(SuiteHookError):
        validate_suite_settings({"review_interval_days": -5})


def test_interval_is_an_inherited_execution_flag():
    assert _execution_flags({"review_interval_days": 30}, {}) == {"review_interval_days": 30}
    assert _execution_flags({"review_interval_days": 30}, {"review_interval_days": 0}) == {"review_interval_days": 0}


def test_owner_state():
    active = NS(id=1, is_active=True, full_name="A", email="a@x")
    assert svc.owner_state(active, True) == "ok"
    assert svc.owner_state(active, False) == "no_access"
    assert svc.owner_state(NS(id=2, is_active=False), True) == "no_access"
    assert svc.person(active, False)["state"] == "no_access"


@pytest.mark.parametrize("status,kind,expected", [
    ("FAILED", None, True), ("ERROR", "test", True), ("ERROR", "setup", True),
    ("ERROR", "infrastructure", False), ("FAILED", "timeout", False), ("PASSED", None, False),
    ("CANCELLED", "cancelled", False), ("ERROR", "not_executed", False),
])
def test_is_test_failure(status, kind, expected):
    assert svc.is_test_failure(status, kind) is expected


def test_case_sync_hash_ignores_ownership():
    """Ownership is metadata about who looks after a test, not the test:
    a re-assignment must not make every pulled file look edited (and turn
    into a sync conflict)."""
    from app.services import case_sync
    base = dict(id=1, name="n", steps=[], tags=[], priority=None, code_paths=[], test_suite_id=1,
                description=None)
    a = NS(**base, owner_user_id=None, backup_owner_user_id=None, last_reviewed_at=None)
    b = NS(**base, owner_user_id=5, backup_owner_user_id=6, last_reviewed_at=datetime.utcnow())
    assert case_sync.case_doc(a)["hash"] == case_sync.case_doc(b)["hash"]


def test_project_notification_setting_is_opt_in_and_validated():
    from app.services import project_notifications as pn
    assert pn.DEFAULTS["notify_case_owners"] is False
    assert pn.validate_patch({"notify_case_owners": True}) == {"notify_case_owners": True}
    with pytest.raises(ValueError):
        pn.validate_patch({"notify_case_owners": "yes"})
