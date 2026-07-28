"""Startup configuration checks (Settings.validate_for_deployment).

These guard the self-hosted distribution story: the images are pulled and run by
people who did not read the source, so a weak or default secret must stop the
process rather than produce a quietly insecure instance. A shared default
SECRET_KEY across deployments would let anyone forge a token for any of them.

No database required.
"""
import pytest

from app.core.config import Settings

STRONG = "9f2c8a1e5b7d3f6a0c4e8b2d7f1a5c93"          # 32 hex chars
OTHER_STRONG = "3a7e1c9d5b2f8a4e6c0d9b7f3a1e5c82"

BASE = dict(
    DATABASE_URL="postgresql+asyncpg://traceiq:s3cure-pw@postgres:5432/traceiq",
    CELERY_BROKER_URL="redis://:pw@redis:6379/0",
    CELERY_RESULT_BACKEND="redis://:pw@redis:6379/0",
    MINIO_ENDPOINT="minio:9000",
    MINIO_ACCESS_KEY="traceiq",
    MINIO_SECRET_KEY="a7f3c9e1b5d8a2f6c4e0b9d7a3f1c5e8",
)


def make(**overrides) -> Settings:
    # _env_file=None so a developer's local .env can't influence the result.
    return Settings(_env_file=None, **{**BASE, **overrides})


def test_strong_production_config_passes():
    s = make(ENVIRONMENT="production", SECRET_KEY=STRONG, WEBHOOK_SECRET=OTHER_STRONG)
    assert s.validate_for_deployment() == []


def test_development_is_not_blocked_by_weak_secrets():
    """Local work must stay frictionless; the checks are production-only."""
    s = make(ENVIRONMENT="development", SECRET_KEY="dev")
    assert s.validate_for_deployment() == []


@pytest.mark.parametrize(
    "secret,reason",
    [
        ("short", "characters"),
        ("hd673n-shortkey-24charxx", "characters"),      # too short
        ("changeme-changeme-changeme-change", "placeholder"),
        ("your-secret-key-goes-right-here!", "placeholder"),
        ("a" * 40, "variety"),
    ],
)
def test_rejects_weak_secret_key(secret, reason):
    s = make(ENVIRONMENT="production", SECRET_KEY=secret, WEBHOOK_SECRET=OTHER_STRONG)
    with pytest.raises(RuntimeError, match=reason):
        s.validate_for_deployment()


def test_rejects_cors_wildcard_in_production():
    s = make(
        ENVIRONMENT="production",
        SECRET_KEY=STRONG,
        WEBHOOK_SECRET=OTHER_STRONG,
        BACKEND_CORS_ORIGINS=["*"],
    )
    with pytest.raises(RuntimeError, match="CORS"):
        s.validate_for_deployment()


@pytest.mark.parametrize("value", ["minioadmin", "admin", "minio"])
def test_rejects_default_minio_credentials(value):
    s = make(
        ENVIRONMENT="production",
        SECRET_KEY=STRONG,
        WEBHOOK_SECRET=OTHER_STRONG,
        MINIO_ACCESS_KEY=value,
    )
    with pytest.raises(RuntimeError, match="MinIO default"):
        s.validate_for_deployment()


def test_rejects_default_database_password():
    s = make(
        ENVIRONMENT="production",
        SECRET_KEY=STRONG,
        WEBHOOK_SECRET=OTHER_STRONG,
        DATABASE_URL="postgresql+asyncpg://user:password@postgres:5432/x",
    )
    with pytest.raises(RuntimeError, match="default password"):
        s.validate_for_deployment()


def test_warns_but_allows_missing_webhook_secret():
    """Falling back to SECRET_KEY works but couples two rotation lifecycles.

    WEBHOOK_SECRET is passed explicitly as None because conftest puts a value in
    os.environ and pydantic-settings reads the environment even with
    _env_file=None.
    """
    s = make(ENVIRONMENT="production", SECRET_KEY=STRONG, WEBHOOK_SECRET=None)
    warnings = s.validate_for_deployment()
    assert any("WEBHOOK_SECRET" in w for w in warnings)


def test_warns_when_webhook_secret_equals_secret_key():
    s = make(ENVIRONMENT="production", SECRET_KEY=STRONG, WEBHOOK_SECRET=STRONG)
    warnings = s.validate_for_deployment()
    assert any("equals SECRET_KEY" in w for w in warnings)


def test_warns_when_private_network_targets_enabled():
    s = make(
        ENVIRONMENT="production",
        SECRET_KEY=STRONG,
        WEBHOOK_SECRET=OTHER_STRONG,
        ALLOW_PRIVATE_NETWORK_TARGETS=True,
    )
    warnings = s.validate_for_deployment()
    assert any("internal network" in w for w in warnings)


def test_error_message_tells_the_operator_what_to_run():
    """A self-hoster hitting this needs the fix in the message, not the docs."""
    s = make(ENVIRONMENT="production", SECRET_KEY="short", WEBHOOK_SECRET=OTHER_STRONG)
    with pytest.raises(RuntimeError) as exc:
        s.validate_for_deployment()
    assert "openssl rand -hex 32" in str(exc.value)
