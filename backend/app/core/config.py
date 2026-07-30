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
