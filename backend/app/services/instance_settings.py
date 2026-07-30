"""Instance-wide settings: DB-over-env resolution for admin-editable config.

The env file / compose environment remains the bootstrap layer and the
default. Anything an admin saves in the UI lands in the instance_settings
table and WINS over the environment ("DB wins when set"). Deleting the row
resets to the environment value. Only keys declared in REGISTRY may be stored,
which keeps bootstrap-critical config (DATABASE_URL, SECRET_KEY, Redis) out of
the database by construction — the app must be able to boot before it can
read the DB.

Consumers call effective()/effective_sync() at use time. Values are cached
for a short TTL so every process (API, celery workers, beat) converges on a
UI change within seconds without a restart — except keys marked
restart_required (storage), which are read once at process start by their
consumer and advertised as such in the UI.

Resolution never *requires* the DB: any failure (table missing during
bootstrap, DB down) falls back to the environment value, so this layer can be
imported anywhere without ordering concerns.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SettingDef:
    key: str                 # canonical name; matches Settings attr / env var
    group: str               # UI section
    type: str = "str"        # str | int | bool | list
    secret: bool = False     # Fernet-encrypted at rest, masked in the API
    restart_required: bool = False
    label: str = ""
    description: str = ""


# Groups: email | notifications | ai | storage | sso | policies
REGISTRY: Dict[str, SettingDef] = {d.key: d for d in [
    # --- Email (SMTP) ---
    SettingDef("SMTP_HOST", "email", label="SMTP host"),
    SettingDef("SMTP_PORT", "email", type="int", label="SMTP port"),
    SettingDef("SMTP_USER", "email", label="SMTP username"),
    SettingDef("SMTP_PASSWORD", "email", secret=True, label="SMTP password"),
    SettingDef("SMTP_FROM", "email", label="From address"),
    # --- Notification channels ---
    SettingDef("NOTIFICATIONS_ENABLED", "notifications", type="bool",
               label="Notifications master switch"),
    SettingDef("EMAIL_NOTIFICATIONS_ENABLED", "notifications", type="bool",
               label="Email notifications"),
    SettingDef("SLACK_NOTIFICATIONS_ENABLED", "notifications", type="bool",
               label="Slack notifications"),
    SettingDef("TEAMS_NOTIFICATIONS_ENABLED", "notifications", type="bool",
               label="Teams notifications"),
    SettingDef("NOTIFY_ON_FAILURE_ONLY", "notifications", type="bool",
               label="Notify on failures only"),
    SettingDef("SLACK_WEBHOOK_URL", "notifications", secret=True,
               label="Slack webhook URL"),
    SettingDef("TEAMS_WEBHOOK_URL", "notifications", secret=True,
               label="Teams webhook URL"),
    # --- AI / LLM ---
    SettingDef("LLM_PROVIDER", "ai", label="Provider",
               description="anthropic | openai | gemini | ollama | openai-compatible; empty disables AI"),
    SettingDef("LLM_MODEL", "ai", label="Model"),
    SettingDef("ANTHROPIC_API_KEY", "ai", secret=True, label="Anthropic API key"),
    SettingDef("OPENAI_API_KEY", "ai", secret=True, label="OpenAI API key"),
    SettingDef("GEMINI_API_KEY", "ai", secret=True, label="Gemini API key"),
    SettingDef("OLLAMA_BASE_URL", "ai", label="Ollama base URL"),
    SettingDef("LLM_BASE_URL", "ai", label="OpenAI-compatible base URL"),
    SettingDef("LLM_API_KEY", "ai", secret=True, label="OpenAI-compatible API key"),
    # --- Storage (read at process start; UI shows a restart banner) ---
    SettingDef("MINIO_ENDPOINT", "storage", restart_required=True,
               label="S3/MinIO endpoint", description="host:port, backend-reachable"),
    SettingDef("MINIO_PUBLIC_URL", "storage", restart_required=True,
               label="S3/MinIO public URL", description="must be reachable by the BROWSER"),
    SettingDef("MINIO_ACCESS_KEY", "storage", restart_required=True,
               label="Access key"),
    SettingDef("MINIO_SECRET_KEY", "storage", secret=True, restart_required=True,
               label="Secret key"),
    SettingDef("MINIO_BUCKET_NAME", "storage", restart_required=True,
               label="Bucket"),
    # --- SSO (OIDC) ---
    SettingDef("OIDC_ISSUER", "sso", label="Issuer URL"),
    SettingDef("OIDC_CLIENT_ID", "sso", label="Client ID"),
    SettingDef("OIDC_CLIENT_SECRET", "sso", secret=True, label="Client secret"),
    SettingDef("OIDC_REDIRECT_URI", "sso", label="Redirect URI"),
    SettingDef("OIDC_POST_LOGIN_REDIRECT", "sso", label="Post-login redirect"),
    SettingDef("PASSWORD_LOGIN_DISABLED", "sso", type="bool",
               label="Disable password login (SSO only)",
               description="requires a saved OIDC config; instance admins can still "
                           "use password login as break-glass so you cannot lock yourself out"),
    # --- LDAP / on-prem Active Directory ---
    SettingDef("LDAP_SERVER_URL", "ldap", label="Server URL",
               description="ldaps://dc01.corp.example.com:636 — prefer ldaps; plain "
                           "ldap sends passwords in clear unless StartTLS is on"),
    SettingDef("LDAP_BIND_DN_TEMPLATE", "ldap", label="Bind DN template",
               description="{username} is substituted, e.g. {username}@corp.example.com "
                           "(AD) or uid={username},ou=people,dc=example,dc=com"),
    SettingDef("LDAP_SEARCH_BASE", "ldap", label="Search base (optional)",
               description="dc=corp,dc=example,dc=com — enables email/name lookup after bind"),
    SettingDef("LDAP_EMAIL_DOMAIN", "ldap", label="Email domain fallback",
               description="used when the directory returns no mail attribute and the "
                           "username is not an email"),
    SettingDef("LDAP_STARTTLS", "ldap", type="bool", label="StartTLS",
               description="upgrade a plain ldap:// connection to TLS before binding"),
    # --- Policies ---
    SettingDef("MFA_REQUIRED", "policies", type="bool",
               label="Require MFA for all users",
               description="password logins must enrol an authenticator app before "
                           "getting a session; SSO logins are governed by the IdP"),
    SettingDef("ALLOW_PRIVATE_NETWORK_TARGETS", "policies", type="bool",
               label="Allow tests against private networks",
               description="lets any user of this instance probe your internal network"),
    SettingDef("OUTBOUND_ALLOWED_HOSTS", "policies", type="list",
               label="Always-allowed hostnames"),
    SettingDef("RUN_RETENTION_DAYS", "policies", type="int",
               label="Run retention (days)", description="0 keeps everything forever"),
]}

_CACHE_TTL_SECONDS = 15.0

_lock = threading.Lock()
_cache: Dict[str, str] = {}      # key -> decrypted raw string from DB
_cache_at: float = 0.0


def _parse(defn: SettingDef, raw: str) -> Any:
    if defn.type == "bool":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if defn.type == "int":
        return int(raw.strip() or 0)
    if defn.type == "list":
        return json.loads(raw) if raw.strip() else []
    return raw


def serialize(defn: SettingDef, value: Any) -> str:
    if defn.type == "bool":
        return "true" if value in (True, "true", "True", 1, "1") else "false"
    if defn.type == "int":
        return str(int(value))
    if defn.type == "list":
        return json.dumps(value if isinstance(value, list) else [value])
    return str(value)


def env_default(key: str) -> Any:
    """The value the setting has with no DB override: pydantic Settings when
    declared there, plain env var otherwise (several LLM keys are read with
    os.getenv by the providers and never made it into Settings)."""
    if hasattr(settings, key):
        return getattr(settings, key)
    return os.getenv(key) or None


def _sync_engine():
    # Local import + lazy creation: this module is imported early and must not
    # drag sqlalchemy engine creation into import time.
    global _engine
    try:
        return _engine
    except NameError:
        from sqlalchemy import create_engine
        _engine = create_engine(
            settings.DATABASE_URL.replace("+asyncpg", ""),
            pool_pre_ping=True, pool_size=2, max_overflow=2)
        return _engine


def _load_overrides_sync() -> Dict[str, str]:
    from sqlalchemy import text
    from app.core.secrets import decrypt_secret
    out: Dict[str, str] = {}
    with _sync_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT key, value, is_secret FROM instance_settings")).fetchall()
    for key, value, is_secret in rows:
        if key not in REGISTRY:
            continue
        try:
            out[key] = decrypt_secret(value) if is_secret else value
        except Exception:
            logger.warning("instance_settings: cannot decrypt %s (SECRET_KEY rotated?); ignoring", key)
    return out


def _overrides(force: bool = False) -> Dict[str, str]:
    """Cached DB overrides; empty dict (env-only) when the DB is unavailable."""
    global _cache, _cache_at
    now = time.monotonic()
    with _lock:
        if not force and now - _cache_at < _CACHE_TTL_SECONDS:
            return _cache
    try:
        fresh = _load_overrides_sync()
    except Exception as exc:
        # Table missing (pre-migration), DB down, bootstrap: env is the truth.
        logger.debug("instance_settings: falling back to environment (%s)", exc)
        fresh = {}
    with _lock:
        _cache, _cache_at = fresh, now
    return fresh


def invalidate_cache() -> None:
    global _cache_at
    with _lock:
        _cache_at = 0.0


def effective(key: str) -> Any:
    """DB override if present, else the environment value. Cached ~15s."""
    defn = REGISTRY.get(key)
    if defn is None:
        return env_default(key)
    raw = _overrides().get(key)
    if raw is None:
        return env_default(key)
    try:
        return _parse(defn, raw)
    except Exception:
        logger.warning("instance_settings: bad stored value for %s; using environment", key)
        return env_default(key)


def effective_group(group: str) -> Dict[str, Any]:
    return {k: effective(k) for k, d in REGISTRY.items() if d.group == group}


def override_source(key: str) -> str:
    """'database' when an override row exists, else 'environment'."""
    return "database" if key in _overrides() else "environment"
