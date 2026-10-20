from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.core.database import init_db, close_db
from app.core.storage import minio_client
from app.core.limiter import limiter
from app.api import (
    api_keys, audit, auth, data_policy, project_notifications, settings, workspaces, projects, admin,
    workspace_webhooks, visual_baselines, visual_review, personas, heal_proposals,
    flake_records, case_generation, comparison_runs, agent_ownership,
    agent_reference, environments, analytics, inspect,
)
from app.api.endpoints import test_suites, test_cases, test_runs, websockets, schedules, quality
from app.api import security as security_api
from app.api import compliance as compliance_api
from app.api import access_matrix as access_matrix_api
from app.api import estates as estates_api
from app.api import frontend_quality as frontend_quality_api
from app.api import journey_reports as journey_reports_api
from app.api import benchmarks as benchmarks_api
from app.api import issue_trackers as issue_trackers_api
from app.api import triage as triage_api
from app.api import reports as reports_api
from app.api import share_links as share_links_api
from app.api import billing as billing_api
from app.api import llm_usage as llm_usage_api
from app.api import costs as costs_api
from app.api import manual_testing as manual_testing_api
from app.api import step_hooks as step_hooks_api
from app.api import external_results as external_results_api
from app.api import status_pages as status_pages_api
from app.api import trust_center as trust_center_api
from app.api import assistant as assistant_api
from app.api import policies as policies_api
from app.api import agents as agents_api
from app.api import poc_reports as poc_reports_api
from app.api import jobs as jobs_api
from app.api import traceability as traceability_api
from app.api import requirements as requirements_api
from app.api import incidents as incidents_api
from app.api import dora as dora_api
from app.api import reliability_reports as reliability_reports_api
from app.api import app_builds as app_builds_api
from app.api import case_revisions as case_revisions_api
from app.api import observability as observability_api
from app.api import onboarding as onboarding_api
from app.api import sizing as sizing_api
from app.api import demo_workspace as demo_workspace_api
from app.api import test_packs as test_packs_api
from app.api import instance_settings as instance_settings_api
from app.api import llm_providers as llm_providers_api
from app.api import scim as scim_api
from app.api import dead_letter as dead_letter_api
from app.api import siem as siem_api
from app.api import access_reviews as access_reviews_api
from app.api import developer as developer_api
from app.api import fleet as fleet_api
from app.api import trace_correlation as trace_correlation_api
from app.api import paging as paging_api
from app.core.config import settings as core_settings
from app.core import telemetry
import logging

# Structured logging and error tracking first, so everything below — including
# the config validation warnings — comes out in the configured format.
telemetry.configure_logging(service="traceiq-backend")
telemetry.configure_error_tracking(service_name="traceiq-backend")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up...")
    # Fail fast on shipped-default secrets before anything is served. Raises on
    # fatal misconfiguration; self-hosted instances depend on this to avoid
    # running with a publicly-known JWT signing key.
    for warning in core_settings.validate_for_deployment():
        logger.warning("[config] %s", warning)
    await init_db()
    minio_client.ensure_bucket()
    # Optional first-boot instance admin (ADMIN_EMAIL/ADMIN_PASSWORD env).
    try:
        from app.core.database import async_session_factory
        from app.services.user_provisioning import ensure_bootstrap_admin
        async with async_session_factory() as session:
            await ensure_bootstrap_admin(session)
    except Exception:
        logger.exception("bootstrap admin provisioning failed; continuing startup")
    logger.info("Startup complete")
    yield
    logger.info("Shutting down...")
    await close_db()
    logger.info("Shutdown complete")


app = FastAPI(title="Quality Intelligence Platform", lifespan=lifespan)

