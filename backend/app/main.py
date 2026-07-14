from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.core.database import init_db, close_db
from app.core.storage import minio_client
from app.core.limiter import limiter
from app.api import (
    api_keys, auth, settings, workspaces, projects, admin,
    workspace_webhooks, visual_baselines, personas, heal_proposals,
    flake_records, case_generation, comparison_runs, agent_ownership,
    agent_reference, environments, analytics, inspect,
)
from app.api.endpoints import test_suites, test_cases, test_runs, websockets, schedules
from app.core.config import settings as core_settings
import logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up...")
    await init_db()
    minio_client.ensure_bucket()
    logger.info("Startup complete")
    yield
    logger.info("Shutting down...")
    await close_db()
    logger.info("Shutdown complete")


app = FastAPI(title="Quality Intelligence Platform", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[str(origin)
                   for origin in core_settings.BACKEND_CORS_ORIGINS],
    allow_credentials=True,
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
app.include_router(websockets.router, prefix="/api", tags=["websockets"])
app.include_router(schedules.router, prefix="/api/schedules", tags=["schedules"])
app.include_router(api_keys.router, prefix="/api", tags=["api-keys"])
app.include_router(workspace_webhooks.router, prefix="/api", tags=["webhooks"])
app.include_router(visual_baselines.router, prefix="/api", tags=["visual-baselines"])
app.include_router(personas.router, prefix="/api", tags=["personas"])
app.include_router(heal_proposals.router, prefix="/api", tags=["heal-proposals"])
app.include_router(flake_records.router, prefix="/api", tags=["flakes"])
app.include_router(case_generation.router, prefix="/api", tags=["case-generation"])
app.include_router(comparison_runs.router, prefix="/api", tags=["comparison-runs"])
app.include_router(agent_ownership.router, prefix="/api", tags=["agent-ownership"])
app.include_router(agent_reference.router, prefix="/api", tags=["agent-reference"])
app.include_router(environments.router, prefix="/api", tags=["environments"])
app.include_router(analytics.router, prefix="/api", tags=["analytics"])
app.include_router(inspect.router, prefix="/api", tags=["inspect"])


@app.get("/health")
def health_check():
    return {"status": "ok"}
