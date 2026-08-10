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

from app.core.config import db_url_for, settings

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
    SettingDef("MINIO_ACCESS_KEY", "storage", secret=True, restart_required=True,
               label="Access key"),
    SettingDef("MINIO_SECRET_KEY", "storage", secret=True, restart_required=True,
               label="Secret key"),
    SettingDef("MINIO_BUCKET_NAME", "storage", restart_required=True,
               label="Bucket"),
    SettingDef("MINIO_USE_SSL", "storage", type="bool", restart_required=True,
               label="Use TLS to the object store",
               description="picks the scheme for a scheme-less endpoint; an endpoint "
                           "that spells out http:// or https:// wins"),
    SettingDef("MINIO_SSE_ALGORITHM", "storage", restart_required=True,
               label="Server-side encryption",
               description="AES256 or aws:kms. Blank disables it. Applies to every "
                           "upload and to baseline promotion (a server-side copy does "
                           "not inherit the source object's encryption)"),
    SettingDef("MINIO_SSE_KMS_KEY_ID", "storage", secret=True, restart_required=True,
               label="SSE-KMS key id",
               description="only used with aws:kms; blank uses the backend's default key"),
    # --- SSO (OIDC) ---
    SettingDef("OIDC_ISSUER", "sso", label="Issuer URL"),
    SettingDef("OIDC_CLIENT_ID", "sso", label="Client ID"),
    SettingDef("OIDC_CLIENT_SECRET", "sso", secret=True, label="Client secret"),
    SettingDef("OIDC_REDIRECT_URI", "sso", label="Redirect URI"),
    SettingDef("OIDC_POST_LOGIN_REDIRECT", "sso", label="Post-login redirect"),
    SettingDef("OIDC_ALLOWED_EMAIL_DOMAINS", "sso", label="Allowed email domains (optional)",
               description="comma-separated list, e.g. corp.example.com,eu.corp.example.com — "
                           "when set, only these email domains may sign in / JIT-provision via "
                           "SSO. Leave blank ONLY if the IdP is single-tenant to your org"),
    SettingDef("PASSWORD_LOGIN_DISABLED", "sso", type="bool",
               label="Disable password login (SSO only)",
               description="requires a saved OIDC config; instance admins can still "
                           "use password login as break-glass so you cannot lock yourself out"),
    SettingDef("OIDC_GROUPS_CLAIM", "sso", label="Groups claim (optional)",
               description="userinfo claim carrying the user's IdP groups — 'groups' "
                           "for Okta/Keycloak, 'roles' for some Entra setups. Only "
                           "needed if you map groups to TraceIQ roles or teams"),
    # --- Federated provisioning (shared by SSO and LDAP) ---
    SettingDef("FEDERATED_PROVISIONING_MODE", "federation",
               label="Where federated users land",
               description="standalone = every SSO/LDAP user gets their own tenant "
                           "(the legacy default — wrong for an organisation, it makes "
                           "one isolated tenant per employee); workspace = they join "
                           "the workspace below; deny = no just-in-time provisioning, "
                           "only invited or SCIM-provisioned accounts may sign in"),
    SettingDef("FEDERATED_WORKSPACE_ID", "federation", type="int",
               label="Target workspace id",
               description="required in 'workspace' mode; its tenant is derived from "
                           "the workspace, so the two can never disagree"),
    SettingDef("FEDERATED_DEFAULT_ROLE", "federation", label="Default workspace role",
               description="'Workspace Member' (default) or 'Workspace Admin'. Members "
                           "get no project access by design — grant it via teams, which "
                           "is what the group→team map below is for"),
    SettingDef("FEDERATED_GROUP_ROLE_MAP", "federation", label="IdP group → role",
               description="comma-separated 'group=Role' pairs, e.g. "
                           "traceiq-admins=Workspace Admin,qa=Workspace Member. "
                           "Re-evaluated on EVERY login, so removing a group in the "
                           "IdP removes the role here. Only 'Workspace Admin' and "
                           "'Workspace Member' are accepted — a directory group "
                           "cannot grant tenant administration"),
    SettingDef("FEDERATED_GROUP_TEAM_MAP", "federation", label="IdP group → team",
               description="comma-separated 'group=Team name' pairs. Teams carry "
                           "project access, so this is how federated users get to "
                           "see projects. Teams must already exist in the target "
                           "workspace. Also re-evaluated on every login"),
    SettingDef("SCIM_TOKEN", "federation", secret=True,
               label="SCIM bearer token",
               description="paste this into Okta/Entra as the provisioning API "
                           "token. Blank disables the /scim/v2 endpoints entirely "
                           "(they answer 404). SCIM provisions into the target "
                           "workspace above and needs it set. Deactivating a user "
                           "via SCIM also revokes their live refresh tokens"),
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
    SettingDef("LDAP_CA_CERT", "ldap", label="CA certificate path (optional)",
               description="path to a PEM CA bundle used to validate the directory's "
                           "TLS certificate (ldaps:// or StartTLS)"),
    SettingDef("LDAP_TLS_INSECURE", "ldap", type="bool", label="Disable TLS validation (unsafe)",
               description="accept any TLS certificate from the directory — leaves the "
                           "connection open to on-path password capture; leave OFF"),
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
    SettingDef("AUDIT_RETENTION_DAYS", "policies", type="int",
               label="Audit-log retention (days)",
               description="0 keeps audit history forever, which is the safe default "
                           "for a compliance record. Separate from run retention on "
                           "purpose: PCI DSS Req 10 wants a year of audit history "
                           "regardless of how long you keep test artifacts"),
    SettingDef("MAX_CAPTURE_LEVEL", "policies", type="str",
               label="Maximum capture level",
               description="ceiling no project may exceed: none | minimal | standard | "
                           "full. 'standard' allows masked screenshots and scrubbed "
                           "logs; 'full' additionally allows video, Playwright traces "
                           "and HAR, none of which can be redacted. Set this to "
                           "'minimal' or lower for deployments near regulated data — "
                           "it is enforced at dispatch, so no project setting can "
                           "override it"),
]}

