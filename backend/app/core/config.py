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
    BACKEND_CORS_ORIGINS: list[str] = ["*"]

    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30  # 30 minutes

    # Webhook Queue Configuration
    REDIS_WEBHOOK_QUEUE: str = "webhook:results"
    REDIS_WEBHOOK_FAILED_QUEUE: str = "webhook:failed"
    WEBHOOK_PROCESSOR_INTERVAL: int = 10  # seconds
    WEBHOOK_MAX_RETRIES: int = 3
    TEST_TIMEOUT_MINUTES: int = 30  # timeout for stuck tests

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

    class Config:
        env_file = ".env"


settings = Settings()
