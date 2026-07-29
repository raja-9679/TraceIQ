from typing import Any
from datetime import datetime, timedelta
import secrets
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, delete
from typing import List, Optional

from app.core.database import get_session
from app.core.limiter import limiter
from app.core.auth import (
    create_access_token,
    generate_refresh_token,
    get_password_hash,
    hash_refresh_token,
    verify_password,
    get_current_user,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
)
from app.models import RefreshToken, User, UserRead, Role, Permission, RolePermission, AccountToken, MfaRecoveryCode
from app.core.auth import hash_refresh_token as _hash_token
from app.core.auth import SECRET_KEY, ALGORITHM
from app.core.config import settings as app_settings
from app.core import totp
from app.core.secrets import encrypt_secret, decrypt_secret
from jose import jwt, JWTError
from pydantic import BaseModel

router = APIRouter()


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class VerifyEmailRequest(BaseModel):
    token: str


def _new_account_token(user_id: int, purpose: str, ttl_hours: int) -> tuple[str, AccountToken]:
    """Mint a single-use account token; return (raw, record). Store hash only."""
    raw = secrets.token_urlsafe(48)
    record = AccountToken(
        user_id=user_id,
        purpose=purpose,
        hashed_token=_hash_token(raw),
        expires_at=datetime.utcnow() + timedelta(hours=ttl_hours),
    )
    return raw, record


async def _consume_account_token(raw: str, purpose: str, session: AsyncSession) -> Optional[User]:
    """Validate + single-use-consume a token; return its User or None."""
    res = await session.exec(
        select(AccountToken).where(
            AccountToken.hashed_token == _hash_token(raw),
            AccountToken.purpose == purpose,
        )
    )
    token = res.first()
    if not token or token.used_at is not None or token.expires_at < datetime.utcnow():
        return None
    token.used_at = datetime.utcnow()
    session.add(token)
    user = (await session.exec(select(User).where(User.id == token.user_id))).first()
    return user

class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str | None = None
    workspace_name: str | None = None
    organization_name: str | None = None # Preferred over workspace_name
    project_name: str | None = None
    invite_token: str | None = None

class Token(BaseModel):
    access_token: Optional[str] = None
    token_type: str = "bearer"
    refresh_token: Optional[str] = None
    refresh_token_expires_at: Optional[datetime] = None
    # MFA challenge: when a user has MFA enabled, login returns mfa_required=True
    # + a short-lived mfa_token instead of tokens; the client then calls
    # /auth/mfa/login with the TOTP code to obtain real tokens.
    mfa_required: bool = False
    mfa_token: Optional[str] = None


class MfaLoginRequest(BaseModel):
    mfa_token: str
    code: str


class MfaCodeRequest(BaseModel):
    code: str


class RefreshRequest(BaseModel):
    refresh_token: str

from app.services.rbac_service import rbac_service

class PermissionsResponse(BaseModel):
    system: List[str]
    workspace: dict
    project: dict

@router.get("/permissions", response_model=PermissionsResponse)
async def get_my_permissions(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session)
) -> PermissionsResponse:
    """
    Get effective permissions for the current user across all scopes.
    """
    perms_map = await rbac_service.get_user_permissions_map(current_user.id, session)
    return PermissionsResponse(**perms_map)

@router.post("/login", response_model=Token)
@limiter.limit("10/minute")
async def login_for_access_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session)
) -> Any:
    result = await session.exec(select(User).where(User.email == form_data.username))
    user = result.first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated",
        )

    # MFA gate: if enabled, don't issue tokens yet — return a short-lived
    # challenge the client redeems at /auth/mfa/login with a TOTP code.
    if user.mfa_enabled:
        challenge = create_access_token(
            data={"sub": user.email, "mfa_pending": True},
            expires_delta=timedelta(minutes=5))
        return {"token_type": "bearer", "mfa_required": True, "mfa_token": challenge}

    return await _issue_tokens(user, request, session)


