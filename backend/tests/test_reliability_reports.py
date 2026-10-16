"""WP12b pure logic: DORA maths + bands, SLO budget/burn, vendor extraction and
aggregation (query stripping), flake pattern bucketing.

    ./run-tests.sh tests/test_reliability_reports.py -q
"""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import dora, flake_patterns, slo, vendor_health

NOW = datetime(2026, 10, 16, 12, 0, 0)
START = NOW - timedelta(days=30)


# --- DORA -------------------------------------------------------------------

@pytest.mark.parametrize("per_day,band", [(3, "elite"), (1, "elite"), (0.5, "high"), (1 / 7, "high"),
                                          (0.1, "medium"), (1 / 30, "medium"), (0.01, "low")])
def test_frequency_bands(per_day, band):
    assert dora.frequency_band(per_day) == band


@pytest.mark.parametrize("hours,band", [(2, "elite"), (24, "high"), (168, "high"), (169, "medium"),
                                        (720, "medium"), (721, "low")])
def test_lead_time_bands(hours, band):
    assert dora.lead_time_band(hours) == band


@pytest.mark.parametrize("pct,band", [(0, "elite"), (5, "elite"), (7, "high"), (15, "medium"), (16, "low")])
def test_change_failure_bands(pct, band):
    assert dora.change_failure_band(pct) == band


@pytest.mark.parametrize("hours,band", [(0.5, "elite"), (1, "high"), (23, "high"), (24, "medium"),
                                        (168, "medium"), (200, "low")])
def test_restore_bands(hours, band):
    assert dora.restore_band(hours) == band


def _dep(i, days_ago, **kw):
    return dora.Dep(id=i, deployed_at=NOW - timedelta(days=days_ago), **kw)


def test_no_deployments_says_so():
    out = dora.compute(deployments=[], incidents=[], failed_runs=[], first_run_at={},
                       window_start=START, window_end=NOW)
    assert out["deployment_frequency"]["deployments"] == 0
    assert out["deployment_frequency"]["band"] is None
    assert "No deployments recorded" in out["deployment_frequency"]["missing"]
    assert out["change_failure_rate"]["percent"] is None
    assert out["lead_time"]["median_hours"] is None
    assert out["time_to_restore"]["missing"] == "No incidents started in this window."


def test_full_dora_computation():
    deps = [
        _dep(1, 20, environment_id=1, git_commit="abc1234def", commit_at=NOW - timedelta(days=20, hours=5)),
        _dep(2, 10, environment_id=1, git_commit="fff0000"),
        _dep(3, 5, environment_id=2, version="v3"),
        _dep(4, 2, environment_id=1, status="failed"),
        _dep(99, 40),  # outside the window
    ]
    incidents = [
        # 3 h after deployment 3, names its release → deployment 3
        dora.Inc(id=10, started_at=NOW - timedelta(days=5) + timedelta(hours=3),
                 resolved_at=NOW - timedelta(days=5) + timedelta(hours=5), release="v3"),
        # 30 h after deployment 1 → nobody
        dora.Inc(id=11, started_at=NOW - timedelta(days=20) + timedelta(hours=30),
                 resolved_at=NOW - timedelta(days=20) + timedelta(hours=31)),
        dora.Inc(id=12, started_at=NOW - timedelta(days=1)),  # unresolved
    ]
    failed_runs = [
        # env 1, 2 h after deployment 2 → deployment 2
        dora.FailedRun(id=1, created_at=NOW - timedelta(days=10) + timedelta(hours=2), environment_id=1),
        # env 2 failed run 2 h after deployment 2 — different env, ignored
        dora.FailedRun(id=2, created_at=NOW - timedelta(days=10) + timedelta(hours=2), environment_id=2),
        # before any deployment on env 1
        dora.FailedRun(id=3, created_at=NOW - timedelta(days=25), environment_id=1),
    ]
    first_run = {"fff0000": NOW - timedelta(days=12)}
    out = dora.compute(deployments=deps, incidents=incidents, failed_runs=failed_runs,
                       first_run_at=first_run, window_start=START, window_end=NOW)

    f = out["deployment_frequency"]
    assert f["deployments"] == 4 and f["per_week"] == round(4 / 30 * 7, 2) and f["band"] == "medium"

    by_id = {d["id"]: d for d in out["deployments"]}
    assert by_id[1]["failure_causes"] == []
    assert by_id[2]["failure_causes"] == ["failed_run"]
    assert by_id[3]["failure_causes"] == ["incident"]
    assert by_id[4]["failure_causes"] == ["deployment_failed"]
    cfr = out["change_failure_rate"]
    assert cfr["percent"] == 75.0 and cfr["band"] == "low"
    assert cfr["linked_incidents"] == 1 and cfr["linked_failed_runs"] == 1

    lead = out["lead_time"]
    assert lead["samples"] == 2
    assert lead["sources"] == {"commit_at": 1, "first_run": 1}
    assert lead["median_hours"] == pytest.approx((5 + 48) / 2)
    assert by_id[2]["lead_time_source"] == "first_run"

    r = out["time_to_restore"]
    assert r["resolved_incidents"] == 2 and r["unresolved_incidents"] == 1
    assert r["median_hours"] == pytest.approx(1.5) and r["band"] == "high"


