"""Database and broker URL derivation, and the production transport checks.

Two problems this covers.

*Ten places built a database engine.* One async engine plus nine sync ones,
each repeating `DATABASE_URL.replace("+asyncpg", "")`. Threading TLS through
that by hand would mean getting it right in ten places and keeping it right —
and it is not even the same parameter in each: asyncpg takes `ssl=`, psycopg2
takes `sslmode=`, and they are not interchangeable in a query string. So a URL
copied from one to the other either silently ignores the setting or fails to
parse.

*`validate_for_deployment` checked secrets but never transport.* It refused to
boot production on a placeholder SECRET_KEY or `minioadmin`, yet was perfectly
happy with a plaintext database connection, an unauthenticated Redis carrying
decrypted job secrets, and a plain-HTTP object store.

The transport findings warn by default and refuse to boot only under
`REQUIRE_TRANSPORT_SECURITY`. Making them unconditionally fatal would stop
every existing deployment from starting on upgrade, and the escape hatch
operators reach for is `ENVIRONMENT=development` — which also switches off the
secret checks. A check people route around by disabling every check is worse
than no check at all. Both halves of that are tested below.
"""
import pytest

from app.core.config import Settings, db_url_for, redis_url_with_tls

BASE = "postgresql+asyncpg://u:p@db:5432/traceiq"


def _settings(**overrides) -> Settings:
    base = dict(
        ENVIRONMENT="production",
        DATABASE_URL="postgresql+asyncpg://u:strongpass@db:5432/traceiq?sslmode=require",
        CELERY_BROKER_URL="rediss://:pw@redis:6379/0",
        CELERY_RESULT_BACKEND="rediss://:pw@redis:6379/0",
        MINIO_ENDPOINT="minio:9000",
        MINIO_USE_SSL=True,
        MINIO_ACCESS_KEY="a-real-looking-access-key",
        MINIO_SECRET_KEY="a-real-looking-secret-key-value",
        SECRET_KEY="7f3a9c2e5b8d1f4a6c9e2b5d8f1a4c7e",
        WEBHOOK_SECRET="2b5d8f1a4c7e9f3a6c2e5b8d1f4a7c9e",
        BACKEND_CORS_ORIGINS=["https://app.example.com"],
    )
    base.update(overrides)
    return Settings(**base)


# --------------------------------------------------------------------------
# Database URL
# --------------------------------------------------------------------------

def test_async_url_is_returned_unchanged_by_default():
    assert db_url_for(BASE, sync=False) == BASE


def test_sync_url_drops_the_asyncpg_driver():
    assert db_url_for(BASE, sync=True) == "postgresql://u:p@db:5432/traceiq"


def test_sslmode_is_renamed_to_ssl_for_asyncpg_preserving_the_value():
    # SQLAlchemy's asyncpg dialect rejects a `sslmode` key but maps `ssl`
    # straight back onto sslmode semantics — so this is a rename, not a
    # boolean coercion. `ssl=true` raises ClientConfigurationError at connect
    # time ("sslmode parameter must be one of: disable, allow, prefer, ...").
    out = db_url_for(BASE + "?sslmode=require", sync=False)
    assert "sslmode" not in out
    assert "ssl=require" in out


def test_sslmode_is_preserved_for_the_sync_driver():
    out = db_url_for(BASE + "?sslmode=require", sync=True)
    assert "sslmode=require" in out


def test_sslmode_disable_is_preserved_verbatim():
    assert "ssl=disable" in db_url_for(BASE + "?sslmode=disable", sync=False)


def test_verify_full_is_preserved_verbatim():
    # Coercing this to a boolean would silently downgrade certificate
    # verification to plain encryption.
    assert "ssl=verify-full" in db_url_for(BASE + "?sslmode=verify-full", sync=False)


def test_other_query_parameters_survive_translation():
    out = db_url_for(BASE + "?sslmode=require&application_name=traceiq", sync=False)
    assert "application_name=traceiq" in out


def test_a_url_with_no_query_string_is_untouched():
    assert db_url_for(BASE, sync=True) == "postgresql://u:p@db:5432/traceiq"


def test_translation_is_idempotent():
    once = db_url_for(BASE + "?sslmode=require", sync=False)
    assert db_url_for(once, sync=False) == once


# --------------------------------------------------------------------------
# Redis URL
# --------------------------------------------------------------------------

def test_plain_redis_url_is_left_alone_when_tls_is_not_requested():
    assert redis_url_with_tls("redis://redis:6379/0", use_tls=False) == "redis://redis:6379/0"


