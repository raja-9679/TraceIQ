"""Shared pytest setup for the backend suite.

`app.core.config` instantiates `Settings()` at module scope, so importing almost
anything under `app.` fails unless the required environment variables exist. This
conftest supplies safe placeholders before any app module is imported, which is
what lets pure unit tests run without Postgres, Redis, or MinIO.

pytest imports conftest.py before collecting test modules, so setting os.environ
here happens early enough.

Values here are deliberately development-grade. ENVIRONMENT stays
"development" so Settings.validate_for_deployment() does not reject them —
tests that exercise the production checks construct their own Settings with
ENVIRONMENT="production" explicitly.
"""
import os

_TEST_ENV = {
    "ENVIRONMENT": "development",
    "DATABASE_URL": "postgresql+asyncpg://traceiq:testpw@localhost:5432/traceiq_test",
    "CELERY_BROKER_URL": "redis://localhost:6379/0",
    "CELERY_RESULT_BACKEND": "redis://localhost:6379/0",
    "MINIO_ENDPOINT": "localhost:9000",
    "MINIO_ACCESS_KEY": "test-access-key",
    "MINIO_SECRET_KEY": "test-secret-key",
    "SECRET_KEY": "test-only-secret-not-for-any-real-deployment",
    "WEBHOOK_SECRET": "test-only-webhook-secret-not-for-real-use",
}

# setdefault so a caller can point the suite at a real stack by exporting these.
for _key, _value in _TEST_ENV.items():
    os.environ.setdefault(_key, _value)