def test_incident_goes_to_latest_preceding_deployment_unless_named():
    deps = [_dep(1, 3, git_commit="aaaaaaa1111"), _dep(2, 3 - 2 / 24)]  # 2 h apart
    inc = dora.Inc(id=1, started_at=NOW - timedelta(days=3) + timedelta(hours=4))
    out = dora.compute(deployments=deps, incidents=[inc], failed_runs=[], first_run_at={},
                       window_start=START, window_end=NOW)
    causes = {d["id"]: d["failure_causes"] for d in out["deployments"]}
    assert causes == {1: [], 2: ["incident"]}

    named = dora.Inc(id=2, started_at=inc.started_at, git_commit="aaaaaaa")  # short sha of deployment 1
    out = dora.compute(deployments=[_dep(1, 3, git_commit="aaaaaaa1111"), _dep(2, 3 - 2 / 24)],
                       incidents=[named], failed_runs=[], first_run_at={},
                       window_start=START, window_end=NOW)
    causes = {d["id"]: d["failure_causes"] for d in out["deployments"]}
    assert causes == {1: ["incident"], 2: []}


def test_lead_time_ignores_start_after_deploy():
    d = _dep(1, 1, git_commit="c1")
    out = dora.compute(deployments=[d], incidents=[], failed_runs=[], first_run_at={"c1": NOW},
                       window_start=START, window_end=NOW)
    assert out["lead_time"]["samples"] == 0
    assert "start time" in out["lead_time"]["missing"]


# --- SLO --------------------------------------------------------------------

def test_budget_maths():
    b = slo.budget(total=1000, bad=5, target=99.0)
    assert b["allowed_failures"] == 10.0
    assert b["remaining_pct"] == 50.0
    assert b["burn_rate"] == 0.5
    over = slo.budget(total=1000, bad=20, target=99.0)
    assert over["remaining_pct"] == -100.0 and over["burn_rate"] == 2.0
    assert slo.budget(0, 0, 99.0)["burn_rate"] is None
    assert slo.budget(10, 0, 100.0) == {"allowed_failures": 0.0, "remaining_pct": 100.0, "burn_rate": 0.0}
    assert slo.budget(10, 1, 100.0)["remaining_pct"] is None


def test_percentile_nearest_rank():
    assert slo.percentile([], 95) is None
    assert slo.percentile(list(range(1, 101)), 95) == 95
    assert slo.percentile([10, 20], 95) == 20


def _checks(n, bad_every=None, region=None, duration=None, start=NOW - timedelta(days=6)):
    out = []
    for i in range(n):
        up = not (bad_every and i % bad_every == 0)
        out.append(slo.Check(checked_at=start + timedelta(minutes=30 * i), is_up=up,
                             region=region, duration_ms=duration))
    return out


def test_report_default_target_no_latency_and_regions():
    checks = _checks(200, bad_every=50, region="eu") + _checks(200, region="us")
    out = slo.report(checks, target=None, latency_target=None, window_days=7, now=NOW)
    assert out["target_is_default"] and out["target_pct"] == slo.DEFAULT_TARGET
    assert out["checks"] == 400 and out["bad"] == 4
    assert out["compliance_pct"] == 99.0
    assert out["status"] == "breached"
    assert out["latency"]["p95_ms"] is None
    assert out["latency"]["missing"].startswith("No latency data")
    regions = {r["region"]: r for r in out["regions"]}
    assert regions["us"]["compliance_pct"] == 100.0 and regions["eu"]["bad"] == 4
    assert len(out["daily"]) == 7 and sum(d["checks"] for d in out["daily"]) == 400