async def _issue_tokens(user: User, request: Request, session: AsyncSession) -> dict:
    """Mint an access token + a rotating refresh token for a fully-authenticated
    user. Shared by password login, MFA login, and SSO."""
    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))

    raw_refresh, hashed_refresh = generate_refresh_token()
    refresh_expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    session.add(RefreshToken(
        user_id=user.id,
        hashed_token=hashed_refresh,
        family_id=secrets.token_urlsafe(16),
        expires_at=refresh_expires_at,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    ))
    user.last_login_at = datetime.utcnow()
    session.add(user)
    await session.commit()
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "refresh_token": raw_refresh,
        "refresh_token_expires_at": refresh_expires_at,
    }


def _normalize_code(c: str) -> str:
    return (c or "").strip().replace("-", "").replace(" ", "").lower()


async def _generate_recovery_codes(user_id: int, session: AsyncSession, n: int = 10) -> List[str]:
    """Replace a user's recovery codes with a fresh set; return the plaintext
    (shown to the user exactly once — only hashes are stored)."""
    await session.exec(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id))
    codes: List[str] = []
    for _ in range(n):
        raw = f"{secrets.token_hex(3)}-{secrets.token_hex(3)}"
        codes.append(raw)
        session.add(MfaRecoveryCode(user_id=user_id, code_hash=_hash_token(_normalize_code(raw))))
    await session.commit()
    return codes


async def _consume_recovery_code(user_id: int, code: str, session: AsyncSession) -> bool:
    rec = (await session.exec(select(MfaRecoveryCode).where(
        MfaRecoveryCode.user_id == user_id,
        MfaRecoveryCode.code_hash == _hash_token(_normalize_code(code)),
        MfaRecoveryCode.used_at == None))).first()  # noqa: E711
    if not rec:
        return False
    rec.used_at = datetime.utcnow()
    session.add(rec)
    await session.commit()
    return True