def test_redis_url_is_upgraded_to_rediss_when_tls_is_requested():
    assert redis_url_with_tls("redis://redis:6379/0", use_tls=True) == "rediss://redis:6379/0"


def test_an_already_tls_url_is_untouched():
    assert redis_url_with_tls("rediss://redis:6379/0", use_tls=True) == "rediss://redis:6379/0"


def test_credentials_survive_the_upgrade():
    out = redis_url_with_tls("redis://:secret@redis:6379/0", use_tls=True)
    assert out == "rediss://:secret@redis:6379/0"


def test_a_blank_redis_url_is_returned_as_is():
    assert redis_url_with_tls("", use_tls=True) == ""


# --------------------------------------------------------------------------
# Production transport checks
# --------------------------------------------------------------------------

def test_a_fully_secured_production_config_boots_in_strict_mode():
    _settings(MINIO_SSE_ALGORITHM="AES256",
              SECRETS_KEY="a-dedicated-secrets-key-value-here",
              REQUIRE_TRANSPORT_SECURITY=True).validate_for_deployment()


def test_a_plaintext_database_warns_by_default():
    # An existing deployment must still boot after upgrading.
    s = _settings(DATABASE_URL="postgresql+asyncpg://u:strongpass@db:5432/traceiq")
    assert any("sslmode" in w for w in s.validate_for_deployment())


def test_a_plaintext_database_is_fatal_under_strict_mode():
    s = _settings(DATABASE_URL="postgresql+asyncpg://u:strongpass@db:5432/traceiq",
                  REQUIRE_TRANSPORT_SECURITY=True)
    with pytest.raises(RuntimeError, match="sslmode"):
        s.validate_for_deployment()


def test_sslmode_disable_is_treated_as_no_tls():
    s = _settings(DATABASE_URL="postgresql+asyncpg://u:strongpass@db:5432/traceiq?sslmode=disable",
                  REQUIRE_TRANSPORT_SECURITY=True)
    with pytest.raises(RuntimeError, match="sslmode"):
        s.validate_for_deployment()


def test_an_unauthenticated_plaintext_redis_is_fatal_under_strict_mode():
    s = _settings(CELERY_BROKER_URL="redis://redis:6379/0",
                  CELERY_RESULT_BACKEND="redis://redis:6379/0",
                  REQUIRE_TRANSPORT_SECURITY=True)
    with pytest.raises(RuntimeError, match="CELERY_BROKER_URL"):
        s.validate_for_deployment()


def test_a_password_protected_plaintext_redis_is_still_flagged():
    # Common on a private network, and weaker than TLS — job payloads carrying
    # decrypted secrets cross it in clear.
    s = _settings(CELERY_BROKER_URL="redis://:pw@redis:6379/0",
                  CELERY_RESULT_BACKEND="redis://:pw@redis:6379/0")
    assert any("Redis" in w for w in s.validate_for_deployment())


def test_a_plaintext_object_store_is_flagged():
    s = _settings(MINIO_USE_SSL=False)
    warnings = s.validate_for_deployment()
    assert any("MinIO" in w or "object store" in w for w in warnings)


def test_missing_server_side_encryption_is_flagged():
    assert any("MINIO_SSE_ALGORITHM" in w for w in _settings().validate_for_deployment())


def test_strict_mode_does_not_fire_outside_production():
    # Strict mode is meaningless in dev, where nothing is configured for TLS.
    s = _settings(ENVIRONMENT="development", REQUIRE_TRANSPORT_SECURITY=True,
                  DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/traceiq",
                  CELERY_BROKER_URL="redis://localhost:6379/0",
                  CELERY_RESULT_BACKEND="redis://localhost:6379/0")
    assert s.validate_for_deployment() == []


def test_an_unset_secrets_key_is_flagged():
    assert any("SECRETS_KEY" in w for w in _settings().validate_for_deployment())


def test_development_is_not_subject_to_transport_checks():
    # Local dev has no TLS anywhere and must keep working.
    s = _settings(ENVIRONMENT="development",
                  DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/traceiq",
                  CELERY_BROKER_URL="redis://localhost:6379/0",
                  CELERY_RESULT_BACKEND="redis://localhost:6379/0",
                  MINIO_USE_SSL=False)
    assert s.validate_for_deployment() == []


def test_the_existing_secret_checks_still_fire():
    # Guard against the transport checks displacing what was already enforced.
    s = _settings(SECRET_KEY="changeme")
    with pytest.raises(RuntimeError):
        s.validate_for_deployment()
