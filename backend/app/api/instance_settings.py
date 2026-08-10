"""Admin API for instance-wide settings (the DB-over-env layer).

Instance-admin only (admin of the deployment's first tenant — see
get_current_instance_admin). Secrets are write-only: the API never returns a stored or
environment secret value, only whether one is set. Saving writes
instance_settings rows (secrets Fernet-encrypted); clearing a key deletes the
row so the environment value applies again.
"""
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from sqlalchemy import func

from app.core.auth import get_current_user
from app.core.database import get_session
from app.core.secrets import encrypt_secret
from app.models import InstanceSetting, Role, Tenant, User, UserSystemRole
from app.services import instance_settings as insvc

router = APIRouter()


async def get_current_instance_admin(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> User:
    """Instance settings affect EVERY tenant, so tenant-admin is not enough:
    self-registration makes each user Tenant Admin of their own tenant.

    Two ways in: the explicit `is_instance_admin` grant (transferable via
    /admin/instance-admins, seeded by the ADMIN_EMAIL bootstrap), or the
    legacy fallback — admin of the FIRST (oldest) tenant, which on a
    self-hosted install is the first account registered, exactly what
    SELF_HOSTING.md promises.
    """
    if current_user.is_instance_admin:
        return current_user
    first_tenant_id = (await session.exec(
        select(func.min(Tenant.id)))).one()
    if first_tenant_id is None:
        raise HTTPException(status_code=403, detail="No tenants exist yet")
    is_owner_admin = (await session.exec(
        select(UserSystemRole)
        .join(Role, Role.id == UserSystemRole.role_id)
        .where(
            UserSystemRole.user_id == current_user.id,
            UserSystemRole.tenant_id == first_tenant_id,
            Role.name == "Tenant Admin",
        ))).first()
    if not is_owner_admin:
        raise HTTPException(
            status_code=403,
            detail="Instance settings can only be managed by the instance admin "
                   "(an admin of the first tenant on this deployment)")
    return current_user


class SettingRead(BaseModel):
    key: str
    group: str
    type: str
    secret: bool
    restart_required: bool
    label: str
    description: str
    source: str                     # "database" | "environment"
    value: Optional[Any] = None     # effective value; None for secrets
    env_default: Optional[Any] = None  # None for secrets
    is_set: bool = False            # secrets: an effective value exists


class SettingsUpdate(BaseModel):
    # KEY -> new value; null resets the key to its environment value.
    # For secret keys an empty string means "leave unchanged".
    values: Dict[str, Optional[Any]]


class TestEmailRequest(BaseModel):
    to: str


def _read_one(key: str) -> SettingRead:
    d = insvc.REGISTRY[key]
    eff = insvc.effective(key)
    if d.secret:
        return SettingRead(
            key=key, group=d.group, type=d.type, secret=True,
            restart_required=d.restart_required, label=d.label,
            description=d.description, source=insvc.override_source(key),
            is_set=bool(eff))
    return SettingRead(
        key=key, group=d.group, type=d.type, secret=False,
        restart_required=d.restart_required, label=d.label,
        description=d.description, source=insvc.override_source(key),
        value=eff, env_default=insvc.env_default(key), is_set=eff is not None)


@router.get("/admin/instance-settings", response_model=list[SettingRead])
async def list_instance_settings(_: User = Depends(get_current_instance_admin)):
    insvc.invalidate_cache()
    return [_read_one(k) for k in insvc.REGISTRY]


@router.put("/admin/instance-settings", response_model=list[SettingRead])
async def update_instance_settings(
    body: SettingsUpdate,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_current_instance_admin),
):
    unknown = [k for k in body.values if k not in insvc.REGISTRY]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown setting keys: {sorted(unknown)}")

    # Lockout guard: SSO-only mode needs a working IdP config. (Instance
    # admins are exempt from the setting either way — break-glass — but an
    # instance with no SSO and no admin flag would be bricked.)
    if body.values.get("PASSWORD_LOGIN_DISABLED") in (True, "true", "True", 1, "1"):
        if not (insvc.effective("OIDC_ISSUER") and insvc.effective("OIDC_CLIENT_ID")
                and insvc.effective("OIDC_CLIENT_SECRET")):
            raise HTTPException(
                status_code=400,
                detail="Configure and save SSO (OIDC) before disabling password login")

    # Federation settings fail closed at login time, so a typo here would take
    # every SSO/LDAP login down until someone noticed. Check the proposed values
    # against the same rules the login path applies, while the admin is still
    # looking at the form.
    from app.services.federation import (
        MODE_WORKSPACE, FederationConfigError, validate_proposed)
    try:
        policy = validate_proposed(body.values)
    except FederationConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if policy and policy.mode == MODE_WORKSPACE:
        from app.models import Workspace
        if await session.get(Workspace, policy.workspace_id) is None:
            raise HTTPException(
                status_code=400,
                detail=f"Workspace {policy.workspace_id} does not exist — federated "
                       "users would have nowhere to land")

    for key, value in body.values.items():
        d = insvc.REGISTRY[key]
        row = (await session.exec(
            select(InstanceSetting).where(InstanceSetting.key == key))).first()

        if value is None:
            if row:
                await session.delete(row)
            continue
        if d.secret and value == "":
            continue  # masked field left untouched in the UI

        try:
            raw = insvc.serialize(d, value)
        except Exception:
            raise HTTPException(status_code=400, detail=f"{key}: invalid value for type {d.type}")
        stored = encrypt_secret(raw) if d.secret else raw

        if row:
            row.value = stored
            row.is_secret = d.secret
            row.updated_by_id = admin.id
            row.updated_at = datetime.utcnow()
            session.add(row)
        else:
            session.add(InstanceSetting(
                key=key, value=stored, is_secret=d.secret, updated_by_id=admin.id))

    await session.commit()
    insvc.invalidate_cache()

    # If a login-hardening policy was just enabled, revoke every live refresh
    # token so existing sessions can't keep rotating past the new policy.
    # (The /refresh path deliberately can't tell which login method minted a
    # token, so enforcement has to happen here.)
    def _enabled(v):
        return v in (True, "true", "True", 1, "1")
    if _enabled(body.values.get("MFA_REQUIRED")) or _enabled(body.values.get("PASSWORD_LOGIN_DISABLED")):
        from app.models import RefreshToken
        now = datetime.utcnow()
        live = (await session.exec(
            select(RefreshToken).where(RefreshToken.revoked_at == None))).all()  # noqa: E711
        for t in live:
            t.revoked_at = now
            session.add(t)
        if live:
            await session.commit()

    return [_read_one(k) for k in insvc.REGISTRY]