@router.post("/mfa/login", response_model=Token)
@limiter.limit("10/minute")
async def mfa_login(request: Request, body: MfaLoginRequest,
                    session: AsyncSession = Depends(get_session)):
    """Redeem an MFA challenge token + a TOTP code *or* a recovery code."""
    try:
        payload = jwt.decode(body.mfa_token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired MFA challenge")
    if not payload.get("mfa_pending") or not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid MFA challenge")
    user = (await session.exec(select(User).where(User.email == payload["sub"]))).first()
    if not user or not user.mfa_enabled or not user.mfa_secret:
        raise HTTPException(status_code=401, detail="MFA not configured")

    ok = totp.verify(decrypt_secret(user.mfa_secret), body.code)
    if not ok:
        ok = await _consume_recovery_code(user.id, body.code, session)
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid authentication or recovery code")
    return await _issue_tokens(user, request, session)


@router.post("/mfa/setup")
async def mfa_setup(current_user: User = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    """Generate a TOTP secret (not yet enforced) and return the enrollment URI."""
    secret = totp.generate_secret()
    current_user.mfa_secret = encrypt_secret(secret)
    current_user.mfa_enabled = False  # not enforced until verified
    session.add(current_user)
    await session.commit()
    return {"secret": secret,
            "otpauth_uri": totp.provisioning_uri(secret, current_user.email)}


@router.post("/mfa/verify")
async def mfa_verify(body: MfaCodeRequest, current_user: User = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
    """Confirm a code against the pending secret and turn MFA on."""
    if not current_user.mfa_secret:
        raise HTTPException(status_code=400, detail="Run /mfa/setup first")
    if not totp.verify(decrypt_secret(current_user.mfa_secret), body.code):
        raise HTTPException(status_code=400, detail="Invalid authentication code")
    current_user.mfa_enabled = True
    session.add(current_user)
    await session.commit()
    # Issue backup codes (shown once) so a lost device doesn't lock the user out.
    codes = await _generate_recovery_codes(current_user.id, session)
    return {"mfa_enabled": True, "recovery_codes": codes}


@router.post("/mfa/disable")
async def mfa_disable(body: MfaCodeRequest, current_user: User = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)):
    if current_user.mfa_enabled and current_user.mfa_secret:
        if not totp.verify(decrypt_secret(current_user.mfa_secret), body.code):
            raise HTTPException(status_code=400, detail="Invalid authentication code")
    current_user.mfa_enabled = False
    current_user.mfa_secret = None
    session.add(current_user)
    await session.exec(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == current_user.id))
    await session.commit()
    return {"mfa_enabled": False}


@router.post("/mfa/recovery-codes")
async def regenerate_recovery_codes(body: MfaCodeRequest,
                                    current_user: User = Depends(get_current_user),
                                    session: AsyncSession = Depends(get_session)):
    """Issue a fresh set of recovery codes (invalidating the old), gated by a
    current TOTP code."""
    if not current_user.mfa_enabled or not current_user.mfa_secret:
        raise HTTPException(status_code=400, detail="MFA is not enabled")
    if not totp.verify(decrypt_secret(current_user.mfa_secret), body.code):
        raise HTTPException(status_code=400, detail="Invalid authentication code")
    codes = await _generate_recovery_codes(current_user.id, session)
    return {"recovery_codes": codes}


@router.post("/refresh", response_model=Token)
@limiter.limit("30/minute")
async def refresh_access_token(
    request: Request,
    body: RefreshRequest,
    session: AsyncSession = Depends(get_session),
) -> Any:
    """Rotate a refresh token for a new access + refresh token pair.

    Rotation-on-use: every successful refresh issues a new refresh token in
    the same family and revokes the one just used. If a previously-revoked
    token is presented (token replay), every token in that family is revoked
    — assume the token was stolen.
    """
    hashed = hash_refresh_token(body.refresh_token)
    res = await session.exec(select(RefreshToken).where(RefreshToken.hashed_token == hashed))
    token = res.first()
    if not token:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    now = datetime.utcnow()

    if token.revoked_at is not None:
        # Replay detected — revoke the whole family.
        family_res = await session.exec(
            select(RefreshToken).where(RefreshToken.family_id == token.family_id)
        )
        for sibling in family_res.all():
            if sibling.revoked_at is None:
                sibling.revoked_at = now
                session.add(sibling)
        await session.commit()
        raise HTTPException(status_code=401, detail="Refresh token reuse detected; session revoked")

    if token.expires_at < now:
        raise HTTPException(status_code=401, detail="Refresh token expired")

    # Revoke the presented token and issue a successor in the same family.
    token.revoked_at = now
    token.last_used_at = now
    session.add(token)

    user_res = await session.exec(select(User).where(User.id == token.user_id))
    user = user_res.first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    new_raw, new_hashed = generate_refresh_token()
    new_expires_at = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    successor = RefreshToken(
        user_id=user.id,
        hashed_token=new_hashed,
        family_id=token.family_id,
        expires_at=new_expires_at,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    session.add(successor)

    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    await session.commit()
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "refresh_token": new_raw,
        "refresh_token_expires_at": new_expires_at,
    }


@router.post("/logout")
async def logout(
    body: RefreshRequest,
    session: AsyncSession = Depends(get_session),
):
    """Revoke a refresh token explicitly. Idempotent."""
    hashed = hash_refresh_token(body.refresh_token)
    res = await session.exec(select(RefreshToken).where(RefreshToken.hashed_token == hashed))
    token = res.first()
    if token and token.revoked_at is None:
        token.revoked_at = datetime.utcnow()
        session.add(token)
        await session.commit()
    return {"status": "ok"}

@router.post("/register", response_model=UserRead)
@limiter.limit("5/minute")
async def register_user(
    request: Request,
    user_in: UserCreate,
    session: AsyncSession = Depends(get_session)
) -> Any:
    result = await session.exec(select(User).where(User.email == user_in.email))
    existing_user = result.first()
    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="User with this email already exists"
        )
    
    # --- Branching Logic ---
    if user_in.invite_token:
        # 1. Invite Flow
        from app.models import WorkspaceInvitation, UserWorkspace
        # Validate Invite
        stmt = select(WorkspaceInvitation).where(WorkspaceInvitation.token == user_in.invite_token)
        invite = (await session.exec(stmt)).first()
        
        if not invite:
             raise HTTPException(status_code=400, detail="Invalid invitation token")
        if invite.expires_at < datetime.utcnow():
             raise HTTPException(status_code=400, detail="Invitation token expired")
        if invite.email != user_in.email:
             # Basic check, though usually token implies email matching or binding
             raise HTTPException(status_code=400, detail="Email does not match invitation")

        # Create User (No Tenant Admin)
        user = User(
            email=user_in.email,
            hashed_password=get_password_hash(user_in.password),
            full_name=user_in.full_name
        )
        session.add(user)
        await session.flush()
        
        # Link to Workspace (Role is in invite)
        # Re-use process logic but specific to this token
        # Map role
        rbac_role_name = "Workspace Member"
        if invite.role == "admin": 
            rbac_role_name = "Workspace Admin"
        
        # We need rbac_service here
        # Local import or global? 'rbac_service' is imported at module level in original file but inside Pydantic block?
        # Check original file imports. Line 33 imports rbac_service.
        
        rbac_role = await rbac_service.get_role_by_name(session, rbac_role_name)
        role_id = rbac_role.id if rbac_role else None
        
        uw = UserWorkspace(
            user_id=user.id, 
            workspace_id=invite.workspace_id, 
            role=invite.role,
            role_id=role_id
        )
        session.add(uw)
        
        # Consume Invite
        await session.delete(invite)
        
        # NEW: Link to Project if invite has project info
        if invite.project_id and invite.project_role:
             from app.models import UserProjectAccess
             # Map access_level ('admin', 'editor', 'viewer') to Role
             pa_role_name = "Project Viewer"
             if invite.project_role == "admin": pa_role_name = "Project Admin"
             elif invite.project_role == "editor": pa_role_name = "Project Editor"
             
             p_role = await rbac_service.get_role_by_name(session, pa_role_name)
             
             upa = UserProjectAccess(
                 user_id=user.id,
                 project_id=invite.project_id,
                 access_level=invite.project_role,
                 role_id=p_role.id if p_role else None
             )
             session.add(upa)
             await session.flush()
        
        # We DO NOT create a Tenant. We DO NOT assign UserSystemRole (Tenant Admin).
        # We DO NOT create a default Workspace.
        
    else:
        # 2. Standalone Flow (Tenant Creation)
        user = User(
            email=user_in.email,
            hashed_password=get_password_hash(user_in.password),
            full_name=user_in.full_name
        )
        session.add(user)
        await session.flush()
        
        # Create New Tenant
        from app.models import Tenant, UserSystemRole
        # Use organization_name if provided, otherwise workspace_name, otherwise default
        tenant_name = user_in.organization_name or user_in.workspace_name or f"{user_in.full_name or user_in.email}'s Workspace"
        tenant = Tenant(name=tenant_name, owner_id=user.id)
        session.add(tenant)
        await session.flush() # Get Tenant ID
        
        # Assign Tenant Admin Role
        ta_role = await rbac_service.get_role_by_name(session, "Tenant Admin")
        if not ta_role:
             raise HTTPException(500, "System configuration error: Tenant Admin role missing")
             
        usr = UserSystemRole(
            user_id=user.id,
            role_id=ta_role.id,
            tenant_id=tenant.id
        )
        session.add(usr)
        
        # Create Default Workspace linked to Tenant
        from app.services.workspace_service import workspace_service
        # Note: workspace_service.create_workspace now accepts tenant_id
        await workspace_service.create_workspace(
            name=tenant_name, 
            owner_id=user.id, 
            session=session, 
            commit=False, 
            auto_create_project=True,
            project_name=user_in.project_name,
            tenant_id=tenant.id
        )
        
        # Workspace Admin role is assigned inside create_workspace
        
        # Process any other pending email-based invitations (legacy / team invites)
        await workspace_service.process_pending_invitations(user.email, user.id, session)
        
    await session.commit()
    await session.refresh(user)
    return user

