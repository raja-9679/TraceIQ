from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.core.database import init_db, close_db
from app.core.storage import minio_client
from app.core.limiter import limiter
from app.api import auth, settings, workspaces, projects, admin
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


@app.get("/health")
def health_check():
    return {"status": "ok"}