@router.delete("/admin/instance-settings/{key}", response_model=SettingRead)
async def reset_instance_setting(
    key: str,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_instance_admin),
):
    if key not in insvc.REGISTRY:
        raise HTTPException(status_code=404, detail="Unknown setting key")
    row = (await session.exec(
        select(InstanceSetting).where(InstanceSetting.key == key))).first()
    if row:
        await session.delete(row)
        await session.commit()
    insvc.invalidate_cache()
    return _read_one(key)


class InstanceAdminRead(BaseModel):
    id: int
    email: str
    full_name: str
    explicit: bool      # holds the is_instance_admin grant (revocable here)
    via_first_tenant: bool  # passes the legacy first-tenant-admin fallback


@router.get("/admin/instance-admins", response_model=list[InstanceAdminRead])
async def list_instance_admins(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_instance_admin),
):
    """Everyone who passes the instance-admin guard, and why."""
    explicit = (await session.exec(
        select(User).where(User.is_instance_admin == True))).all()  # noqa: E712
    first_tenant_id = (await session.exec(select(func.min(Tenant.id)))).one()
    legacy = []
    if first_tenant_id is not None:
        legacy = (await session.exec(
            select(User)
            .join(UserSystemRole, UserSystemRole.user_id == User.id)
            .join(Role, Role.id == UserSystemRole.role_id)
            .where(UserSystemRole.tenant_id == first_tenant_id,
                   Role.name == "Tenant Admin"))).all()
    legacy_ids = {u.id for u in legacy}
    out: dict[int, InstanceAdminRead] = {}
    for u in explicit:
        out[u.id] = InstanceAdminRead(
            id=u.id, email=u.email, full_name=u.full_name,
            explicit=True, via_first_tenant=u.id in legacy_ids)
    for u in legacy:
        if u.id not in out:
            out[u.id] = InstanceAdminRead(
                id=u.id, email=u.email, full_name=u.full_name,
                explicit=False, via_first_tenant=True)
    return list(out.values())


