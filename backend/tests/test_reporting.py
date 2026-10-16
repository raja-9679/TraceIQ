"""WP12a unit tests: digest numbers and rendering, share-link tokens and the
redacted public payloads. Queries and access rules: tests/integration/test_reporting_db.py."""
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models import TestStatus
from app.services import reporting as r
from app.services import share_links as sl


def _stats(runs, passed, failures=0):
    return {"runs": runs, "passed": passed, "failed": runs - passed,
            "pass_rate": r.pass_rate(passed, runs), "failures": failures}


# --- numbers ------------------------------------------------------------------

def test_pass_rate_is_none_without_runs_not_zero():
    assert r.pass_rate(0, 0) is None
    assert r.pass_rate(9, 12) == 75.0
    assert r.pass_rate(1, 3) == 33.3


def test_trend_is_current_minus_previous():
    t = r.trend(_stats(20, 18, failures=4), _stats(10, 8, failures=9))
    assert t == {"pass_rate": 10.0, "runs": 10, "failures": -5}


def test_trend_pass_rate_needs_both_windows():
    assert r.trend(_stats(5, 5), r.empty_stats())["pass_rate"] is None
    assert r.trend(r.empty_stats(), _stats(5, 5))["pass_rate"] is None
    assert r.trend(r.empty_stats(), _stats(5, 5))["runs"] == -5


def test_combine_recomputes_pass_rate_from_counts():
    # Not the mean of 100% and 50%: 11 of 12 runs passed.
    c = r.combine([_stats(2, 2, 0), _stats(10, 9, 3)])
    assert (c["runs"], c["passed"], c["failures"], c["pass_rate"]) == (12, 11, 3, 91.7)


def test_fill_days_covers_every_day_of_the_window():
    start = date(2026, 10, 1)
    out = r.fill_days({date(2026, 10, 2): {"runs": 4, "passed": 3}}, start, 3)
    assert [d["date"] for d in out] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert out[0] == {"date": "2026-10-01", "runs": 0, "passed": 0, "pass_rate": None}
    assert out[1]["pass_rate"] == 75.0


def test_clamp_days():
    assert r.clamp_days(0) == 7 and r.clamp_days(400) == 90 and r.clamp_days(30) == 30


def _snap(**kw):
    s = {"project_name": "Shop <b>", "window_days": 7, "current": _stats(40, 36, failures=6),
         "previous": _stats(40, 32, failures=10), "top_failing": [], "clusters": [],
         "open_clusters": 0, "flaky_count": 0, "readiness": None,
         "incidents": {"escaped": 0, "open": 0, "open_critical": 0}}
    s.update(kw)
    s["trend"] = r.trend(s["current"], s["previous"])
    s["headline"] = r.headline(s)
    return s


def test_headline_reads_like_a_sentence():
    s = _snap(readiness={"verdict": "not_ready"}, open_clusters=2, flaky_count=1,
              incidents={"escaped": 1})
    assert s["headline"] == ("90% of 40 runs passed this week, up 10 points on the period before. "
                             "The latest build is not ready to release. "
                             "Watch: 2 open failure groups, 1 flaky test, 1 escaped defect.")


def test_headline_without_runs_or_previous_window():
    s = _snap(current=r.empty_stats(), window_days=30)
    assert s["headline"].startswith("No test runs finished in the last 30 days.")
    s = _snap(previous=r.empty_stats())
    assert s["headline"].startswith("90% of 40 runs passed this week.")
    s = _snap(previous=_stats(40, 36))
    assert "about the same" in s["headline"]


def test_digest_text_has_trend_and_top_failures():
    s = _snap(top_failing=[{"test_name": "checkout", "failures": 4}],
              clusters=[{"title": "Timeout on #pay", "category": "timeout", "occurrences": 3}],
              open_clusters=1, readiness={"verdict": "ready"})
    text = r.render_digest_text(s, "https://tiq.example.com/reports/summary")
    assert "Pass rate: 90% (+10 pts)" in text
    assert "Runs: 40 (no change)" in text
    assert "Failures: 6 (-4)" in text
    assert "checkout: 4 failures" in text
    assert "Release readiness: ready" in text
    assert text.endswith("Open in TraceIQ: https://tiq.example.com/reports/summary")


def test_digest_html_escapes_names():
    s = _snap(top_failing=[{"test_name": "<script>alert(1)</script>", "failures": 1}])
    out = r.render_digest_html(s, 'https://x.example.com/"onmouseover')
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "Shop &lt;b&gt;" in out
    assert '"onmouseover' not in out


# --- share-link tokens ---------------------------------------------------------

def test_token_is_random_and_only_its_hash_is_kept():
    a, b = sl.new_token(), sl.new_token()
    assert a != b and len(a) >= 40
    h = sl.hash_token(a)
    assert len(h) == 64 and a not in h
    assert sl.hash_token(a) == h


def test_expiry_defaults_to_seven_days_and_caps_at_ninety():
    now = datetime(2026, 10, 16, 12, 0)
    assert sl.expiry(None, now) == now + timedelta(days=7)
    assert sl.expiry(90, now) == now + timedelta(days=90)
    for bad in (0, 91, -1, True, "7"):
        with pytest.raises(ValueError):
            sl.expiry(bad, now)