# X-Request-ID in, contextvar for the logs, X-Request-ID out. Added before the
# tracer so the span and the log lines for one request can be joined.
app.add_middleware(telemetry.RequestIdMiddleware)
# Optional worker mTLS for /api/internal/* (WP8). A no-op unless
# REQUIRE_WORKER_MTLS is set; see app/core/worker_auth.py.
from app.core.worker_auth import WorkerMTLSMiddleware  # noqa: E402
app.add_middleware(WorkerMTLSMiddleware)
# OpenTelemetry: only active when OTEL_EXPORTER_OTLP_ENDPOINT is set.
from app.core.database import engine as _db_engine  # noqa: E402
telemetry.configure_tracing(app, service_name="traceiq-backend", sqlalchemy_engine=_db_engine)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# SCIM errors must come back in SCIM's own envelope with SCIM's own status —
# identity providers branch on both (a 409 without scimType=uniqueness makes
# Okta retry a create forever), so they cannot go through FastAPI's default
# HTTPException shape.
@app.exception_handler(scim_api.ScimError)
async def _scim_error_handler(request: Request, exc: scim_api.ScimError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.status, content=exc.to_dict())

# A delete whose cascade would reach another project's rows is refused, not
# executed: access was checked for the caller's project only.
from app.services.entity_delete import CrossProjectReference  # noqa: E402


@app.exception_handler(CrossProjectReference)
async def _cross_project_delete_handler(request: Request, exc: CrossProjectReference):
    # The exception names the other project(s) and table; the caller may have
    # no access to them, so that stays in the server log. The response says
    # only what the caller can act on.
    import logging
    from fastapi.responses import JSONResponse
    logging.getLogger(__name__).warning("delete refused: %s", exc)
    return JSONResponse(status_code=409, content={
        "detail": "This can't be deleted: data outside this project still refers "
                  "to it. Ask an admin of the other project to remove those "
                  "references first."})