@router.post("/admin/instance-admins/{user_id}", response_model=InstanceAdminRead)
async def grant_instance_admin(
    user_id: int,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_instance_admin),
):
    user = await session.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_instance_admin = True
    session.add(user)
    await session.commit()
    return InstanceAdminRead(id=user.id, email=user.email, full_name=user.full_name,
                             explicit=True, via_first_tenant=False)


@router.delete("/admin/instance-admins/{user_id}", response_model=InstanceAdminRead)
async def revoke_instance_admin(
    user_id: int,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_current_instance_admin),
):
    """Revokes the explicit grant only — the first-tenant fallback is not
    revocable from the API, so an instance can never talk itself out of
    having an operator."""
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot revoke your own instance-admin grant")
    user.is_instance_admin = False
    session.add(user)
    await session.commit()
    return InstanceAdminRead(id=user.id, email=user.email, full_name=user.full_name,
                             explicit=False, via_first_tenant=False)


@router.post("/admin/instance-settings/test-email")
async def send_test_email(
    body: TestEmailRequest,
    _: User = Depends(get_current_instance_admin),
):
    """Send a test message through the EFFECTIVE SMTP config (DB over env)."""
    import asyncio
    import smtplib
    from email.mime.text import MIMEText

    host = insvc.effective("SMTP_HOST")
    if not host:
        raise HTTPException(status_code=400, detail="SMTP_HOST is not configured")
    msg = MIMEText("TraceIQ test email: your SMTP settings work.")
    msg["Subject"] = "TraceIQ SMTP test"
    msg["From"] = insvc.effective("SMTP_FROM")
    msg["To"] = body.to
    port = int(insvc.effective("SMTP_PORT") or 587)
    user, password = insvc.effective("SMTP_USER"), insvc.effective("SMTP_PASSWORD")

    def _send() -> None:
        # Blocking SMTP (DNS + connect + STARTTLS + login, up to 15s each) must
        # not run on the event loop — it would freeze every other request.
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            if user and password:
                server.login(user, password)
            server.sendmail(msg["From"], [body.to], msg.as_string())

    try:
        await asyncio.to_thread(_send)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SMTP test failed: {exc}")
    return {"ok": True, "detail": f"Test email sent to {body.to}"}


@router.post("/admin/instance-settings/test-llm")
async def test_llm(_: User = Depends(get_current_instance_admin)):
    """One tiny completion through the EFFECTIVE LLM config.

    Providers swallow their own errors and return "", so an empty reply is
    reported as a failure here — this endpoint exists to surface problems.
    """
    import asyncio

    from app.ai.providers import build_default_provider, NullProvider

    provider = build_default_provider()
    if isinstance(provider, NullProvider):
        raise HTTPException(
            status_code=400,
            detail="No LLM provider configured (set a provider or an API key)")
    reply = await asyncio.to_thread(
        provider.complete, "Health check. Reply with exactly: ok", max_tokens=10)
    if not reply:
        raise HTTPException(
            status_code=502,
            detail=f"LLM test failed: provider '{provider.name}' returned no reply "
                   f"(check the API key / base URL; details are in the backend log)")
    return {"ok": True, "provider": provider.name, "reply": reply.strip()[:100]}
