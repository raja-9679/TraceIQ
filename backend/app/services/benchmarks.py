"""Cross-site benchmarking (WP12c).

One suite, N target base URLs: each target becomes one suite-level TestRun
with `target_url` set — the comparison-run override, which the dispatcher
applies as the run's environment `base_url` so relative gotos (`/cart`)
resolve against that site. Absolute goto URLs are NOT rewritten; the report
lists cases whose gotos are absolute, because they hit the same site in
every column.

Targets are validated like every other user-supplied URL (net_guard), and when
the project enforces a worker egress allowlist every target host must be on
it: overriding the base URL must not be a way around the network policy (the
environment base URL is otherwise allowed implicitly).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit
from app.services.run_status import TERMINAL_VALUES

MIN_TARGETS = 2
MAX_TARGETS = 10
MAX_LABEL = 80
MAX_URL = 2000

# Page-budget metrics shown in the matrix (PageBudgetSample.metrics keys).
MATRIX_METRICS = ("lcp_ms", "fcp_ms", "cls", "inp_ms", "ttfb_ms", "load_ms")


class BenchmarkError(ValueError):
    pass


def normalize_base_url(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s or len(s) > MAX_URL:
        raise BenchmarkError(f"a target URL must be 1-{MAX_URL} characters")
    parts = urlsplit(s)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise BenchmarkError(f"{s[:100]!r} is not an http(s) URL")
    if parts.username or parts.password:
        raise BenchmarkError("target URLs may not carry credentials")
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def host_allowed(host: str, allowed: Iterable[str]) -> bool:
    """Same matching as the worker egress allowlist: an entry matches the host
    and its subdomains; `*.x` matches subdomains of x."""
    h = (host or "").lower().rstrip(".")
    for entry in allowed or []:
        e = (entry or "").lower().strip()
        if not e:
            continue
        if e.startswith("*."):
            if h.endswith(e[1:]):
                return True
        elif h == e or h.endswith("." + e):
            return True
    return False


def clean_targets(raw: Any) -> List[Dict[str, str]]:
    """Shape check only (no DNS): [{label, base_url}], unique, 2..10."""
    if not isinstance(raw, list):
        raise BenchmarkError("targets must be a list")
    if not MIN_TARGETS <= len(raw) <= MAX_TARGETS:
        raise BenchmarkError(f"a benchmark needs {MIN_TARGETS}-{MAX_TARGETS} targets")
    out: List[Dict[str, str]] = []
    for t in raw:
        if isinstance(t, str):
            t = {"base_url": t}
        if not isinstance(t, dict):
            raise BenchmarkError("each target is a URL or {label, base_url}")
        url = normalize_base_url(t.get("base_url") or t.get("url"))
        label = str(t.get("label") or "").strip()[:MAX_LABEL] or (urlsplit(url).hostname or url)
        if any(o["base_url"] == url for o in out):
            raise BenchmarkError(f"{url} is listed twice")
        if any(o["label"] == label for o in out):
            label = f"{label} ({len(out) + 1})"
        out.append({"label": label, "base_url": url})
    return out


async def check_target_reachable_policy(url: str, egress_policy: Optional[Dict[str, Any]]) -> None:
    """net_guard + the project's egress allowlist; raises BenchmarkError."""
    from app.core.net_guard import UnsafeUrlError, validate_outbound_url
    try:
        await validate_outbound_url(url)
    except UnsafeUrlError as exc:
        raise BenchmarkError(f"{url}: {exc}")
    if egress_policy and egress_policy.get("enforce"):
        host = urlsplit(url).hostname or ""
        if not host_allowed(host, egress_policy.get("allowed_hosts") or []):
            raise BenchmarkError(
                f"{host} is not on this project's network allowlist; add it under the "
                "project's network policy first")


def absolute_goto_cases(cases: Iterable[Any]) -> List[Dict[str, Any]]:
    """Cases with an absolute goto URL: those do not follow the target."""
    out = []
    for c in cases:
        for step in getattr(c, "steps", None) or []:
            if isinstance(step, dict) and step.get("type") == "goto":
                v = str(step.get("value") or "")
                if "://" in v[:12] or v.startswith("//"):
                    out.append({"id": c.id, "name": c.name})
                    break
    return out


