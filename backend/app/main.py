from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.core.database import init_db
from app.core.storage import minio_client
from app.api import endpoints, auth

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB
    await init_db()
    # Ensure MinIO bucket exists
    minio_client.ensure_bucket()
    yield

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Quality Intelligence Platform", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(endpoints.router, prefix="/api")
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])

@app.get("/health")
def health_check():
    return {"status": "ok"}
