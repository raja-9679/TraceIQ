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
Queries are restricted, because Prometheus has no tenancy of its own: a
PromQL string the step author wrote could read any series the server holds
(other tenants' services included). So by default only operator-approved
templates run — `PROMETHEUS_QUERY_TEMPLATES`, `name=PromQL` per line, named by
the step (`{"template": "cpu"}` or a bare `"cpu"`), with `$project_id`,
`$workspace_id` and `$run_id` substituted server-side so a template can be
label-scoped to the caller. Free-form PromQL needs the
`PROMETHEUS_ALLOW_ADHOC_QUERIES` opt-in (single-tenant instances, or a
label-enforcing proxy such as prom-label-proxy in front).
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
            template = str(q.get("template") or "").strip()[:80]
            query = str(q.get("query") or "").strip()
            if template:
                out.append({"name": str(q.get("name") or template)[:80],
                            "query": query, "template": template})
            elif not query or len(query) > MAX_QUERY_LEN:
                continue
            else:
                out.append({"name": str(q.get("name") or f"query {i + 1}")[:80], "query": query})
            if len(out) >= MAX_QUERIES:
                break
        return out
    return []


def parse_templates(entries: Optional[List[str]]) -> Dict[str, str]:
    """`PROMETHEUS_QUERY_TEMPLATES` lines (`name=PromQL`) → {name: PromQL}."""
    out: Dict[str, str] = {}
    for raw in entries or []:
        name, sep, query = str(raw or "").partition("=")
        if sep and name.strip() and query.strip():
            out[name.strip()] = query.strip()
    return out


_SCOPE_VARS = ("project_id", "workspace_id", "run_id")


def resolve_query(q: Dict[str, str], templates: Dict[str, str], allow_adhoc: bool,
                  scope: Dict[str, Any]) -> str:
    """The PromQL actually sent for one step entry, or ValueError.

    A template is looked up by its `template` name, or by a `query` that is
    exactly a template name. Scope values are integers we hold, substituted
    as digits only, so they cannot inject PromQL."""
    name = q.get("template") or (q.get("query") if q.get("query") in templates else None)
    if name:
        if name not in templates:
            raise ValueError(f"no approved query template named {name!r}")
        text = templates[name]
        # A missing scope value must not leave the literal placeholder in
        # (that would silently widen a label-scoped template).
        for var in _SCOPE_VARS:
            token = "$" + var
            if token in text:
                value = scope.get(var)
                if value is None:
                    raise ValueError(f"template {name!r} needs ${var}, which this result has none of")
                text = text.replace(token, str(int(value)))
        return text
    if not allow_adhoc:
        raise ValueError("free-form PromQL is disabled on this instance; use one of the "
                         "operator-approved query templates"
                         + (f" ({', '.join(sorted(templates))})" if templates else ""))
    return q["query"]


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


async def evaluate(queries: List[Dict[str, str]], start: datetime, end: datetime,
                   scope: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
    templates = parse_templates(effective("PROMETHEUS_QUERY_TEMPLATES") or [])
    allow_adhoc = bool(effective("PROMETHEUS_ALLOW_ADHOC_QUERIES"))
    for q in queries:
        entry: Dict[str, Any] = {"name": q["name"], "query": q.get("template") or q["query"],
                                 "result": [], "error": None}
        try:
            # The resolved text is not echoed back: a template may embed label
            # matchers the operator would rather not publish to every member.
            promql = resolve_query(q, templates, allow_adhoc, scope or {})
            body = await _fetch(base, {
                "query": promql,
                "start": start.timestamp() if start.tzinfo else (start - datetime(1970, 1, 1)).total_seconds(),
                "end": end.timestamp() if end.tzinfo else (end - datetime(1970, 1, 1)).total_seconds(),
                "step": out["step_seconds"],
            }, token)
            entry["result"] = parse_matrix(body)
        except Exception as exc:  # noqa: BLE001 — one bad query must not hide the rest
            entry["error"] = str(exc)[:300]
        out["series"].append(entry)
    return out
