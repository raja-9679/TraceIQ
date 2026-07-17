from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    DATABASE_URL: str
    CELERY_BROKER_URL: str
    CELERY_RESULT_BACKEND: str
    MINIO_ENDPOINT: str
    # MINIO_PUBLIC_URL: str = "https://traceiqstore.thehindu.co.in"
    MINIO_PUBLIC_URL: str = "http://localhost:9000"
    MINIO_ACCESS_KEY: str
    MINIO_SECRET_KEY: str
    MINIO_BUCKET_NAME: str = "test-artifacts"
    OPENAI_API_KEY: str = ""
    EXECUTION_ENGINE_URL: str = "http://execution-engine:3000/run"
    # Set in the environment as a JSON list, e.g.
    #   BACKEND_CORS_ORIGINS=["https://app.traceiq.io","https://admin.traceiq.io"]
    # Defaults to local dev origins — set explicitly in production. A bare "*"
    # is honoured but forces credentials off (see cors_allow_credentials /
    # main.py) since browsers reject wildcard-with-credentials.
    BACKEND_CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
    ]

    # Security
    SECRET_KEY: str
    WEBHOOK_SECRET: Optional[str] = None  # Dedicated secret for webhook/finalize endpoints; falls back to SECRET_KEY
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30  # 30 minutes

    # Webhook Queue Configuration
    REDIS_WEBHOOK_QUEUE: str = "webhook:results"
    REDIS_WEBHOOK_FAILED_QUEUE: str = "webhook:failed"
    WEBHOOK_PROCESSOR_INTERVAL: int = 10  # seconds
    WEBHOOK_MAX_RETRIES: int = 3
    TEST_TIMEOUT_MINUTES: int = 30  # timeout for stuck tests (legacy cleanup task)

    # How long since the last job result arrived before a run is considered "stuck".
    # A run actively receiving results is NOT considered stuck even if it has been
    # running for hours. Default: 15 minutes of inactivity.
    STALE_RUN_INACTIVITY_MINUTES: int = 15

    # Absolute maximum wall-clock duration any single run is allowed to stay in
    # RUNNING state, regardless of activity. Prevents zombie runs from lingering
    # forever. Default: 6 hours.
    MAX_RUN_DURATION_HOURS: int = 6

    # Data retention: finished TestRuns (and their TestCaseResults + MinIO
    # artifacts) older than this many days are purged by a Celery beat task.
    # 0 disables retention (keep everything forever).
    RUN_RETENTION_DAYS: int = 0
    # How many runs to purge per pass, to bound each task's work.
    RETENTION_BATCH_SIZE: int = 500

    # Notification Settings
    # Master switch - if false, no notifications are sent regardless of other settings
    NOTIFICATIONS_ENABLED: bool = False
    # Individual channel controls (only checked if NOTIFICATIONS_ENABLED=true)
    EMAIL_NOTIFICATIONS_ENABLED: bool = False
    SLACK_NOTIFICATIONS_ENABLED: bool = False
    TEAMS_NOTIFICATIONS_ENABLED: bool = False
    # Only send notifications for failed runs
    NOTIFY_ON_FAILURE_ONLY: bool = True

    # Email (SMTP) Configuration
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM: str = "noreply@traceiq.io"

    # Slack Configuration
    SLACK_WEBHOOK_URL: Optional[str] = None

    # Teams Configuration
    TEAMS_WEBHOOK_URL: Optional[str] = None

    @property
    def cors_origins(self) -> list[str]:
        return self.BACKEND_CORS_ORIGINS

    @property
    def cors_allow_credentials(self) -> bool:
        # Browsers reject wildcard origin with credentials; disable credentials
        # when a wildcard is configured so the wildcard actually works.
        return "*" not in self.BACKEND_CORS_ORIGINS

    class Config:
        env_file = ".env"


settings = Settings()
