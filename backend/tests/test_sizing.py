"""Sizing calculator math (WP13c, decision C6) — worked examples by hand."""
import pytest
from pydantic import ValidationError

from app.services import data_policy
from app.services.sizing import (
    ARTIFACT_SIZE_KB, LEVEL_KINDS, SizingInput, artifact_kb_per_execution, estimate,
)


def _f(out, key):
    return next(r for r in out["formulas"] if r["key"] == key)


def test_level_kinds_match_the_capture_policy():
    for level, kinds in LEVEL_KINDS.items():
        allowed = set(data_policy._LEVEL_ALLOWS[level]) - {"har"}   # HAR is opt-in, not counted
        assert set(kinds) == allowed, level


def test_default_worked_example():
    # 2000 executions/day, 60 s each, 25% in the peak hour, 1 browser:
    #   peak = 500 jobs/h; busy = 500*60/3600 = 8.33; /0.7 -> ceil(11.9) = 12
    #   burst 25 jobs, waves = floor(300/60)+1 = 6 -> ceil(25/6) = 5
    #   workers = max(12, 5) = 12; 12 CPU, 18 GB
    out = estimate(SizingInput())
    r = out["results"]
    assert r["peak_jobs_per_hour"] == 500
    assert _f(out, "busy_workers")["value"] == 8.33
    assert _f(out, "workers_throughput")["value"] == 12
    assert _f(out, "start_waves")["value"] == 6
    assert _f(out, "workers_wait")["value"] == 5
    assert r["workers"] == 12 and r["worker_cpu"] == 12 and r["worker_ram_gb"] == 18
    assert out["compose"]["EXECUTION_WORKERS"] == 12
    assert "EXECUTION_WORKERS=12" in out["compose"]["env"]
    # standard capture: screenshot 250*(1+0.05) + console 10 + network 60 + diff 300*0.1
    assert r["artifact_kb_per_job"] == 262.5 + 10 + 60 + 30
    # 2000 * 90 * 362.5 KB / 1024^2
    assert r["object_store_gb"] == round(2000 * 90 * 362.5 / 1048576, 2)
    # postgres: 2000*90*12 KB*1.3/1024^2 + 1
    assert r["postgres"]["size_gb"] == round(2000 * 90 * 12 * 1.3 / 1048576 + 1, 2)
    assert out["helm"]["lag_count"] == 5          # floor(300 / 60)
    assert out["helm"]["keda_max_replicas"] == 12
    assert "lagCount: 5" in out["helm"]["values_yaml"]


def test_burst_dominates_when_the_wait_target_is_tight():
    # 100 executions/day barely load anyone, but a 40-case suite must start
    # within 30 s of 60 s jobs: waves = 1 -> 40 workers.
    out = estimate(SizingInput(executions_per_day=100, largest_suite_cases=40,
                               target_max_queue_wait_s=30))
    assert _f(out, "start_waves")["value"] == 1
    assert out["results"]["workers"] == 40
    assert out["helm"]["lag_count"] == 1


def test_matrix_factor_multiplies_jobs_and_bursts():
    one = estimate(SizingInput(matrix_factor=1))
    three = estimate(SizingInput(matrix_factor=3))
    assert three["results"]["peak_jobs_per_hour"] == 3 * one["results"]["peak_jobs_per_hour"]
    assert _f(three, "burst_jobs")["value"] == 75
    # 1500 jobs/h * 60/3600 = 25 busy; /0.7 -> 36
    assert three["results"]["workers"] == 36


def test_ha_adds_a_spare_and_doubles_the_control_plane():
    out = estimate(SizingInput(executions_per_day=10, largest_suite_cases=1, ha=True))
    r = out["results"]
    assert r["workers"] == 2           # max(1, ...) + 1, at least 2
    assert r["api_replicas"] == 2 and r["celery_replicas"] == 2
    assert any(a["key"] == "ha" for a in out["assumptions"])


def test_capture_levels_order_storage():
    sizes = [estimate(SizingInput(capture_level=lvl))["results"]["object_store_gb"]
             for lvl in ("none", "minimal", "standard", "full")]
    assert sizes[0] == 0
    assert sizes == sorted(sizes) and len(set(sizes)) == 4


def test_full_capture_counts_trace_always_and_video_on_failure_only():
    kb = artifact_kb_per_execution("full", 120, 0.1, dict(ARTIFACT_SIZE_KB))
    assert kb["trace"] == 1500 + 1000 * 2
    assert kb["video"] == 1200 * 2 * 0.1


def test_measured_sizes_override_the_declared_ones():
    out = estimate(SizingInput(capture_level="minimal", failure_rate=0,
                               artifact_sizes_kb={"screenshot": 100}))
    assert out["results"]["artifact_kb_per_job"] == 100


def test_mobile_emulators_are_sized_separately():
    out = estimate(SizingInput(mobile_emulator=True, mobile_workers=2))
    m = out["results"]["mobile"]
    assert m["ram_gb"] == 2 * (4 + 1) and m["cpu"] == 2 * 2.5
    assert estimate(SizingInput())["results"]["mobile"] is None


def test_every_result_has_its_formula_shown():
    out = estimate(SizingInput())
    keys = {r["key"] for r in out["formulas"]}
    for needed in ("workers", "worker_cpu", "worker_ram_gb", "object_store_gb", "postgres_gb",
                   "redis_mb", "keda_lag_count", "keda_min_replicas", "api_replicas", "celery_replicas"):
        assert needed in keys
    assert all(r["formula"] and r["substituted"] for r in out["formulas"])


def test_small_install_keeps_the_documented_floor():
    out = estimate(SizingInput(executions_per_day=10, largest_suite_cases=1))
    assert out["results"]["recommended_host"]["cpu"] >= 4
    assert out["results"]["recommended_host"]["ram_gb"] >= 8


@pytest.mark.parametrize("bad", [{"capture_level": "everything"}, {"peak_hour_share": 0},
                                 {"artifact_sizes_kb": {"gif": 1}}, {"avg_duration_s": 0}])
def test_bad_inputs_are_rejected(bad):
    with pytest.raises(ValidationError):
        SizingInput(**bad)