def _status(v: Any) -> str:
    return str(getattr(v, "value", v) or "")


def build_matrix(targets: Sequence[Dict[str, str]], runs: Sequence[Dict[str, Any]],
                 results: Sequence[Dict[str, Any]],
                 samples: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Per-site summary, per-test pass/fail grid and page-budget averages.

    `runs`: [{id, target_url, status, duration_ms}] — matched to targets by
    target_url; `results`: [{test_run_id, test_name, test_case_id, status,
    duration_ms}]; `samples`: [{test_run_id, metrics}]."""
    run_for: Dict[str, Dict[str, Any]] = {}
    for r in runs:
        run_for.setdefault(r.get("target_url") or "", r)
    by_run: Dict[int, List[Dict[str, Any]]] = {}
    for res in results:
        by_run.setdefault(res["test_run_id"], []).append(res)
    samples_by_run: Dict[int, List[dict]] = {}
    for s in samples:
        if isinstance(s.get("metrics"), dict):
            samples_by_run.setdefault(s["test_run_id"], []).append(s["metrics"])

    sites = []
    grid: Dict[str, Dict[str, Any]] = {}
    seen_metrics: List[str] = []
    for idx, t in enumerate(targets):
        run = run_for.get(t["base_url"])
        res = by_run.get(run["id"], []) if run else []
        passed = sum(1 for r in res if _status(r.get("status")) == "passed")
        failed = sum(1 for r in res if _status(r.get("status")) in ("failed", "error"))
        durations = [float(r["duration_ms"]) for r in res if r.get("duration_ms") is not None]
        budgets: Dict[str, Optional[float]] = {}
        run_samples = samples_by_run.get(run["id"], []) if run else []
        for key in MATRIX_METRICS:
            vals = [float(m[key]) for m in run_samples
                    if isinstance(m.get(key), (int, float)) and not isinstance(m.get(key), bool)]
            if vals:
                budgets[key] = round(sum(vals) / len(vals), 3 if key == "cls" else 1)
                if key not in seen_metrics:
                    seen_metrics.append(key)
        sites.append({
            "index": idx, "label": t["label"], "base_url": t["base_url"],
            "run_id": run["id"] if run else None,
            "status": _status(run.get("status")) if run else "missing",
            "total": len(res), "passed": passed, "failed": failed,
            "pass_rate": round(100.0 * passed / len(res), 1) if res else None,
            "duration_ms": (run.get("duration_ms") if run and run.get("duration_ms") is not None
                            else (round(sum(durations), 1) if durations else None)),
            "avg_case_ms": round(sum(durations) / len(durations), 1) if durations else None,
            "budgets": budgets, "budget_samples": len(run_samples),
        })
        for r in res:
            key = f"c{r['test_case_id']}" if r.get("test_case_id") else f"n{r.get('test_name')}"
            row = grid.setdefault(key, {"key": key, "test_name": r.get("test_name"),
                                        "test_case_id": r.get("test_case_id"),
                                        "cells": [None] * len(targets)})
            prev = row["cells"][idx]
            # Data-driven rows or retries can repeat a case: the worst wins.
            st = _status(r.get("status"))
            if prev is None or (prev["status"] == "passed" and st != "passed"):
                row["cells"][idx] = {"status": st, "duration_ms": r.get("duration_ms")}

    tests = sorted(grid.values(), key=lambda g: (g["test_name"] or ""))
    for g in tests:
        statuses = {c["status"] for c in g["cells"] if c}
        g["differs"] = len(statuses) > 1 or (bool(statuses) and any(c is None for c in g["cells"]))

    rated = [s for s in sites if s["pass_rate"] is not None]
    timed = [s for s in sites if s["avg_case_ms"] is not None]
    return {
        "sites": sites,
        "tests": tests,
        "metrics": seen_metrics,
        "summary": {
            "best_pass_rate": max(rated, key=lambda s: s["pass_rate"])["label"] if rated else None,
            "worst_pass_rate": min(rated, key=lambda s: s["pass_rate"])["label"] if rated else None,
            "fastest": min(timed, key=lambda s: s["avg_case_ms"])["label"] if timed else None,
            "differing_tests": sum(1 for g in tests if g["differs"]),
            "complete": all(s["status"] in TERMINAL_VALUES for s in sites),
        },
    }
