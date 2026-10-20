"""Trace correlation with the application under test (WP16a, decision V1).

Opt-in per project (`Project.trace_settings.enabled`, off by default). When on,
the worker adds a W3C `traceparent` header to the requests browser and API
steps make to the project's OWN hosts — never to third parties — with one
trace id per case attempt and one span id per step, and records both in the
step's evidence (`step_evidence[].trace`). The backend later reads the spans
the application emitted under those ids from a configured trace backend
(app/services/trace_backends.py) and shows them next to the step.

"Own hosts" is the same set the frontend-quality crawler treats as the
project's own: every environment base URL ∪ every suite's `allowed_domains` ∪
the security allowlist (`site_crawl.known_hosts_from`). A comparison or
benchmark `target_url` is deliberately NOT added: a benchmark target is
usually somebody else's site.

Resolved at dispatch and carried in the job as `settings.trace_correlation`,
so a worker never decides its own host set. Not sent at capture level `none`
("step outcome only"): the ids are correlation data, not page content, but
that level promises nothing beyond the outcome.

CORS trade-off (`cross_origin`, default false): a custom request header on a
cross-origin `fetch`/XHR can make the browser send a preflight, and an API
whose CORS policy does not list `traceparent` would then fail the very request
the test exercises. So by default the worker adds the header to browser
requests only when they are navigations or same-origin with the frame that
makes them; API steps (Playwright's request context, no browser CORS) always
get it for own hosts. `cross_origin: true` adds it to cross-origin requests to
own hosts too — for an API that already allows `traceparent` (most
OpenTelemetry-instrumented ones do).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel

MAX_HOSTS = 200


class TraceSettings(BaseModel):
    enabled: bool = False
    cross_origin: bool = False


def stored_settings(project) -> TraceSettings:
    try:
        return TraceSettings(**(getattr(project, "trace_settings", None) or {}))
    except Exception:  # noqa: BLE001 — a corrupt blob means off, never on
        return TraceSettings()


def own_hosts_sync(project, session) -> List[str]:
    """The project's own hosts (sync session, for dispatch)."""
    from sqlmodel import select

    from app.models import ProjectEnvironment, TestSuite
    from app.services.access_matrix_runtime import security_settings_for
    from app.services.site_crawl import known_hosts_from
    envs = session.exec(select(ProjectEnvironment.base_url).where(
        ProjectEnvironment.project_id == project.id)).all()
    suites = session.exec(select(TestSuite.settings).where(
        TestSuite.project_id == project.id)).all()
    sec = security_settings_for(project).get("allowed_domains") or []
    return sorted(known_hosts_from(envs, suites, sec))[:MAX_HOSTS]


def for_dispatch(project, session, capture_level: Optional[str]) -> Optional[Dict[str, Any]]:
    """`settings.trace_correlation` for a job, or None (= the worker injects
    nothing). An empty host set is also None: there is nothing to inject into."""
    if project is None or capture_level == "none":
        return None
    cfg = stored_settings(project)
    if not cfg.enabled:
        return None
    hosts = own_hosts_sync(project, session)
    if not hosts:
        return None
    return {"enabled": True, "hosts": hosts, "cross_origin": cfg.cross_origin}
