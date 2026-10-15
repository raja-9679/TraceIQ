"""Server-side metrics next to k6 load-test results (WP8).

A `load-test` step may carry `params.prometheus_queries` — a list of
`{name, query}` PromQL expressions (CPU, p99 latency, DB connections...). When
someone opens the result, each query is evaluated as a range query over the
test's window against the instance's Prometheus (`PROMETHEUS_URL`), so the k6
client-side numbers sit next to what the servers were doing at the time.

Evaluated on read, not stored: Prometheus keeps the data, and storing series
per result would duplicate it into Postgres for no benefit.

The Prometheus URL is operator configuration, fetched through net_guard with
its own host as the private-network exemption (an in-cluster Prometheus is the
normal case) — metadata/link-local ranges stay refused, redirects are not
followed. Queries are user-authored but only ever sent to that one server.
Note what that means on a shared instance: any project member who can author a
load test can read any series that Prometheus holds. Point PROMETHEUS_URL at a
Prometheus (or a label-enforcing proxy such as prom-label-proxy) scoped to what
your users may see.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

MAX_QUERIES = 10
MAX_QUERY_LEN = 1000
MAX_POINTS = 300
PAD = timedelta(seconds=30)


def queries_from_steps(steps: Optional[List[Any]]) -> List[Dict[str, str]]:
    """The first load-test step's prometheus_queries, validated and capped."""
    for step in steps or []:
        step = step if isinstance(step, dict) else getattr(step, "__dict__", {})
        if step.get("type") != "load-test":
            continue
        raw = ((step.get("params") or {}).get("prometheus_queries")) or []
        out: List[Dict[str, str]] = []
        for i, q in enumerate(raw if isinstance(raw, list) else []):
            if isinstance(q, str):
                q = {"name": f"query {i + 1}", "query": q}
            if not isinstance(q, dict):
                continue
            query = str(q.get("query") or "").strip()
            if not query or len(query) > MAX_QUERY_LEN:
                continue
            out.append({"name": str(q.get("name") or f"query {i + 1}")[:80], "query": query})
            if len(out) >= MAX_QUERIES:
                break
        return out
    return []


def window_for(run_created: datetime, run_finalized: Optional[datetime],
               result_duration_ms: float, now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """Approximate test window. Results carry no start timestamp, so the end is
    when the run finalised (or now, while it is still running) and the start is
    that minus the result's own duration; both are padded so the ramp is in
    view. Accurate to the aggregator's few-second drain interval."""
    end = run_finalized or now or datetime.utcnow()
    start = end - timedelta(milliseconds=max(0.0, float(result_duration_ms or 0)))
    start = max(start, run_created - PAD) if run_created else start
    return start - PAD, end + PAD


def step_seconds(start: datetime, end: datetime) -> int:
    span = max(1.0, (end - start).total_seconds())
    return max(5, int(span // MAX_POINTS) + 1)


def parse_matrix(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Prometheus range-query response → [{labels, points: [[ts, float]]}]."""
    if body.get("status") != "success":
        raise ValueError(body.get("error") or "query failed")
    data = body.get("data") or {}
    if data.get("resultType") != "matrix":
        raise ValueError(f"expected a range vector, got {data.get('resultType')}")
    out = []
    for series in (data.get("result") or [])[:20]:
        points = []
        for ts, val in series.get("values") or []:
            try:
                points.append([float(ts), float(val)])
            except (TypeError, ValueError):
                continue
        out.append({"labels": series.get("metric") or {}, "points": points})
    return out


async def _fetch(base: str, params: Dict[str, Any], token: Optional[str]) -> Dict[str, Any]:
    """The single network call — tests replace this."""
    import httpx
    from app.core.net_guard import validate_outbound_url
    url = base.rstrip("/") + "/api/v1/query_range"
    await validate_outbound_url(url, extra_allowed_hosts=[urlsplit(base).hostname or ""])
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        resp = await client.get(url, params=params, headers=headers)
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("error")
        except Exception:
            detail = resp.text[:200]
        raise ValueError(f"Prometheus returned HTTP {resp.status_code}: {detail}")
    return resp.json()


async def evaluate(queries: List[Dict[str, str]], start: datetime, end: datetime) -> Dict[str, Any]:
    from app.services.instance_settings import effective
    base = str(effective("PROMETHEUS_URL") or "").strip()
    out: Dict[str, Any] = {
        "configured": bool(base),
        "window": {"start": start.isoformat() + "Z", "end": end.isoformat() + "Z"},
        "step_seconds": step_seconds(start, end),
        "series": [],
    }
    if not base or not queries:
        return out
    token = effective("PROMETHEUS_BEARER_TOKEN") or None
    for q in queries:
        entry: Dict[str, Any] = {"name": q["name"], "query": q["query"], "result": [], "error": None}
        try:
            body = await _fetch(base, {
                "query": q["query"],
                "start": start.timestamp() if start.tzinfo else (start - datetime(1970, 1, 1)).total_seconds(),
                "end": end.timestamp() if end.tzinfo else (end - datetime(1970, 1, 1)).total_seconds(),
                "step": out["step_seconds"],
            }, token)
            entry["result"] = parse_matrix(body)
        except Exception as exc:  # noqa: BLE001 — one bad query must not hide the rest
            entry["error"] = str(exc)[:300]
        out["series"].append(entry)
    return out