def test_report_latency_and_window_filter():
    old = slo.Check(checked_at=NOW - timedelta(days=40), is_up=False)
    checks = _checks(100, duration=800.0) + [old]
    out = slo.report(checks, target=99.0, latency_target=500, window_days=30, now=NOW)
    assert out["checks"] == 100 and out["status"] == "meeting"
    assert out["regions"] == []          # single (unlabelled) region
    assert out["latency"]["p95_ms"] == 800.0 and out["latency"]["met"] is False
    empty = slo.report([], target=99.0, latency_target=None, window_days=7, now=NOW)
    assert empty["status"] == "no_data" and "No monitor checks" in empty["missing"]


def test_fast_burn_marks_at_risk():
    # 1000 good checks over the window, 3 failures in the last 24 h: compliance
    # above 99 %, but the 24 h burn is far over 1.
    checks = _checks(1000, start=NOW - timedelta(days=29))
    checks = [c for c in checks if c.checked_at <= NOW]
    checks += [slo.Check(checked_at=NOW - timedelta(hours=h), is_up=h > 3) for h in range(1, 12)]
    out = slo.report(checks, target=99.0, latency_target=None, window_days=30, now=NOW)
    assert out["compliance_pct"] >= 99.0
    assert out["burn_rate_24h"] > 1 and out["status"] == "at_risk"


# --- vendor health ---------------------------------------------------------

def test_strip_url_drops_query_userinfo_port_fragment():
    assert vendor_health.strip_url("https://u:p@cdn.stripe.com:8443/v3/x.js?key=SECRET#frag") == \
        "https://cdn.stripe.com/v3/x.js"
    assert vendor_health.host_of("https://API.Segment.io./v1?t=1") == "api.segment.io"
    assert vendor_health.host_of("data:text/plain,hi") is None
    assert vendor_health.host_of(None) is None


def test_vendors_in_run_classifies_and_never_keeps_paths():
    events = [
        {"url": "https://shop.example.co.uk/", "resourceType": "document", "status": 200, "duration": 120, "testCaseId": 1},
        {"url": "https://static.example.co.uk/app.js", "status": 200, "duration": 30},
        {"url": "https://www.google-analytics.com/collect?tid=UA-1&cid=secret", "status": 204, "duration": 40},
        {"url": "https://region1.google-analytics.com/g/collect?x=1", "status": 503, "duration": 2500},
        {"url": "https://js.stripe.com/v3/?token=tok_123", "status": 200, "duration": 90},
        {"url": "https://api.stripe.com/v1/tokens", "status": None, "duration": "n/a"},
    ]
    vendors = vendor_health.vendors_in_run(events)
    assert set(vendors) == {"google-analytics.com", "stripe.com"}
    ga = vendors["google-analytics.com"]
    assert ga.requests == 2 and ga.errors == 1
    assert sum(ga.hist) == 2
    stripe = vendors["stripe.com"]
    assert stripe.errors == 1 and sum(stripe.hist) == 1   # unparseable duration skipped

    day = {}
    vendor_health.add_run(day, vendors, passed_results=1, failed_results=2)
    row = vendor_health.row_values(day["google-analytics.com"])
    assert row["hosts"] == ["www.google-analytics.com", "region1.google-analytics.com"] or \
        set(row["hosts"]) == {"www.google-analytics.com", "region1.google-analytics.com"}
    blob = repr(row)
    assert "?" not in blob and "secret" not in blob and "collect" not in blob
    assert row["failed_results_vendor_errors"] == 2 and row["passed_results_vendor_errors"] == 1


def test_first_party_falls_back_to_target_and_busiest_site():
    events = [{"url": "https://app.acme.io/api/a", "status": 200},
              {"url": "https://app.acme.io/api/b", "status": 200},
              {"url": "https://cdn.vendor.net/x", "status": 200}]
    assert set(vendor_health.vendors_in_run(events)) == {"vendor.net"}
    # target_url makes vendor.net first party even when it's the busiest
    assert vendor_health.vendors_in_run(events, ("https://www.vendor.net",)).keys() == {"acme.io"}


def test_histogram_percentiles_merge():
    h = [0] * (len(vendor_health.LATENCY_BUCKETS) + 1)
    for ms in [10, 20, 30, 40, 60, 80, 150, 400, 900, 40000]:
        h[vendor_health.bucket_index(ms)] += 1
    assert vendor_health.hist_percentile(h, 50) == 100.0   # 5th of 10 sits in the <=100 bucket
    assert vendor_health.hist_percentile(h, 40) == 50.0
    assert vendor_health.hist_percentile(h, 95) == 30000.0   # overflow reports the last bound
    assert vendor_health.hist_percentile([], 50) is None
    merged = vendor_health.merge_hist(h, None)
    assert merged == h and vendor_health.merge_hist(None, None) == [0] * len(h)