_CACHE_TTL_SECONDS = 15.0

_lock = threading.Lock()
_cache: Dict[str, str] = {}      # key -> decrypted raw string from DB
_cache_at: float = 0.0
_cache_loaded_ok: bool = False   # True once the DB has been read successfully once


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
    with _lock:
        try:
            return _engine
        except NameError:
            from sqlalchemy import create_engine
            _engine = create_engine(
                db_url_for(settings.DATABASE_URL, sync=True),
                pool_pre_ping=True, pool_size=2, max_overflow=2,
                # Bound the blocking refresh: effective() is called from async
                # request handlers, and without a connect timeout a slow/down DB
                # would block the whole event loop for the full libpq default.
                connect_args={"connect_timeout": 2})
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
            # Falling back to the env value is deliberate — a stuck admin
            # setting must not take the instance down. But it IS silent
            # degradation: SMTP, OIDC and LLM credentials revert to whatever
            # the environment says. The usual cause is key material missing
            # from the ring; put it back in SECRETS_KEY_PREVIOUS and run
            # scripts/rotate_secrets.py.
            logger.warning(
                "instance_settings: cannot decrypt %s — falling back to the env value. "
                "Is the key that wrote it still in SECRETS_KEY / SECRETS_KEY_PREVIOUS?",
                key)
    return out


def _overrides(force: bool = False) -> Dict[str, str]:
    """Cached DB overrides.

    On a refresh failure we keep serving the LAST-KNOWN-GOOD snapshot (stale)
    rather than discarding it for an empty dict. Discarding it would silently
    revert security policies (MFA_REQUIRED / PASSWORD_LOGIN_DISABLED) and the
    active LLM provider to their env defaults during a brief DB blip — i.e.
    fail *open*. Env-only is used only until the very first successful load
    (bootstrap / pre-migration), which is the one case where there is no
    good snapshot to preserve.
    """
    global _cache, _cache_at, _cache_loaded_ok
    now = time.monotonic()
    with _lock:
        if not force and now - _cache_at < _CACHE_TTL_SECONDS:
            return _cache
    try:
        fresh = _load_overrides_sync()
    except Exception as exc:
        with _lock:
            if _cache_loaded_ok:
                # Serve stale; bump the timestamp so we don't hammer a down DB
                # every call for the whole TTL window.
                _cache_at = now
                logger.warning("instance_settings: refresh failed, serving cached values (%s)", exc)
                return _cache
        # Never loaded successfully yet — env is the truth (bootstrap/pre-migration).
        logger.debug("instance_settings: falling back to environment (%s)", exc)
        return {}
    with _lock:
        _cache, _cache_at, _cache_loaded_ok = fresh, now, True
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