@router.get("/me", response_model=UserRead)
async def read_users_me(
    current_user: User = Depends(get_current_user)
) -> Any:
    return current_user


def _send_email(to_email: str, subject: str, html: str, text: str):
    """Queue an account email via Celery; fall back to inline send if broker down."""
    try:
        from app.tasks.notification_tasks import send_account_email
        send_account_email.delay(to_email, subject, html, text)
    except Exception:
        try:
            from app.tasks.notification_tasks import send_account_email
            send_account_email(to_email, subject, html, text)
        except Exception:
            pass


@router.post("/forgot-password")
@limiter.limit("5/minute")
async def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    session: AsyncSession = Depends(get_session),
):
    """Start a password reset. Always returns 200 (never leaks whether the
    email exists). If the account exists, emails a single-use reset link."""
    user = (await session.exec(select(User).where(User.email == body.email))).first()
    if user and user.is_active:
        raw, record = _new_account_token(
            user.id, "password_reset",
            getattr(app_settings, "PASSWORD_RESET_TOKEN_EXPIRE_HOURS", 2))
        session.add(record)
        await session.commit()
        link = f"{app_settings.FRONTEND_BASE_URL.rstrip('/')}/reset-password?token={raw}"
        _send_email(
            user.email,
            "Reset your TraceIQ password",
            f"<p>We received a request to reset your password.</p>"
            f"<p><a href=\"{link}\">Reset your password</a> (link expires soon).</p>"
            f"<p>If you didn't request this, you can ignore this email.</p>",
            f"Reset your password: {link}",
        )
    return {"status": "ok", "message": "If that account exists, a reset link has been sent."}


