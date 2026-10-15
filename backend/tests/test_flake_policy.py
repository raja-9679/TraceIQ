"""Flake owners, quarantine expiry and per-attempt retries.

Pins the rules in app/services/flake_policy.py: a quarantine always expires,
an expired one runs again but is not silently re-hidden, and score-based
release never undoes a person's quarantine.

Run with:
    cd backend && ./run-tests.sh tests/test_flake_policy.py -q
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from app.models import FlakeRecord
from app.services import flake_policy as fp

NOW = datetime(2026, 10, 15, 12, 0, 0)


def _rec(**kw) -> FlakeRecord:
    return FlakeRecord(test_case_id=1, **kw)


# ── quarantine / state ───────────────────────────────────────────────────────

def test_quarantine_sets_expiry_reason_and_source():
    r = _rec()
    fp.quarantine(r, source=fp.SOURCE_MANUAL, days=7, reason="timing", by_user_id=3, now=NOW)
    assert r.is_quarantined
    assert r.quarantine_expires_at == NOW + timedelta(days=7)
    assert (r.quarantine_source, r.quarantine_reason, r.quarantined_by_id) == ("manual", "timing", 3)
    assert fp.quarantine_state(r, NOW) == "active"


@pytest.mark.parametrize("days", [0, fp.MAX_QUARANTINE_DAYS + 1])
def test_quarantine_length_is_bounded(days):
    with pytest.raises(ValueError):
        fp.quarantine(_rec(), source=fp.SOURCE_MANUAL, days=days, now=NOW)


def test_state_after_expiry_is_expired_and_not_active():
    r = _rec()
    fp.quarantine(r, source=fp.SOURCE_AUTO, days=1, now=NOW)
    later = NOW + timedelta(days=1)
    assert fp.quarantine_state(r, later) == "expired"
    assert not fp.is_active(r, later)


def test_release_keeps_owner_and_clears_quarantine():
    r = _rec(owner_user_id=5)
    fp.quarantine(r, source=fp.SOURCE_MANUAL, reason="x", by_user_id=5, now=NOW)
    fp.release(r)
    assert fp.quarantine_state(r) == "none"
    assert r.owner_user_id == 5
    assert r.quarantine_expires_at is None and r.quarantine_reason is None


def test_is_active_handles_missing_record():
    assert fp.is_active(None) is False


def test_active_clause_checks_expiry():
    sql = str(fp.active_quarantine_clause(NOW).compile(dialect=postgresql.dialect()))
    assert "is_quarantined" in sql and "quarantine_expires_at" in sql


# ── aggregator transitions ───────────────────────────────────────────────────

def test_high_score_auto_quarantines_with_default_expiry():
    r = _rec()
    assert fp.apply_score(r, 0.5, 10, now=NOW) == "quarantined"
    assert r.quarantine_source == "auto"
    assert r.quarantine_expires_at == NOW + timedelta(days=fp.DEFAULT_QUARANTINE_DAYS)


def test_too_few_samples_does_not_quarantine():
    assert fp.apply_score(_rec(), 0.9, fp.QUARANTINE_MIN_SAMPLES - 1, now=NOW) is None


def test_expired_quarantine_is_not_silently_renewed():
    r = _rec()
    fp.quarantine(r, source=fp.SOURCE_AUTO, days=1, now=NOW)
    later = NOW + timedelta(days=2)
    assert fp.apply_score(r, 0.9, 20, now=later) is None
    assert fp.quarantine_state(r, later) == "expired"


def test_low_score_releases_auto_and_legacy_quarantine():
    auto = _rec()
    fp.quarantine(auto, source=fp.SOURCE_AUTO, now=NOW)
    legacy = _rec(is_quarantined=True)  # pre-migration row: no source
    assert fp.apply_score(auto, 0.0, 20, now=NOW) == "released"
    assert fp.apply_score(legacy, 0.0, 20, now=NOW) == "released"


def test_low_score_never_releases_manual_quarantine():
    r = _rec()
    fp.quarantine(r, source=fp.SOURCE_MANUAL, reason="known race", by_user_id=2, now=NOW)
    assert fp.apply_score(r, 0.0, 20, now=NOW) is None
    assert r.is_quarantined


# ── attempts ingestion ───────────────────────────────────────────────────────

def test_evidence_keeps_normalised_attempts():
    from app.tasks.result_aggregator import _evidence_payload
    out = _evidence_payload({"attempts": [
        {"attempt": 1, "status": "failed", "error": "x" * 5000, "duration_ms": 1200.7},
        {"attempt": 2, "status": "passed", "duration_ms": 900},
        "junk", {"no": "status"},
    ]})
    assert [a["status"] for a in out["attempts"]] == ["failed", "passed"]
    assert len(out["attempts"][0]["error"]) == 1000
    assert out["attempts"][0]["duration_ms"] == 1200


def test_evidence_without_attempts_is_unchanged():
    from app.tasks.result_aggregator import _evidence_payload
    assert "attempts" not in _evidence_payload({"attempts": "nope"})
    assert _evidence_payload({}) == {}


def test_attempts_are_capped():
    from app.tasks.result_aggregator import MAX_ATTEMPTS_KEPT, _attempts
    raw = [{"attempt": i, "status": "failed"} for i in range(1, 50)]
    assert len(_attempts(raw)) == MAX_ATTEMPTS_KEPT


@pytest.mark.parametrize("bad", [
    {"attempt": "x", "status": "failed"},
    {"attempt": 1, "status": "failed", "duration_ms": float("inf")},
    {"attempt": 1, "status": "failed", "duration_ms": float("nan")},
    {"attempt": True, "status": "failed", "duration_ms": -5},
])
def test_malformed_attempts_never_raise(bad):
    # An exception here would leave the whole job's result un-acked.
    from app.tasks.result_aggregator import _attempts
    out = _attempts([bad])
    assert out[0]["attempt"] == 1 and "duration_ms" not in out[0]
