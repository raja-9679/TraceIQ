"""Deployment sizing calculator (WP13c, decision C6).

Pure arithmetic over figures this repo already documents, so the answer can be
checked by hand: every number in the output comes with the formula that made
it and the assumption it rests on (the page shows both). Nothing here reads the
database or the running fleet.

Sources of the fixed figures:

* SELF_HOSTING.md "Requirements": 4 CPU / 8 GB minimum for a small team; each
  browser worker wants ~1 CPU and ~1.5 GB under load; RUN_RETENTION_DAYS
  defaults to 90.
* deploy/helm/traceiq/values.yaml `executionWorker`: requests 500m / 1Gi,
  limits 2 / 3Gi, a 1Gi memory-backed /dev/shm; `keda.lagCount` is the target
  of undelivered jobs per replica.
* execution-engine `core/job-queue.ts`: a worker claims ONE job at a time
  (XREADGROUP COUNT 1), so peak concurrency == workers needed.
* PRODUCTION_DEPLOY.md: an Android emulator needs ~4 GB RAM.
* app/services/data_policy.py `_LEVEL_ALLOWS` + execution-engine
  `artifact-store.ts` (`keepByPrefs`): which artifact kinds a capture level
  allows, and that a video is kept on failure only by default while a trace is
  kept on every run.

Artifact SIZES are not measured anywhere in the code base, so they are declared
defaults (ARTIFACT_SIZE_KB) the caller may override with their own averages.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

CAPTURE_LEVELS = ("none", "minimal", "standard", "full")

#: Mirrors data_policy._LEVEL_ALLOWS (kept literal so this module stays pure;
#: tests/test_sizing.py asserts the two agree).
LEVEL_KINDS: Dict[str, tuple] = {
    "none": (),
    "minimal": ("screenshot",),
    "standard": ("screenshot", "console_log", "network_log", "visual_diff"),
    "full": ("screenshot", "console_log", "network_log", "visual_diff", "video", "trace"),
}

#: Declared per-artifact sizes in KB. Override with measured values.
ARTIFACT_SIZE_KB: Dict[str, float] = {
    "screenshot": 250.0,       # one masked full-page PNG
    "console_log": 10.0,       # per execution
    "network_log": 60.0,       # per execution (request list, no bodies)
    "visual_diff": 300.0,      # per visual-diff produced
    "video_per_min": 1200.0,   # 1280x720 webm
    "trace_base": 1500.0,      # Playwright trace with DOM snapshots, fixed part
    "trace_per_min": 1000.0,   # ... plus this per minute of runtime
}

# Fixed figures from the docs listed above.
WORKER_CPU = 1.0               # SELF_HOSTING.md
WORKER_RAM_GB = 1.5            # SELF_HOSTING.md
WORKER_LIMIT_CPU = 2.0         # values.yaml limits
WORKER_LIMIT_RAM_GB = 3.0
EMULATOR_RAM_GB = 4.0          # PRODUCTION_DEPLOY.md
EMULATOR_CPU = 2.0             # assumption: a KVM emulator pins ~2 cores
MOBILE_WORKER_CPU = 0.5
MOBILE_WORKER_RAM_GB = 1.0
TARGET_UTILISATION = 0.7       # same 70% the chart's CPU HPA targets
SCREENSHOTS_PER_EXECUTION = 1.0   # explicit screenshot steps, on average
VISUAL_DIFFS_PER_EXECUTION = 0.1
CELERY_TASK_SECONDS_PER_JOB = 1.5  # finalize + aggregate + notify, ~3 x 0.5 s
CELERY_CONCURRENCY = 4
CELERY_TARGET_UTILISATION = 0.5
WORKERS_PER_API_REPLICA = 50   # webhook/finalize callbacks one API pod absorbs
PG_KB_PER_EXECUTION = {"none": 4.0, "minimal": 4.0, "standard": 12.0, "full": 12.0}
PG_INDEX_OVERHEAD = 1.3
PG_BASE_GB = 1.0
REDIS_BASE_MB = 256.0
REDIS_KB_PER_QUEUED_JOB = 20.0


class SizingInput(BaseModel):
    executions_per_day: int = Field(2000, ge=0, le=10_000_000,
                                    description="Test case executions per day, before the browser/device matrix")
    avg_duration_s: float = Field(60.0, gt=0, le=3600)
    peak_hour_share: float = Field(0.25, gt=0, le=1,
                                   description="Fraction of the day's executions in the busiest hour")
    matrix_factor: int = Field(1, ge=1, le=50, description="Browsers x devices each test runs on")
    largest_suite_cases: int = Field(25, ge=1, le=100_000,
                                     description="Cases dispatched at once by the biggest suite run")
    target_max_queue_wait_s: float = Field(300.0, ge=0, le=86_400)
    capture_level: str = Field("standard")
    failure_rate: float = Field(0.05, ge=0, le=1)
    retention_days: int = Field(90, ge=1, le=3650)
    mobile_emulator: bool = False
    mobile_workers: int = Field(1, ge=0, le=100)
    ha: bool = False
    artifact_sizes_kb: Optional[Dict[str, float]] = None

    @field_validator("capture_level")
    @classmethod
    def _level(cls, v: str) -> str:
        key = (v or "").strip().lower()
        if key not in CAPTURE_LEVELS:
            raise ValueError(f"capture_level must be one of {', '.join(CAPTURE_LEVELS)}")
        return key

    @field_validator("artifact_sizes_kb")
    @classmethod
    def _sizes(cls, v):
        if v is None:
            return v
        out = {}
        for key, val in v.items():
            if key not in ARTIFACT_SIZE_KB:
                raise ValueError(f"unknown artifact size '{key}'; known: {', '.join(ARTIFACT_SIZE_KB)}")
            if val is None or val < 0 or val > 10_000_000:
                raise ValueError(f"artifact size '{key}' must be between 0 and 10000000 KB")
            out[key] = float(val)
        return out


def _r(x: float, nd: int = 2) -> float:
    return round(x + 0.0, nd)


class _Sheet:
    """Collects formula rows as they are computed, so the page shows exactly
    the arithmetic that produced each number (no second copy to drift)."""

    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []

    def add(self, key: str, label: str, formula: str, substituted: str,
            value: float, unit: str = "") -> float:
        self.rows.append({"key": key, "label": label, "formula": formula,
                          "substituted": substituted, "value": value, "unit": unit})
        return value


def artifact_kb_per_execution(level: str, duration_s: float, failure_rate: float,
                              sizes: Dict[str, float]) -> Dict[str, float]:
    """Average KB written to the object store per execution, by kind."""
    kinds = LEVEL_KINDS[level]
    minutes = duration_s / 60.0
    out: Dict[str, float] = {}
    if "screenshot" in kinds:
        # Explicit screenshot steps, plus the automatic one on failure.
        out["screenshot"] = sizes["screenshot"] * (SCREENSHOTS_PER_EXECUTION + failure_rate)
    if "console_log" in kinds:
        out["console_log"] = sizes["console_log"]
    if "network_log" in kinds:
        out["network_log"] = sizes["network_log"]
    if "visual_diff" in kinds:
        out["visual_diff"] = sizes["visual_diff"] * VISUAL_DIFFS_PER_EXECUTION
    if "trace" in kinds:
        out["trace"] = sizes["trace_base"] + sizes["trace_per_min"] * minutes
    if "video" in kinds:
        # Default preference keeps a video only when the case failed.
        out["video"] = sizes["video_per_min"] * minutes * failure_rate
    return out


def estimate(inp: SizingInput) -> Dict[str, Any]:
    sizes = dict(ARTIFACT_SIZE_KB)
    sizes.update(inp.artifact_sizes_kb or {})
    s = _Sheet()
    D = inp.avg_duration_s

    jobs_day = s.add("jobs_per_day", "Browser jobs per day",
                     "executions_per_day x matrix_factor",
                     f"{inp.executions_per_day} x {inp.matrix_factor}",
                     inp.executions_per_day * inp.matrix_factor, "jobs/day")
    peak = s.add("peak_jobs_per_hour", "Jobs in the busiest hour",
                 "jobs_per_day x peak_hour_share",
                 f"{jobs_day} x {inp.peak_hour_share}",
                 _r(jobs_day * inp.peak_hour_share), "jobs/h")
    busy = s.add("busy_workers", "Average busy workers in the peak hour (Little's law)",
                 "peak_jobs_per_hour x avg_duration_s / 3600",
                 f"{peak} x {D} / 3600", _r(peak * D / 3600.0), "workers")
    c_tp = s.add("workers_throughput", "Workers for throughput at 70% utilisation",
                 "ceil(busy_workers / 0.7)", f"ceil({busy} / {TARGET_UTILISATION})",
                 math.ceil(busy / TARGET_UTILISATION - 1e-9) if busy > 0 else 0, "workers")
    waves = s.add("start_waves", "Jobs one worker can START within the target wait",
                  "floor(target_max_queue_wait_s / avg_duration_s) + 1",
                  f"floor({inp.target_max_queue_wait_s} / {D}) + 1",
                  math.floor(inp.target_max_queue_wait_s / D) + 1, "jobs")
    burst = s.add("burst_jobs", "Largest burst dispatched at once",
                  "largest_suite_cases x matrix_factor",
                  f"{inp.largest_suite_cases} x {inp.matrix_factor}",
                  inp.largest_suite_cases * inp.matrix_factor, "jobs")
    c_wait = s.add("workers_wait", "Workers so the burst's last job starts within the target wait",
                   "ceil(burst_jobs / start_waves)", f"ceil({burst} / {waves})",
                   math.ceil(burst / waves) if inp.executions_per_day > 0 else 0, "workers")
    base_workers = max(c_tp, c_wait, 1)
    if inp.ha:
        workers = s.add("workers", "Browser workers (peak concurrency, HA: N+1, min 2)",
                        "max(workers_throughput, workers_wait, 1) + 1, at least 2",
                        f"max({c_tp}, {c_wait}, 1) + 1", max(base_workers + 1, 2), "workers")
    else:
        workers = s.add("workers", "Browser workers (peak concurrency)",
                        "max(workers_throughput, workers_wait, 1)",
                        f"max({c_tp}, {c_wait}, 1)", base_workers, "workers")
    w_cpu = s.add("worker_cpu", "Worker CPU under load", "workers x 1 CPU",
                  f"{workers} x {WORKER_CPU}", workers * WORKER_CPU, "CPU")
    w_ram = s.add("worker_ram_gb", "Worker RAM under load", "workers x 1.5 GB",
                  f"{workers} x {WORKER_RAM_GB}", _r(workers * WORKER_RAM_GB), "GB")

    # --- control plane ---------------------------------------------------
    api_min = 2 if inp.ha else 1
    api = s.add("api_replicas", "Backend API replicas",
                f"max({api_min}, ceil(workers / {WORKERS_PER_API_REPLICA}))",
                f"max({api_min}, ceil({workers} / {WORKERS_PER_API_REPLICA}))",
                max(api_min, math.ceil(workers / WORKERS_PER_API_REPLICA)), "replicas")
    celery_cap = CELERY_CONCURRENCY * 3600 * CELERY_TARGET_UTILISATION / CELERY_TASK_SECONDS_PER_JOB
    celery = s.add("celery_replicas", "Celery main-queue workers",
                   f"max({api_min}, ceil(peak_jobs_per_hour / {int(celery_cap)}))",
                   f"max({api_min}, ceil({peak} / {int(celery_cap)}))",
                   max(api_min, math.ceil(peak / celery_cap)), "replicas")

    # --- storage ----------------------------------------------------------
    kinds_kb = artifact_kb_per_execution(inp.capture_level, D, inp.failure_rate, sizes)
    per_exec_kb = _r(sum(kinds_kb.values()))
    s.add("artifact_kb_per_job", f"Artifacts per job at capture level '{inp.capture_level}'",
          " + ".join(sorted(kinds_kb)) or "0 (nothing is captured)",
          " + ".join(f"{_r(v)}" for _, v in sorted(kinds_kb.items())) or "0",
          per_exec_kb, "KB")
    obj_gb = s.add("object_store_gb", "Object store over the retention window",
                   "jobs_per_day x retention_days x artifact_kb_per_job / 1024^2",
                   f"{jobs_day} x {inp.retention_days} x {per_exec_kb} / 1048576",
                   _r(jobs_day * inp.retention_days * per_exec_kb / 1048576.0), "GB")
    pg_kb = PG_KB_PER_EXECUTION[inp.capture_level]
    pg_gb = s.add("postgres_gb", "Postgres size (runs + results over the retention window)",
                  f"jobs_per_day x retention_days x {pg_kb} KB x {PG_INDEX_OVERHEAD} / 1024^2 + {PG_BASE_GB} GB",
                  f"{jobs_day} x {inp.retention_days} x {pg_kb} x {PG_INDEX_OVERHEAD} / 1048576 + {PG_BASE_GB}",
                  _r(jobs_day * inp.retention_days * pg_kb * PG_INDEX_OVERHEAD / 1048576.0 + PG_BASE_GB), "GB")
    redis_mb = s.add("redis_mb", "Redis memory",
                     f"{int(REDIS_BASE_MB)} MB + burst_jobs x {int(REDIS_KB_PER_QUEUED_JOB)} KB",
                     f"{int(REDIS_BASE_MB)} + {burst} x {int(REDIS_KB_PER_QUEUED_JOB)} / 1024",
                     _r(REDIS_BASE_MB + burst * REDIS_KB_PER_QUEUED_JOB / 1024.0), "MB")
    if pg_gb <= 5:
        pg_cpu, pg_ram = 1, 2
    elif pg_gb <= 50:
        pg_cpu, pg_ram = 2, 8
    else:
        pg_cpu, pg_ram = 4, 16

    # --- mobile -------------------------------------------------------------
    mobile = None
    if inp.mobile_emulator and inp.mobile_workers > 0:
        m = inp.mobile_workers
        mobile = {
            "mobile_workers": m,
            "emulators": m,
            "cpu": s.add("mobile_cpu", "Mobile CPU (emulator + mobile worker each)",
                         f"mobile_workers x ({EMULATOR_CPU} + {MOBILE_WORKER_CPU})",
                         f"{m} x ({EMULATOR_CPU} + {MOBILE_WORKER_CPU})",
                         _r(m * (EMULATOR_CPU + MOBILE_WORKER_CPU)), "CPU"),
            "ram_gb": s.add("mobile_ram_gb", "Mobile RAM (emulator + mobile worker each)",
                            f"mobile_workers x ({EMULATOR_RAM_GB} + {MOBILE_WORKER_RAM_GB}) GB",
                            f"{m} x ({EMULATOR_RAM_GB} + {MOBILE_WORKER_RAM_GB})",
                            _r(m * (EMULATOR_RAM_GB + MOBILE_WORKER_RAM_GB)), "GB"),
            "note": "Emulators need a host with /dev/kvm; iOS needs a device cloud.",
        }

    components = [
        {"name": "execution-worker", "replicas": workers, "cpu": WORKER_CPU, "ram_gb": WORKER_RAM_GB},
        {"name": "backend", "replicas": api, "cpu": 1.0, "ram_gb": 1.0},
        {"name": "celery_worker", "replicas": celery, "cpu": 1.0, "ram_gb": 1.0},
        {"name": "celery_aggregator", "replicas": api_min, "cpu": 0.5, "ram_gb": 0.5},
        {"name": "celery_beat", "replicas": 1, "cpu": 0.25, "ram_gb": 0.25},
        {"name": "postgres", "replicas": 2 if inp.ha else 1, "cpu": pg_cpu, "ram_gb": pg_ram},
        {"name": "redis", "replicas": 3 if inp.ha else 1, "cpu": 0.5,
         "ram_gb": _r(max(0.5, redis_mb / 1024.0 * 2))},
        {"name": "object-store", "replicas": 4 if inp.ha else 1, "cpu": 0.5, "ram_gb": 1.0},
        {"name": "frontend", "replicas": api_min, "cpu": 0.25, "ram_gb": 0.25},
    ]
    if mobile:
        components.append({"name": "android-emulator + mobile-worker", "replicas": mobile["mobile_workers"],
                           "cpu": EMULATOR_CPU + MOBILE_WORKER_CPU,
                           "ram_gb": EMULATOR_RAM_GB + MOBILE_WORKER_RAM_GB})
    total_cpu = _r(sum(c["replicas"] * c["cpu"] for c in components))
    total_ram = _r(sum(c["replicas"] * c["ram_gb"] for c in components))
    s.add("total_cpu", "Total CPU (all components)", "sum(replicas x cpu)",
          " + ".join(f"{c['replicas']}x{c['cpu']}" for c in components), total_cpu, "CPU")
    s.add("total_ram_gb", "Total RAM (all components)", "sum(replicas x ram)",
          " + ".join(f"{c['replicas']}x{c['ram_gb']}" for c in components), total_ram, "GB")

    # --- deployment suggestions ------------------------------------------------
    offpeak = jobs_day * (1 - inp.peak_hour_share) / 23.0 if jobs_day else 0.0
    keda_min = max(api_min if inp.ha else 1, math.ceil(offpeak * D / 3600.0 / TARGET_UTILISATION - 1e-9))
    keda_min = min(keda_min, workers)
    s.add("keda_min_replicas", "KEDA minReplicas (off-peak average load)",
          "ceil(jobs_per_day x (1 - peak_hour_share) / 23 x avg_duration_s / 3600 / 0.7), at least 1",
          f"ceil({jobs_day} x {_r(1 - inp.peak_hour_share)} / 23 x {D} / 3600 / 0.7)", keda_min, "replicas")
    lag = s.add("keda_lag_count", "KEDA lagCount (undelivered jobs per replica)",
                "max(1, floor(target_max_queue_wait_s / avg_duration_s))",
                f"max(1, floor({inp.target_max_queue_wait_s} / {D}))",
                max(1, math.floor(inp.target_max_queue_wait_s / D)), "jobs")
    single_host = total_cpu <= 16 and total_ram <= 64
    helm_yaml = "\n".join([
        "executionWorker:",
        f"  replicas: {workers}",
        "  shmSize: 1Gi",
        "  resources:",
        "    requests: {cpu: \"1\", memory: 1536Mi}",
        f"    limits: {{cpu: \"{int(WORKER_LIMIT_CPU)}\", memory: {int(WORKER_LIMIT_RAM_GB)}Gi}}",
        "keda:",
        "  enabled: true",
        f"  minReplicas: {keda_min}",
        f"  maxReplicas: {workers}",
        f"  lagCount: {lag}",
        "backend:",
        f"  replicas: {api}",
    ])
    compose_env = "\n".join([
        f"EXECUTION_WORKERS={workers}",
        f"RUN_RETENTION_DAYS={inp.retention_days}",
    ])

    assumptions = [
        {"key": "worker", "text": "A browser worker uses ~1 CPU and ~1.5 GB under load and runs one job at a time.",
         "source": "SELF_HOSTING.md; execution-engine core/job-queue.ts (COUNT 1)"},
        {"key": "utilisation", "text": "Workers are planned at 70% busy in the peak hour so normal variance does not grow the queue.",
         "source": "same target as executionWorker.autoscaling (values.yaml)"},
        {"key": "burst", "text": "The biggest suite run dispatches all its cases (x matrix) at once; waits are measured for that burst.",
         "source": "job_dispatcher: SEPARATE/PARALLEL push one job per case"},
        {"key": "capture", "text": f"Capture level '{inp.capture_level}' allows: {', '.join(LEVEL_KINDS[inp.capture_level]) or 'nothing'}. "
                                    "Video is kept on failure only (default preference); a trace is kept on every run at 'full'; HAR is opt-in and not counted.",
         "source": "app/services/data_policy.py; execution-engine core/artifact-store.ts keepByPrefs"},
        {"key": "sizes", "text": "Artifact sizes are declared averages (KB): " +
                                 ", ".join(f"{k}={_r(v)}" for k, v in sizes.items()) +
                                 ". Replace them with your own measured averages.",
         "source": "declared (not measured in code)"},
        {"key": "postgres", "text": f"Each job writes about {pg_kb} KB to Postgres (run + result rows, logs, network events), x{PG_INDEX_OVERHEAD} for indexes and bloat.",
         "source": "declared"},
        {"key": "celery", "text": f"Each finished job costs ~{CELERY_TASK_SECONDS_PER_JOB} Celery task-seconds; a Celery worker runs {CELERY_CONCURRENCY} at once, planned at 50% busy.",
         "source": "declared"},
        {"key": "retention", "text": "Runs and their artifacts older than RUN_RETENTION_DAYS are deleted, which bounds storage.",
         "source": "SELF_HOSTING.md (default 90)"},
        {"key": "minimum", "text": "Whatever the formulas say, a single-host install wants at least 4 CPU / 8 GB.",
         "source": "SELF_HOSTING.md"},
    ]
    if inp.mobile_emulator:
        assumptions.append({"key": "mobile", "text": "Each Android emulator needs ~4 GB RAM and ~2 CPU on a KVM host.",
                            "source": "PRODUCTION_DEPLOY.md (RAM); CPU declared"})
    if inp.ha:
        assumptions.append({"key": "ha", "text": "HA: N+1 browser workers, 2 API/Celery/frontend replicas, a Postgres standby, 3-node Redis (Sentinel) and a 4-node object store.",
                            "source": "declared"})

    return {
        "inputs": inp.model_dump(),
        "results": {
            "workers": workers,
            "worker_cpu": w_cpu,
            "worker_ram_gb": w_ram,
            "peak_jobs_per_hour": peak,
            "api_replicas": api,
            "celery_replicas": celery,
            "postgres": {"size_gb": pg_gb, "cpu": pg_cpu, "ram_gb": pg_ram},
            "redis_mb": redis_mb,
            "object_store_gb": obj_gb,
            "artifact_kb_per_job": per_exec_kb,
            "artifact_kb_by_kind": {k: _r(v) for k, v in kinds_kb.items()},
            "mobile": mobile,
            "total_cpu": total_cpu,
            "total_ram_gb": total_ram,
            # A single host never goes below the documented 4 CPU / 8 GB floor.
            "recommended_host": ({"cpu": max(total_cpu, 4.0), "ram_gb": max(total_ram, 8.0)}
                                 if single_host else None),
            "single_host": single_host,
            "components": components,
        },
        "compose": {"EXECUTION_WORKERS": workers, "env": compose_env},
        "helm": {"replicas": workers, "keda_min_replicas": keda_min, "keda_max_replicas": workers,
                 "lag_count": lag, "values_yaml": helm_yaml},
        "formulas": s.rows,
        "assumptions": assumptions,
        "defaults": {"artifact_sizes_kb": ARTIFACT_SIZE_KB, "capture_levels": list(CAPTURE_LEVELS)},
    }