@router.post("/reset-password")
@limiter.limit("5/minute")
async def reset_password(
    request: Request,
    body: ResetPasswordRequest,
    session: AsyncSession = Depends(get_session),
):
    """Complete a password reset with a valid token. Revokes all of the user's
    refresh tokens so existing sessions can't continue with the old password."""
    if len(body.new_password or "") < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    user = await _consume_account_token(body.token, "password_reset", session)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user.hashed_password = get_password_hash(body.new_password)
    session.add(user)

    now = datetime.utcnow()
    fam = await session.exec(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)))
    for tok in fam.all():
        tok.revoked_at = now
        session.add(tok)
    await session.commit()
    return {"status": "ok", "message": "Password updated. Please log in again."}


@router.post("/request-verification")
@limiter.limit("5/minute")
async def request_email_verification(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Send (or resend) an email-verification link to the current user."""
    if current_user.is_verified:
        return {"status": "ok", "message": "Email already verified"}
    raw, record = _new_account_token(
        current_user.id, "email_verification",
        getattr(app_settings, "EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS", 48))
    session.add(record)
    await session.commit()
    link = f"{app_settings.FRONTEND_BASE_URL.rstrip('/')}/verify-email?token={raw}"
    _send_email(
        current_user.email,
        "Verify your TraceIQ email",
        f"<p>Confirm your email address.</p><p><a href=\"{link}\">Verify email</a></p>",
        f"Verify your email: {link}",
    )
    return {"status": "ok", "message": "Verification email sent."}


@router.post("/verify-email")
async def verify_email(
    body: VerifyEmailRequest,
    session: AsyncSession = Depends(get_session),
):
    """Mark the user's email verified given a valid verification token."""
    user = await _consume_account_token(body.token, "email_verification", session)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")
    user.is_verified = True
    user.email_verified_at = datetime.utcnow()
    session.add(user)
    await session.commit()
    return {"status": "ok", "message": "Email verified"}


@router.delete("/me")
async def delete_my_account(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Self-serve account deactivation (GDPR).

    Deactivates the account, scrubs PII from the email/name, and revokes all
    refresh tokens. Kept as a soft delete (not a hard row delete) because the
    user is referenced as owner/creator across tenants, suites, cases and runs;
    hard deletion would orphan or cascade those. The account can no longer log
    in and its personal data is removed.
    """
    now = datetime.utcnow()
    # Scrub PII, keep a stable non-identifying placeholder unique per user.
    current_user.email = f"deleted+{current_user.id}@deleted.traceiq.local"
    current_user.full_name = "Deleted User"
    current_user.hashed_password = get_password_hash(secrets.token_urlsafe(32))
    current_user.is_active = False
    session.add(current_user)

    fam = await session.exec(
        select(RefreshToken).where(
            RefreshToken.user_id == current_user.id, RefreshToken.revoked_at.is_(None)))
    for tok in fam.all():
        tok.revoked_at = now
        session.add(tok)
    await session.commit()
    return {"status": "ok", "message": "Account deleted"}


# ---------------------------------------------------------------------------
# SSO (OIDC). Enabled only when OIDC_* is configured. The token-exchange flow
# needs a real IdP to exercise end-to-end; URL/state construction is pure.
# ---------------------------------------------------------------------------
import httpx
from fastapi.responses import RedirectResponse
from urllib.parse import urlencode


def _oidc(key: str) -> str:
    """Effective OIDC value: admin UI (DB) override, else environment."""
    from app.services.instance_settings import effective
    return str(effective(key) or "")


def _oidc_enabled() -> bool:
    return bool(_oidc("OIDC_ISSUER") and _oidc("OIDC_CLIENT_ID") and _oidc("OIDC_CLIENT_SECRET"))


async def _oidc_discovery() -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{_oidc('OIDC_ISSUER').rstrip('/')}/.well-known/openid-configuration")
        r.raise_for_status()
        return r.json()


def build_authorize_url(cfg: dict, state: str) -> str:
    """Pure: assemble the IdP authorization URL (unit-testable)."""
    params = {
        "response_type": "code",
        "client_id": _oidc("OIDC_CLIENT_ID"),
        "redirect_uri": _oidc("OIDC_REDIRECT_URI"),
        "scope": "openid email profile",
        "state": state,
    }
    return f"{cfg['authorization_endpoint']}?{urlencode(params)}"


@router.get("/sso/status")
async def sso_status():
    enabled = _oidc_enabled()
    return {"enabled": enabled,
            "issuer": _oidc("OIDC_ISSUER") if enabled else None}


@router.get("/sso/login")
async def sso_login():
    if not _oidc_enabled():
        raise HTTPException(status_code=404, detail="SSO is not configured")
    state = create_access_token(data={"sso_state": True}, expires_delta=timedelta(minutes=10))
    cfg = await _oidc_discovery()
    return RedirectResponse(build_authorize_url(cfg, state))


@router.get("/sso/callback")
async def sso_callback(request: Request, code: str = "", state: str = "",
                       session: AsyncSession = Depends(get_session)):
    if not _oidc_enabled():
        raise HTTPException(status_code=404, detail="SSO is not configured")
    try:
        payload = jwt.decode(state, SECRET_KEY, algorithms=[ALGORITHM])
        if not payload.get("sso_state"):
            raise JWTError("bad state")
    except JWTError:
        raise HTTPException(status_code=400, detail="Invalid SSO state")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    cfg = await _oidc_discovery()
    async with httpx.AsyncClient(timeout=15) as client:
        tok = await client.post(cfg["token_endpoint"], data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": _oidc("OIDC_REDIRECT_URI"),
            "client_id": _oidc("OIDC_CLIENT_ID"),
            "client_secret": _oidc("OIDC_CLIENT_SECRET"),
        }, headers={"Accept": "application/json"})
        if tok.status_code != 200:
            raise HTTPException(status_code=401, detail=f"SSO token exchange failed: {tok.text[:200]}")
        access = tok.json().get("access_token")
        ui = await client.get(cfg["userinfo_endpoint"], headers={"Authorization": f"Bearer {access}"})
        ui.raise_for_status()
        info = ui.json()

    email = (info.get("email") or "").lower()
    if not email:
        raise HTTPException(status_code=400, detail="SSO provider did not return an email")

    user = (await session.exec(select(User).where(User.email == email))).first()
    if not user:
        user = User(email=email, full_name=info.get("name") or email.split("@")[0],
                    hashed_password=get_password_hash(secrets.token_urlsafe(32)),
                    is_active=True, is_verified=True, email_verified_at=datetime.utcnow())
        session.add(user)
        await session.commit()
        await session.refresh(user)

    tokens = await _issue_tokens(user, request, session)
    frag = urlencode({"access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"]})
    return RedirectResponse(f"{_oidc('OIDC_POST_LOGIN_REDIRECT')}#{frag}")
