"""Restore-drill report validation (WP13a): nothing secret is accepted.

    ./run-tests.sh tests/test_restore_drill_payload.py -q
"""
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from app.services.restore_drills import DrillReport, looks_secret


def payload(**kw):
    base = {"status": "pass", "ran_at": "2026-10-15T03:00:00Z", "dump_name": "traceiq-2026-10-15.sql.gz",
            "target": "traceiq_restore_drill on traceiq-postgres-1", "duration_seconds": 42,
            "checks": [{"status": "PASS", "check": "restore", "detail": "12s, 4.1M dump"},
                       {"status": "WARN", "check": "schema revision", "detail": "migrated"}]}
    base.update(kw)
    return base


def test_a_normal_report_is_accepted_and_ran_at_is_naive_utc():
    report = DrillReport.model_validate(payload(ran_at="2026-10-15T08:30:00+05:30"))
    assert report.ran_at == datetime(2026, 10, 15, 3, 0, 0) and report.ran_at.tzinfo is None


@pytest.mark.parametrize("detail", [
    "could not connect: postgresql+asyncpg://traceiq:hunter2@db:5432/traceiq",
    "PGPASSWORD set in env",
    "password=hunter2",
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.x.y",
    "used key tiq_abcdefgh12345678",
    "SECRET_KEY: s3cr3t",
])
def test_anything_that_looks_like_a_credential_is_refused(detail):
    with pytest.raises(ValidationError, match="credential"):
        DrillReport.model_validate(payload(checks=[{"status": "PASS", "check": "restore", "detail": detail}]))


def test_redacted_lines_are_fine():
    assert not looks_secret("could not connect: postgresql://traceiq:***@db/x")
    assert not looks_secret("users=12 workspace=3 auditlog=900")


def test_unknown_fields_are_refused():
    with pytest.raises(ValidationError):
        DrillReport.model_validate(payload(database_url="postgresql://u@h/db"))
    with pytest.raises(ValidationError):
        DrillReport.model_validate(payload(checks=[{"status": "PASS", "check": "x", "pg_pass": "y"}]))


def test_dump_name_must_be_a_basename():
    with pytest.raises(ValidationError):
        DrillReport.model_validate(payload(dump_name="/srv/backups/traceiq.sql.gz"))


def test_status_must_agree_with_the_checks():
    with pytest.raises(ValidationError):
        DrillReport.model_validate(payload(checks=[{"status": "FAIL", "check": "restore"}]))
    with pytest.raises(ValidationError):
        DrillReport.model_validate(payload(status="fail"))
    ok = DrillReport.model_validate(payload(status="fail", checks=[{"status": "FAIL", "check": "restore"}]))
    assert ok.status == "fail"


def test_limits():
    with pytest.raises(ValidationError):
        DrillReport.model_validate(payload(checks=[]))
    with pytest.raises(ValidationError):
        DrillReport.model_validate(payload(checks=[{"status": "PASS", "check": "x"}] * 51))
    with pytest.raises(ValidationError):
        DrillReport.model_validate(payload(checks=[{"status": "MAYBE", "check": "x"}]))
    future = (datetime.utcnow() + timedelta(days=1)).isoformat()
    with pytest.raises(ValidationError):
        DrillReport.model_validate(payload(ran_at=future))
