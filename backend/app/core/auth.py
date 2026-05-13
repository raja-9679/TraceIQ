import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple, Union
from jose import jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.database import get_session
from app.core.config import settings
from app.models import ApiKey, User

# Configuration
SECRET_KEY = settings.SECRET_KEY
ALGORITHM = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES

# Refresh tokens are long-lived. Default 30 days; override via settings.
REFRESH_TOKEN_EXPIRE_DAYS = getattr(settings, "REFRESH_TOKEN_EXPIRE_DAYS", 30)

# API key prefix the user-facing key starts with. Helps users spot a real key
# at a glance (similar to `sk-...`, `ghp_...`, etc.).
API_KEY_PREFIX = "tiq_"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# Bearer-token scheme used for the JWT path. `auto_error=False` lets us fall
# back to API-key auth when no Authorization header is present.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login", auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


# ---------------------------------------------------------------------------
# Refresh tokens
# ---------------------------------------------------------------------------

def generate_refresh_token() -> Tuple[str, str]:
    """Return (raw_token, hashed_token). Raw is returned to client once."""
    raw = secrets.token_urlsafe(48)
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, hashed


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------

def generate_api_key() -> Tuple[str, str, str]:
    """Return (raw_key, prefix, hashed_key).

    `prefix` is the displayable 12-char identifier shown in the UI; `hashed_key`
    is what's stored in the database.
    """
    raw_secret = secrets.token_urlsafe(32)
    raw_key = f"{API_KEY_PREFIX}{raw_secret}"
    prefix = raw_key[:12]  # e.g. "tiq_a1b2c3d4"
    hashed = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, prefix, hashed


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Auth principal — either a User (JWT) or an (ApiKey, User) pair.
# ---------------------------------------------------------------------------

class AuthPrincipal:
    """Whoever made the request. Always carries a backing User so existing
    endpoints continue to enforce per-user RBAC unchanged. When `api_key`
    is set, the call came in via service-account auth and `triggered_by`
    on derived runs should be `api_agent`.

    Phase E adds `agent_session_id`: an arbitrary UUID the agent mints
    once per "session" (one Claude Code conversation, one CI run, etc.)
    and sends on every request via `X-Agent-Session-Id`. Lets the server
    distinguish "this entity was created this session by me" from
    "this entity was created earlier by something else" — drives the
    delete-policy split (auto-delete own work, ask user otherwise).
    """

    __slots__ = ("user", "api_key", "agent_id", "agent_session_id")

    def __init__(
        self,
        user: User,
        api_key: Optional[ApiKey] = None,
        agent_id: Optional[str] = None,
        agent_session_id: Optional[str] = None,
    ) -> None:
        self.user = user
        self.api_key = api_key
        self.agent_id = agent_id
        self.agent_session_id = agent_session_id

    @property
    def is_api_caller(self) -> bool:
        return self.api_key is not None


async def _user_from_jwt(token: str, session: AsyncSession) -> Optional[User]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if not email:
            return None
    except Exception:
        return None
    result = await session.exec(select(User).where(User.email == email))
    return result.first()


async def _principal_from_api_key(
    raw_key: str, session: AsyncSession, agent_id: Optional[str]
) -> Optional[AuthPrincipal]:
    if not raw_key.startswith(API_KEY_PREFIX):
        return None
    hashed = hash_api_key(raw_key)
    result = await session.exec(select(ApiKey).where(ApiKey.hashed_key == hashed))
    key = result.first()
    if not key:
        return None
    if key.revoked_at is not None:
        return None
    if key.expires_at is not None and key.expires_at < datetime.utcnow():
        return None

    # Track usage. Best-effort; failure to update last_used_at must not block auth.
    try:
        key.last_used_at = datetime.utcnow()
        session.add(key)
        await session.commit()
    except Exception:
        await session.rollback()

    # Back the API key by the user who created it. That user's RBAC governs
    # what the key can do; deletion of the user revokes the key implicitly.
    user_result = await session.exec(select(User).where(User.id == key.created_by_id))
    user = user_result.first()
    if not user:
        return None
    return AuthPrincipal(user=user, api_key=key, agent_id=agent_id)


async def get_current_principal(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> AuthPrincipal:
    """Resolve the caller from either a JWT or an `X-API-Key` header.

    Header precedence: explicit `X-API-Key` wins over JWT so a service can
    still pass its own Authorization-bearer JWT for human-style endpoints
    while using the API key for run-triggering.
    """
    api_key_raw = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
    agent_id = request.headers.get("X-Agent-Id") or request.headers.get("x-agent-id")
    # Phase E — session id is caller-supplied so the agent can correlate
    # everything it touches in one logical run.
    agent_session_id = (
        request.headers.get("X-Agent-Session-Id")
        or request.headers.get("x-agent-session-id")
    )

    if api_key_raw:
        principal = await _principal_from_api_key(api_key_raw, session, agent_id)
        if principal:
            principal.agent_session_id = agent_session_id
            return principal
        raise HTTPException(status_code=401, detail="Invalid API key")

    if token:
        user = await _user_from_jwt(token, session)
        if user:
            return AuthPrincipal(
                user=user,
                agent_id=agent_id,
                agent_session_id=agent_session_id,
            )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    principal: AuthPrincipal = Depends(get_current_principal),
) -> User:
    """Back-compat shim. Existing endpoints that take `current_user: User`
    keep working — they receive the backing user of the principal. New
    endpoints that need to know whether the caller was an API key should
    depend on `get_current_principal` directly.
    """
    return principal.user