def test_summarise_merges_days_and_computes_correlation():
    def row(day, vendor, **kw):
        base = dict(requests=0, errors=0, latency_hist=None, latency_p95_ms=None, hosts=[], runs=0,
                    runs_with_errors=0, failed_results=0, failed_results_vendor_errors=0,
                    passed_results=0, passed_results_vendor_errors=0)
        base.update(kw)
        return SimpleNamespace(day=day, vendor=vendor, **base)
    rows = [
        row(date(2026, 10, 1), "stripe.com", requests=10, errors=2, hosts=["js.stripe.com"],
            failed_results=4, failed_results_vendor_errors=3, passed_results=20, passed_results_vendor_errors=2),
        row(date(2026, 10, 2), "stripe.com", requests=10, errors=0, hosts=["api.stripe.com"],
            failed_results=0, passed_results=20),
        row(date(2026, 10, 2), "hotjar.com", requests=5),
    ]
    out = vendor_health.summarise(rows)
    assert [v["vendor"] for v in out] == ["stripe.com", "hotjar.com"]
    s = out[0]
    assert s["name"] == "Stripe" and s["error_rate"] == 10.0
    assert s["hosts"] == ["js.stripe.com", "api.stripe.com"]
    assert [d["date"] for d in s["daily"]] == ["2026-10-01", "2026-10-02"]
    c = s["correlation"]
    assert c["failed_with_vendor_errors_pct"] == 75.0 and c["passed_with_vendor_errors_pct"] == 5.0
    assert c["lift"] == 15.0
    assert out[1]["correlation"]["lift"] is None


def test_run_outcomes():
    assert vendor_health.run_outcomes([("passed", 3), ("FAILED", 1), ("error", 2), ("pending", 9)]) == (3, 3)


# --- flake patterns --------------------------------------------------------

def test_rate_buckets_merge_unknown_and_sort():
    rows = [("chromium", 100, 5, 10), ("firefox", 10, 2, 3), (None, 4, 0, 0), ("", 6, 1, 1)]
    out = flake_patterns.rate_buckets(rows)
    assert [b["key"] for b in out] == ["firefox", "unknown", "chromium"]
    unknown = out[1]
    assert unknown["total"] == 10 and unknown["flaky_pct"] == 10.0
    assert out[0]["flaky_pct"] == 20.0 and out[0]["failed_pct"] == 30.0
    labelled = flake_patterns.rate_buckets([(1, 5, 1, 0)], label=lambda k: f"env {k}")
    assert labelled[0]["label"] == "env 1"


def test_hour_buckets_zero_fill():
    out = flake_patterns.hour_buckets([(3.0, 10, 1, 2), (23, 4, 4, 0)])
    assert len(out) == 24 and [b["key"] for b in out] == list(range(24))
    assert out[3]["flaky_pct"] == 10.0 and out[23]["flaky"] == 4
    assert out[0]["total"] == 0 and out[0]["flaky_pct"] is None and out[0]["label"] == "00:00 UTC"


def test_step_type_buckets():
    out = flake_patterns.step_type_buckets([(True, "click", 3), (False, "click", 2), (False, None, 7),
                                            (True, "fill", 1)])
    assert out[0] == {"step_type": "click", "flaky": 3, "failed": 2, "total": 5}
    assert out[-1]["step_type"] == "unknown" and out[-1]["failed"] == 7


def test_case_and_project_snapshot():
    recs = [SimpleNamespace(test_case_id=1, flake_score=0.2, is_quarantined=False, sample_count=10),
            SimpleNamespace(test_case_id=1, flake_score=0.5, is_quarantined=True, sample_count=4),
            SimpleNamespace(test_case_id=2, flake_score=0.1, is_quarantined=False, sample_count=3)]
    cases = flake_patterns.case_snapshot_rows(recs)
    assert cases[1] == {"flake_score": 0.5, "is_quarantined": True, "sample_count": 10}
    snap = flake_patterns.project_snapshot(cases)
    assert snap == {"flaky_cases": 1, "quarantined_cases": 1, "avg_flake_score": 0.3}
    assert flake_patterns.project_snapshot({})["avg_flake_score"] is None