app.add_middleware(
    CORSMiddleware,
    allow_origins=[str(origin)
                   for origin in core_settings.BACKEND_CORS_ORIGINS],
    allow_credentials=core_settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(test_suites.router, prefix="/api", tags=["suites"])
app.include_router(test_cases.router, prefix="/api", tags=["cases"])
app.include_router(test_runs.router, prefix="/api", tags=["runs"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(workspaces.router, prefix="/api", tags=["workspaces"])
app.include_router(projects.router, prefix="/api", tags=["projects"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(instance_settings_api.router, prefix="/api", tags=["instance-settings"])
app.include_router(llm_providers_api.router, prefix="/api", tags=["llm-providers"])
# SCIM is mounted twice on purpose: the shipped nginx only proxies /api/, so a
# compose or all-in-one deployment can only reach the /api variant from outside,
# while a split deployment points the IdP straight at the backend and uses the
# bare path. Both resolve to the same handlers.
app.include_router(scim_api.router, tags=["scim"])
app.include_router(scim_api.router, prefix="/api", tags=["scim"])
app.include_router(dead_letter_api.router, prefix="/api", tags=["dead-letter"])
app.include_router(siem_api.router, prefix="/api", tags=["siem"])
app.include_router(access_reviews_api.router, prefix="/api", tags=["access-reviews"])
app.include_router(compliance_api.router, prefix="/api", tags=["compliance"])
app.include_router(fleet_api.router, prefix="/api", tags=["fleet"])
app.include_router(trace_correlation_api.router, prefix="/api", tags=["trace-correlation"])
app.include_router(paging_api.router, prefix="/api", tags=["paging"])
app.include_router(websockets.router, prefix="/api", tags=["websockets"])
app.include_router(schedules.router, prefix="/api/schedules", tags=["schedules"])
app.include_router(api_keys.router, prefix="/api", tags=["api-keys"])
app.include_router(audit.router, prefix="/api", tags=["audit"])
app.include_router(data_policy.router, prefix="/api", tags=["data-policy"])
app.include_router(project_notifications.router, prefix="/api", tags=["notifications"])
app.include_router(workspace_webhooks.router, prefix="/api", tags=["webhooks"])
app.include_router(visual_baselines.router, prefix="/api", tags=["visual-baselines"])
app.include_router(visual_review.router, prefix="/api", tags=["visual-review"])
app.include_router(personas.router, prefix="/api", tags=["personas"])
app.include_router(heal_proposals.router, prefix="/api", tags=["heal-proposals"])
app.include_router(flake_records.router, prefix="/api", tags=["flakes"])
app.include_router(case_generation.router, prefix="/api", tags=["case-generation"])
app.include_router(comparison_runs.router, prefix="/api", tags=["comparison-runs"])
app.include_router(quality.router, prefix="/api", tags=["quality"])
app.include_router(security_api.router, prefix="/api", tags=["security"])
app.include_router(access_matrix_api.router, prefix="/api", tags=["access-matrix"])
app.include_router(estates_api.router, prefix="/api", tags=["estates"])
app.include_router(frontend_quality_api.router, prefix="/api", tags=["frontend-quality"])
app.include_router(journey_reports_api.router, prefix="/api", tags=["journey-reports"])
app.include_router(benchmarks_api.router, prefix="/api", tags=["benchmarks"])
app.include_router(issue_trackers_api.router, prefix="/api", tags=["issue-trackers"])
app.include_router(triage_api.router, prefix="/api", tags=["triage"])
app.include_router(reports_api.router, prefix="/api", tags=["reports"])
app.include_router(share_links_api.router, prefix="/api", tags=["share-links"])
app.include_router(billing_api.router, prefix="/api", tags=["billing"])
app.include_router(llm_usage_api.router, prefix="/api", tags=["llm-usage"])
app.include_router(manual_testing_api.router, prefix="/api", tags=["manual-testing"])
app.include_router(costs_api.router, prefix="/api", tags=["costs"])
app.include_router(step_hooks_api.router, prefix="/api", tags=["step-hooks"])
app.include_router(external_results_api.router, prefix="/api", tags=["external-results"])
app.include_router(status_pages_api.router, prefix="/api", tags=["status-pages"])
app.include_router(trust_center_api.router, prefix="/api", tags=["trust-center"])
app.include_router(assistant_api.router, prefix="/api", tags=["assistant"])
app.include_router(policies_api.router, prefix="/api", tags=["policies"])
app.include_router(agents_api.router, prefix="/api", tags=["agents"])
app.include_router(poc_reports_api.router, prefix="/api", tags=["poc-report"])
app.include_router(jobs_api.router, prefix="/api", tags=["local-worker-jobs"])
app.include_router(traceability_api.router, prefix="/api", tags=["traceability"])
app.include_router(requirements_api.router, prefix="/api", tags=["requirements"])
app.include_router(incidents_api.router, prefix="/api", tags=["incidents"])
app.include_router(dora_api.router, prefix="/api", tags=["dora"])
app.include_router(reliability_reports_api.router, prefix="/api", tags=["reliability-reports"])
app.include_router(app_builds_api.router, prefix="/api", tags=["app-builds"])
app.include_router(case_revisions_api.router, prefix="/api", tags=["case-revisions"])
app.include_router(developer_api.router, prefix="/api", tags=["developer"])
# Observability paths are absolute inside the router (/metrics, /health/ready,
# /api/admin/queue-health) — no prefix here.
app.include_router(observability_api.router, tags=["observability"])
app.include_router(onboarding_api.router, prefix="/api", tags=["onboarding"])
app.include_router(sizing_api.router, prefix="/api", tags=["sizing"])
app.include_router(demo_workspace_api.router, prefix="/api", tags=["demo-workspace"])
app.include_router(test_packs_api.router, prefix="/api", tags=["test-packs"])
app.include_router(agent_ownership.router, prefix="/api", tags=["agent-ownership"])
app.include_router(agent_reference.router, prefix="/api", tags=["agent-reference"])
app.include_router(environments.router, prefix="/api", tags=["environments"])
app.include_router(analytics.router, prefix="/api", tags=["analytics"])
app.include_router(inspect.router, prefix="/api", tags=["inspect"])


@app.get("/health")
def health_check():
    return {"status": "ok"}
