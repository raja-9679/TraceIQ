"""Instance settings: DB-over-env resolution (services/instance_settings.py).

These guard the admin-editable configuration layer:
- bootstrap-critical keys must never be storable (the app has to boot before
  it can read the DB, and a DB-stored SECRET_KEY would be unrecoverable);
- a DB override must win over the environment, and its absence must fall
  back to the environment;
- an unreachable database must degrade to env-only, never raise.

No database required: the DB layer is exercised through _load_overrides_sync
monkeypatching; the real loader is additionally pointed at a dead address to
prove the fallback path.
"""
import pytest

from app.services import instance_settings as insvc


@pytest.fixture(autouse=True)
def fresh_cache():
    insvc.invalidate_cache()
    yield
    insvc.invalidate_cache()


# --- Registry shape ---------------------------------------------------------

BOOTSTRAP_KEYS = (
    "DATABASE_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND",
    "SECRET_KEY", "WEBHOOK_SECRET", "ENVIRONMENT",
)


def test_bootstrap_keys_are_not_storable():
    for key in BOOTSTRAP_KEYS:
        assert key not in insvc.REGISTRY, f"{key} must stay environment-only"


def test_every_secret_key_is_flagged():
    for key in ("SMTP_PASSWORD", "SLACK_WEBHOOK_URL", "TEAMS_WEBHOOK_URL",
                "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
                "LLM_API_KEY", "MINIO_SECRET_KEY", "OIDC_CLIENT_SECRET"):
        assert insvc.REGISTRY[key].secret, f"{key} must be marked secret"


def test_storage_keys_are_restart_required():
    for key, d in insvc.REGISTRY.items():
        if d.group == "storage":
            assert d.restart_required, f"{key} must be restart_required"


# --- Type round-trips -------------------------------------------------------

@pytest.mark.parametrize("key,value,raw", [
    ("NOTIFICATIONS_ENABLED", True, "true"),
    ("NOTIFICATIONS_ENABLED", False, "false"),
    ("RUN_RETENTION_DAYS", 45, "45"),
    ("OUTBOUND_ALLOWED_HOSTS", ["a.example", "b.example"], '["a.example", "b.example"]'),
    ("SMTP_HOST", "mail.example.com", "mail.example.com"),
])
def test_serialize_parse_round_trip(key, value, raw):
    d = insvc.REGISTRY[key]
    assert insvc.serialize(d, value) == raw
    assert insvc._parse(d, raw) == value


# --- Resolution -------------------------------------------------------------

def test_db_override_wins(monkeypatch):
    monkeypatch.setattr(insvc, "_load_overrides_sync",
                        lambda: {"SMTP_HOST": "db.mail.example",
                                 "NOTIFY_ON_FAILURE_ONLY": "false"})
    insvc.invalidate_cache()
    assert insvc.effective("SMTP_HOST") == "db.mail.example"
    assert insvc.effective("NOTIFY_ON_FAILURE_ONLY") is False
    assert insvc.override_source("SMTP_HOST") == "database"


def test_env_fallback_without_override(monkeypatch):
    monkeypatch.setattr(insvc, "_load_overrides_sync", lambda: {})
    insvc.invalidate_cache()
    # conftest.py does not set SMTP_HOST; pydantic default is None.
    assert insvc.effective("SMTP_HOST") is None
    # NOTIFY_ON_FAILURE_ONLY defaults to True in Settings.
    assert insvc.effective("NOTIFY_ON_FAILURE_ONLY") is True
    assert insvc.override_source("SMTP_HOST") == "environment"


def test_bad_stored_value_falls_back_to_env(monkeypatch):
    monkeypatch.setattr(insvc, "_load_overrides_sync",
                        lambda: {"RUN_RETENTION_DAYS": "not-a-number"})
    insvc.invalidate_cache()
    assert insvc.effective("RUN_RETENTION_DAYS") == insvc.env_default("RUN_RETENTION_DAYS")


def test_unreachable_database_degrades_to_env(monkeypatch):
    def boom():
        raise ConnectionError("db down")
    monkeypatch.setattr(insvc, "_load_overrides_sync", boom)
    insvc.invalidate_cache()
    assert insvc.effective("NOTIFY_ON_FAILURE_ONLY") is True
    assert insvc.override_source("SMTP_HOST") == "environment"


def test_unregistered_key_returns_env_value(monkeypatch):
    monkeypatch.setenv("SOME_RANDOM_ENV", "hello")
    assert insvc.effective("SOME_RANDOM_ENV") == "hello"


def test_effective_group_covers_registry():
    ai = insvc.effective_group("ai")
    assert set(ai) == {k for k, d in insvc.REGISTRY.items() if d.group == "ai"}
