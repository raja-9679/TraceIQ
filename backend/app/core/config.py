from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic_settings import BaseSettings
from typing import Optional

# sslmode values that mean "TLS is on". Used by validate_for_deployment; the
# URL translation below preserves whatever value was given.
_SSLMODE_ON = {"require", "verify-ca", "verify-full", "prefer", "allow"}


def db_url_for(database_url: str, sync: bool) -> str:
    """Return `database_url` shaped for the driver that will consume it.

    Ten places in this codebase build an engine — one async, nine sync — and
    each previously did its own `.replace("+asyncpg", "")`. Centralising it is
    what makes TLS configurable at all: the two drivers spell the same setting
    differently, and neither tolerates the other's spelling.

    psycopg2 takes `sslmode`. SQLAlchemy's asyncpg dialect takes `ssl`, and
    rejects a `sslmode` key outright. The *values* are the same vocabulary
    (`require`, `verify-full`, `disable`, …), so translation is a rename and
    the value is preserved — coercing it to a boolean produces
    `ClientConfigurationError: sslmode parameter must be one of …`, because the
    dialect maps `ssl` straight back onto sslmode semantics.

    Operators therefore write `sslmode=` everywhere (what psql and every
    Postgres doc use) and this adapts it per driver.
    """
    if not database_url:
        return database_url

    url = database_url.replace("+asyncpg", "") if sync else database_url
    parts = urlsplit(url)
    if not parts.query:
        return url

    if sync:
        # psycopg2 speaks sslmode natively; nothing to translate.
        return url

    translated = [
        ("ssl", value) if key.lower() == "sslmode" else (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit(parts._replace(query=urlencode(translated)))


def redis_url_with_tls(redis_url: str, use_tls: bool) -> str:
    """Upgrade a `redis://` URL to `rediss://` when TLS is requested.

    redis-py and Celery both select TLS from the scheme, so this is the whole
    mechanism — but Celery additionally needs `broker_use_ssl` /
    `redis_backend_use_ssl` to control certificate verification; see
    core/celery_app.py.
    """
    if not redis_url or not use_tls:
        return redis_url
    if redis_url.startswith("rediss://"):
        return redis_url
    if redis_url.startswith("redis://"):
        return "rediss://" + redis_url[len("redis://"):]
    return redis_url


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
    # TLS to the object store. A scheme-less MINIO_ENDPOINT (what compose
    # ships) picks its scheme from this; an endpoint that spells out http://
    # or https:// wins outright.
    MINIO_USE_SSL: bool = False
    # Server-side encryption at rest: "AES256" or "aws:kms". Unset = none.
    MINIO_SSE_ALGORITHM: str = ""
    MINIO_SSE_KMS_KEY_ID: str = ""
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

    # Public URL of the frontend, used to build links in account emails
    # (password reset / email verification).
    FRONTEND_BASE_URL: str = "http://localhost:5173"
    # Lifetimes for single-use account tokens.
    PASSWORD_RESET_TOKEN_EXPIRE_HOURS: int = 2
    EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS: int = 48

    # Security
    SECRET_KEY: str
    WEBHOOK_SECRET: Optional[str] = None  # Dedicated secret for webhook/finalize endpoints; falls back to SECRET_KEY
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30  # 30 minutes

    # Set to "production" in any real deployment. Enables the startup checks in
    # validate_for_deployment() that refuse to boot on shipped-default secrets.
    ENVIRONMENT: str = "development"

    # Outbound fetch policy for user-supplied URLs (see core/net_guard.py).
    # Self-hosted deployments testing internal apps need this true; leave it
    # false on any instance where you don't trust every user, because true also
    # lets a user probe your internal network. Link-local/metadata addresses
    # (169.254.169.254) stay blocked either way.
    ALLOW_PRIVATE_NETWORK_TARGETS: bool = False
    # Password logins must enrol a TOTP authenticator before getting a session
    # (admin-editable at runtime via instance settings).
    MFA_REQUIRED: bool = False
    # SSO-only mode: password login rejected for everyone except instance
    # admins (break-glass). Admin-editable; refuses to enable without OIDC.
    PASSWORD_LOGIN_DISABLED: bool = False
    # First-boot instance admin (env-only, read once at startup). Created only
    # if the email doesn't exist; changing ADMIN_PASSWORD later does NOT
    # rewrite the stored hash.
    ADMIN_EMAIL: Optional[str] = None
    ADMIN_PASSWORD: Optional[str] = None
    # LDAP / on-prem AD login (all admin-editable via instance settings).
    LDAP_SERVER_URL: Optional[str] = None
    LDAP_BIND_DN_TEMPLATE: Optional[str] = None
    LDAP_SEARCH_BASE: Optional[str] = None
    LDAP_EMAIL_DOMAIN: Optional[str] = None
    LDAP_STARTTLS: bool = False
    LDAP_CA_CERT: Optional[str] = None
    LDAP_TLS_INSECURE: bool = False
    # Hostnames always permitted regardless of where they resolve. Useful for
    # e.g. host.docker.internal on a developer machine.
    OUTBOUND_ALLOWED_HOSTS: list[str] = []

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

    # Bearer token for GET /metrics. Prometheus cannot present a JWT, so a
    # scraper authenticates with this instead. Unset means /metrics falls back
    # to requiring an authenticated principal — it is never anonymous.
    METRICS_TOKEN: str = ""

    # Instance-wide ceiling on what any project may capture:
    # none | minimal | standard | full. Enforced at dispatch, so no project
    # setting can exceed it. See app/services/data_policy.py.
    MAX_CAPTURE_LEVEL: str = "standard"

    # Key material for secrets at rest (project secrets, MFA seeds, provider
    # API keys, stored session state). Independent of SECRET_KEY so JWT signing
    # can be rotated without destroying every stored secret. Accepts a real
    # Fernet key (from a KMS or `Fernet.generate_key()`) or a passphrase.
    # Unset falls back to SECRET_KEY, which is the legacy behaviour.
    SECRETS_KEY: str = ""
    # Comma-separated retired keys, kept readable during a rotation overlap.
    # Drop a key from here once `scripts/rotate_secrets.py` reports nothing
    # left to re-encrypt. See app/core/secrets.py.
    SECRETS_KEY_PREVIOUS: str = ""

    # Promote the transport/at-rest checks in validate_for_deployment() from
    # warnings to boot-refusing errors. Off by default so an upgrade cannot
    # brick an existing deployment; regulated deployments turn it on, and it
    # is the switch to point an auditor at.
    REQUIRE_TRANSPORT_SECURITY: bool = False

    # Certificate verification for a rediss:// broker. The scheme alone gives
    # an encrypted channel to an UNVERIFIED peer; this is what checks who is
    # on the other end. "required" | "optional" | "none".
    CELERY_REDIS_SSL_CERT_REQS: str = "required"
    CELERY_REDIS_SSL_CA_CERTS: str = ""

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

    # Security scanning (PLATFORM_VISION.md P-4).
    # Passive analysis of captured responses (item 2) — read-only, safe.
    PASSIVE_SECURITY_SCAN_ENABLED: bool = True
    # OWASP ZAP daemon for active/authenticated DAST (item 6). Scans are refused
    # when ZAP_API_URL is unset. Active (attacking) scans additionally require
    # SECURITY_ACTIVE_SCAN_ENABLED=true AND per-project opt-in + target allowlist.
    ZAP_API_URL: Optional[str] = None            # e.g. http://zap:8090
    ZAP_API_KEY: str = ""
    SECURITY_ACTIVE_SCAN_ENABLED: bool = False
    # Max seconds to wait for a ZAP scan phase before giving up.
    ZAP_SCAN_TIMEOUT_SECONDS: int = 900
    # Crawl coverage. AJAX spider crawls JS-rendered / SPA content the HTML
    # spider can't see (slower; needs the browser bundled in the ZAP image).
    ZAP_AJAX_SPIDER: bool = True
    ZAP_SPIDER_MAX_DEPTH: int = 10          # 0 = ZAP default (5)
    ZAP_SPIDER_MAX_CHILDREN: int = 0        # 0 = unlimited children per node

    # Billing (Stripe). When unset, billing runs in "manual" mode: plans exist
    # and quotas are enforced, but Stripe checkout/webhooks are disabled and
    # plans are assigned by a workspace admin.
    STRIPE_SECRET_KEY: Optional[str] = None
    STRIPE_WEBHOOK_SECRET: Optional[str] = None
    STRIPE_SUCCESS_URL: str = "http://localhost:5173/billing?success=1"
    STRIPE_CANCEL_URL: str = "http://localhost:5173/billing?canceled=1"

    # SSO (OIDC). When issuer+client are set, /api/auth/sso/* is enabled.
    OIDC_ISSUER: Optional[str] = None            # e.g. https://accounts.google.com
    OIDC_CLIENT_ID: Optional[str] = None
    OIDC_CLIENT_SECRET: Optional[str] = None
    OIDC_REDIRECT_URI: str = "http://localhost:8001/api/auth/sso/callback"
    # Where to send the browser after a successful SSO login (token in fragment).
    OIDC_POST_LOGIN_REDIRECT: str = "http://localhost:5173/login"
    OIDC_ALLOWED_EMAIL_DOMAINS: Optional[str] = None

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.OIDC_ISSUER and self.OIDC_CLIENT_ID and self.OIDC_CLIENT_SECRET)

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.strip().lower() in ("production", "prod")

    def validate_for_deployment(self) -> list[str]:
        """Refuse to run a production instance on shipped-default credentials.

        Called from the app lifespan rather than at import time so tests and
        tooling can load settings without a full production environment.

        This exists because TraceIQ is distributed as a Docker image that users
        self-host. A weak default here isn't one insecure instance — it's a
        known signing key across every deployment, which lets anyone forge a
        token against any of them. Returns a list of non-fatal warnings; raises
        RuntimeError on anything fatal.
        """
        MIN_SECRET_LEN = 32
        PLACEHOLDERS = (
            "changeme", "change-me", "secret-key", "your-secret", "yoursecret",
            "placeholder", "example", "insecure", "password", "supersecret",
            "traceiq-dev", "dev-secret", "test-secret", "minioadmin",
        )

        fatal: list[str] = []
        warnings: list[str] = []

        def check_secret(name: str, value: Optional[str], *, required: bool) -> None:
            if not value:
                if required:
                    fatal.append(f"{name} is not set.")
                return
            low = value.strip().lower()
            if len(value.strip()) < MIN_SECRET_LEN:
                fatal.append(
                    f"{name} is only {len(value.strip())} characters; "
                    f"at least {MIN_SECRET_LEN} are required. "
                    f"Generate one with: openssl rand -hex 32"
                )
            if any(p in low for p in PLACEHOLDERS):
                fatal.append(
                    f"{name} looks like a placeholder or documented default. "
                    f"Generate a real one with: openssl rand -hex 32"
                )
            if len(set(value.strip())) < 8:
                fatal.append(f"{name} has too little variety to be a real secret.")

        if self.is_production:
            check_secret("SECRET_KEY", self.SECRET_KEY, required=True)
            check_secret("WEBHOOK_SECRET", self.WEBHOOK_SECRET, required=False)

            if not self.WEBHOOK_SECRET:
                warnings.append(
                    "WEBHOOK_SECRET is unset, so worker/webhook authentication "
                    "falls back to SECRET_KEY — the same value that signs JWTs. "
                    "Set a separate WEBHOOK_SECRET so the two can be rotated "
                    "independently."
                )
            elif self.WEBHOOK_SECRET == self.SECRET_KEY:
                warnings.append(
                    "WEBHOOK_SECRET equals SECRET_KEY. Use distinct values so "
                    "rotating one does not invalidate the other."
                )

            if "*" in self.BACKEND_CORS_ORIGINS:
                fatal.append(
                    "BACKEND_CORS_ORIGINS contains \"*\", which is not a valid "
                    "production setting. List your actual frontend origins."
                )

            for cred_name in ("MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"):
                val = (getattr(self, cred_name, "") or "").strip().lower()
                if val in ("minioadmin", "minio", "admin"):
                    fatal.append(
                        f"{cred_name} is still the MinIO default. Change it "
                        f"before exposing this instance."
                    )

            db = (self.DATABASE_URL or "").lower()
            if "://user:password@" in db or ":password@" in db:
                fatal.append(
                    "DATABASE_URL still contains the default password. "
                    "Set a real POSTGRES_PASSWORD."
                )

            if self.ALLOW_PRIVATE_NETWORK_TARGETS:
                warnings.append(
                    "ALLOW_PRIVATE_NETWORK_TARGETS=true lets any user aim a test "
                    "at your internal network. Correct for a trusted "
                    "single-tenant deployment; unsafe if users are untrusted."
                )

            # --- Transport and at-rest security -------------------------
            # Everything above checks credentials. These check the channels
            # they travel over and the media they land on, which was the
            # larger gap: captured request bodies, decrypted job secrets and
            # session cookies all crossed these connections in clear.
            #
            # These are WARNINGS by default and fatal only under
            # REQUIRE_TRANSPORT_SECURITY. Making them unconditionally fatal
            # would stop every existing deployment from booting on upgrade,
            # and the escape hatch operators reach for is ENVIRONMENT=
            # development — which switches off the secret checks above too.
            # A check people route around by disabling all checks is worse
            # than no check. Regulated deployments opt in; the SELF_HOSTING
            # guide tells them to.
            strict = self.REQUIRE_TRANSPORT_SECURITY
            transport: list[str] = []

            db_query = dict(parse_qsl(urlsplit(self.DATABASE_URL or "").query))
            sslmode = (db_query.get("sslmode") or db_query.get("ssl") or "").lower()
            if sslmode in ("", "disable", "false"):
                transport.append(
                    "DATABASE_URL has no TLS (add ?sslmode=require, or "
                    "verify-full with a CA). Test results carry captured "
                    "request and response bodies, so this connection is not "
                    "metadata-only."
                )

            broker = (self.CELERY_BROKER_URL or "").lower()
            broker_tls = broker.startswith("rediss://")
            # A password appears as redis://:pw@host or redis://user:pw@host.
            broker_authed = "@" in broker.split("//", 1)[-1]
            if not broker_tls and not broker_authed:
                transport.append(
                    "CELERY_BROKER_URL is neither TLS (rediss://) nor "
                    "password-protected. Dispatched jobs carry DECRYPTED "
                    "project secrets across this connection."
                )
            elif not broker_tls:
                transport.append(
                    "Redis is password-protected but not TLS. Job payloads "
                    "containing decrypted secrets cross it in clear; prefer "
                    "rediss:// unless the link is already private."
                )

            store_endpoint = (self.MINIO_ENDPOINT or "").lower()
            if not self.MINIO_USE_SSL and not store_endpoint.startswith("https://"):
                transport.append(
                    "MinIO/object store is plaintext (set MINIO_USE_SSL=true or "
                    "an https:// endpoint). Artifacts and presigned URLs — which "
                    "are bearer credentials — cross it in clear."
                )
            if not (self.MINIO_SSE_ALGORITHM or "").strip():
                transport.append(
                    "No server-side encryption configured for the object store "
                    "(MINIO_SSE_ALGORITHM). Screenshots and logs are stored "
                    "unencrypted at rest."
                )

            if not (self.SECRETS_KEY or "").strip():
                warnings.append(
                    "SECRETS_KEY is unset, so secret encryption falls back to "
                    "SECRET_KEY. Rotating your JWT signing key would then "
                    "require re-encrypting every stored secret at the same "
                    "moment. Set SECRETS_KEY and run scripts/rotate_secrets.py."
                )

            (fatal if strict else warnings).extend(transport)

        if fatal:
            raise RuntimeError(
                "Refusing to start — insecure configuration detected:\n  - "
                + "\n  - ".join(fatal)
                + "\n\nSee infrastructure/env.community.example for guidance. "
                  "To run anyway (never in production), set ENVIRONMENT=development."
            )
        return warnings

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