def test_state():
    now = datetime(2026, 10, 16)
    link = SimpleNamespace(revoked_at=None, expires_at=now + timedelta(hours=1))
    assert sl.state(link, now) == "active"
    link.expires_at = now
    assert sl.state(link, now) == "expired"
    link.revoked_at = now - timedelta(days=1)
    assert sl.state(link, now) == "revoked"


def test_describe_never_carries_the_hash():
    link = SimpleNamespace(id=1, kind="project_summary", project_id=2, workspace_id=3, target_id=None,
                           window_days=30, label=None, created_at=datetime.utcnow(),
                           expires_at=datetime.utcnow() + timedelta(days=1), revoked_at=None,
                           last_viewed_at=None, view_count=0, token_hash="f" * 64)
    out = sl.describe(link, SimpleNamespace(id=9, full_name="Ann", email="ann@corp.example.com"))
    assert "f" * 64 not in json.dumps(out, default=str)
    assert "ann@corp.example.com" not in json.dumps(out, default=str)


# --- redaction of the public payloads -------------------------------------------

SECRETS = ["https://minio.internal/traces/1.zip", "Error: Timeout 30000ms waiting for #pay",
           "at Object.<anonymous> (/app/x.js:1:1)", "#checkout > button.pay",
           "https://shop.internal/api/cart", "Bearer abc.def", "dev@corp.example.com",
           "feature/secret-branch", "deadbeefcafe"]


def _run():
    return SimpleNamespace(
        id=7, suite_name="Checkout", status=TestStatus.FAILED, browser="chromium",
        total_tests=2, passed_tests=1, failed_tests=1, duration_ms=1234.0,
        created_at=datetime(2026, 10, 16, 9), finalized_at=datetime(2026, 10, 16, 9, 5),
        error_message=SECRETS[1], trace_url=SECRETS[0], video_url=SECRETS[0],
        screenshots=[SECRETS[0]], request_headers={"Authorization": SECRETS[5]},
        target_url=SECRETS[4], git_branch=SECRETS[7], git_commit=SECRETS[8],
        execution_log=[{"selector": SECRETS[3]}], user=SimpleNamespace(email=SECRETS[6]))


def _result(name, status):
    return SimpleNamespace(
        test_name=name, status=status, duration_ms=500.0, error_message=SECRETS[1] + SECRETS[2],
        trace_url=SECRETS[0], video_url=SECRETS[0], har_url=SECRETS[0], screenshots=[SECRETS[0]],
        request_url=SECRETS[4], request_headers={"Authorization": SECRETS[5]},
        response_body=SECRETS[1], ai_analysis=SECRETS[1])


def test_public_run_report_is_a_whitelist():
    out = sl.public_run_report(_run(), [_result("cart", TestStatus.PASSED),
                                        _result("pay", TestStatus.FAILED)], "Shop")
    dumped = json.dumps(out)
    for s in SECRETS:
        assert s not in dumped, s
    assert out["run"]["pass_rate"] == 50.0
    assert out["results"] == [{"test_name": "cart", "status": "passed", "duration_ms": 500.0},
                              {"test_name": "pay", "status": "failed", "duration_ms": 500.0}]
    assert set(out["run"]) == {"id", "suite_name", "status", "browser", "total_tests", "passed_tests",
                               "failed_tests", "pass_rate", "duration_ms", "created_at", "finalized_at"}


def test_public_summary_drops_cluster_titles_reasons_and_extras():
    snap = _snap(clusters=[{"title": SECRETS[1], "category": "timeout", "occurrences": 3}],
                 open_clusters=1, top_failing=[{"test_name": "pay", "failures": 2, "extra": SECRETS[2]}],
                 readiness={"verdict": "not_ready", "reasons": [SECRETS[1]], "git_commit": SECRETS[8]},
                 last_run={"id": 3, "status": "failed"}, scope="project", generated_at="2026-10-16",
                 daily=[{"date": "2026-10-16", "runs": 1, "passed": 1, "pass_rate": 100.0, "x": SECRETS[0]}])
    snap["current"]["note"] = SECRETS[6]
    out = sl.public_summary(snap)
    dumped = json.dumps(out)
    for s in SECRETS:
        assert s not in dumped, s
    assert out["open_clusters"] == 1
    assert out["readiness"] == {"verdict": "not_ready"}
    assert out["top_failing"] == [{"test_name": "pay", "failures": 2}]
    assert "clusters" not in out


def test_public_workspace_summary_lists_projects_by_name_only():
    snap = _snap(scope="workspace", workspace_name="Corp", verdicts={"ready": 1},
                 projects=[{"project_id": 5, "project_name": "Shop", "pass_rate": 90.0, "runs": 4,
                            "failures": 1, "trend_pass_rate": 2.0, "verdict": "ready",
                            "flaky_count": 0, "owner_email": SECRETS[6]}])
    out = sl.public_summary(snap)
    assert out["kind"] == "workspace_summary" and out["name"] == "Corp"
    assert out["projects"][0]["name"] == "Shop"
    assert "project_id" not in out["projects"][0]
    assert SECRETS[6] not in json.dumps(out)
