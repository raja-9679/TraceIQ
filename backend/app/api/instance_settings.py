"""Admin API for instance-wide settings (the DB-over-env layer).

Instance-admin only (admin of the deployment's first tenant — see
get_current_instance_admin). Secrets are write-only: the API never returns a stored or
environment secret value, only whether one is set. Saving writes
instance_settings rows (secrets Fernet-encrypted); clearing a key deletes the
row so the environment value applies again.
"""
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
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


async def is_instance_admin(session: AsyncSession, user: User) -> bool:
    """The check behind get_current_instance_admin, without its side effects
    (no 403, no RLS bypass) — for endpoints that only branch on it."""
    if user.is_instance_admin:
        return True
    first_tenant_id = (await session.exec(
        select(func.min(Tenant.id)))).one()
    if first_tenant_id is None:
        return False
    return (await session.exec(
        select(UserSystemRole)
        .join(Role, Role.id == UserSystemRole.role_id)
        .where(
            UserSystemRole.user_id == user.id,
            UserSystemRole.tenant_id == first_tenant_id,
            Role.name == "Tenant Admin",
        ))).first() is not None


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

    The 403 text is generic on purpose: this guards much more than instance
    settings (fleet, DLQ, instance-wide access reviews and policy decisions).
    """
    if not await is_instance_admin(session, current_user):
        raise HTTPException(
            status_code=403,
            detail="Only the instance admin can do this (an admin of the first "
                   "tenant on this deployment; being a tenant admin is not enough)")
    # Operator endpoints read across tenants on purpose (fleet, DLQ, policy
    # decisions, instance-wide reviews): no database tenant scope for them.
    from app.services import rls
    await rls.bypass(session, "instance_admin")
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


async def _audit_setting(session: AsyncSession, admin: User, request: Optional[Request],
                         key: str, action: str, before: Any, after: Any) -> None:
    """Instance settings carry the security posture (MFA_REQUIRED, SSO-only,
    SCIM token, SIEM sink), so every change is an audit event — and the
    security-alert rules key off these rows. Secret values are never recorded,
    only that they changed."""
    from app.services.audit import record
    secret = insvc.REGISTRY[key].secret
    changes = {"key": key, "changed": True} if secret else {"key": key, "from": before, "to": after}
    if secret:
        changes["was_set"] = bool(before)
        changes["is_set"] = bool(after)
    await record(session, entity_type="instance_setting", entity_id=0, action=action,
                 user_id=admin.id, request=request, changes=changes)


def _same(key: str, a: Any, b: Any) -> bool:
    d = insvc.REGISTRY[key]
    try:
        return insvc.serialize(d, a) == insvc.serialize(d, b) if a is not None and b is not None else a == b
    except Exception:
        return a == b


@router.put("/admin/instance-settings", response_model=list[SettingRead])
async def update_instance_settings(
    body: SettingsUpdate,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_current_instance_admin),
    request: Request = None,
):
    unknown = [k for k in body.values if k not in insvc.REGISTRY]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown setting keys: {sorted(unknown)}")

    # Lockout guard: SSO-only mode needs a working IdP config. (Instance
    # admins are exempt from the setting either way — break-glass — but an
    # instance with no SSO and no admin flag would be bricked.)
    if body.values.get("PASSWORD_LOGIN_DISABLED") in (True, "true", "True", 1, "1"):
        from app.services import saml_auth
        oidc_ok = bool(insvc.effective("OIDC_ISSUER") and insvc.effective("OIDC_CLIENT_ID")
                       and insvc.effective("OIDC_CLIENT_SECRET"))
        if not (oidc_ok or saml_auth.is_configured()):
            raise HTTPException(
                status_code=400,
                detail="Configure and save SSO (OIDC or SAML) before disabling password login")

    def proposed(key: str):
        if key in body.values:
            v = body.values[key]
            if v is None:
                return insvc.env_default(key)
            if insvc.REGISTRY[key].secret and v == "":
                return insvc.effective(key)   # masked field left untouched
            if insvc.REGISTRY[key].type == "bool":
                return v in (True, "true", "True", 1, "1")
            return v
        return insvc.effective(key)

    # SIEM: the sink is checked the way the drain will use it, including the
    # outbound-URL guard, so a typo or a metadata-range address is refused on
    # the form instead of failing silently in a beat task.
    if any(k.startswith("SIEM_") for k in body.values):
        from app.core.net_guard import UnsafeUrlError
        from app.services import siem
        try:
            sink = siem.load_config(proposed)
            if sink is not None:
                await siem.check_destination(sink)
        except siem.SiemConfigError as exc:
            raise HTTPException(status_code=400, detail=f"SIEM: {exc}")
        except UnsafeUrlError as exc:
            raise HTTPException(status_code=400, detail=f"SIEM destination refused: {exc}")

    # SAML settings are validated the way the login path will use them — the
    # settings dict is built and, if a metadata URL is involved, fetched right
    # now — so a bad certificate or unreachable IdP surfaces on the form rather
    # than as a 503 for the first person who clicks the button.
    if any(k.startswith("SAML_") for k in body.values):
        from app.services import saml_auth

        if saml_auth.is_configured(proposed):
            try:
                cfg = saml_auth.load_config(proposed)
                await saml_auth.load_settings(cfg, force_refresh=True)
            except saml_auth.SamlConfigError as exc:
                raise HTTPException(status_code=400, detail=f"SAML: {exc}")

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

    # Test inbox: a half-configured provider would only surface as a failing
    # wait-for-email step in somebody's run, so check it while the form is open.
    if any(k.startswith("TEST_INBOX_") for k in body.values):
        from app.services import step_hooks

        def inbox_value(key: str):
            if key in body.values:
                v = body.values[key]
                if v is None:
                    return insvc.env_default(key)
                if insvc.REGISTRY[key].secret and v == "":
                    return insvc.effective(key)   # masked field left untouched
                return v
            return insvc.effective(key)

        try:
            step_hooks.validate_inbox_config(step_hooks.load_inbox_config(inbox_value))
        except step_hooks.StepHooksError as exc:
            raise HTTPException(status_code=400, detail=f"Test inbox: {exc}")

    # WP8: CIDR allowlists and worker settings. A typo in an allowlist would
    # lock out the IdP or every proxied caller, so they are parsed here; list
    # values typed as "a, b" are normalised to a JSON list.
    from app.core.client_ip import as_list, parse_cidrs
    for key in ("TRUSTED_PROXY_CIDRS", "SCIM_ALLOWED_CIDRS"):
        if body.values.get(key) is not None:
            items = as_list(body.values[key])
            try:
                parse_cidrs(items)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"{key}: {exc}")
            body.values[key] = items
    if body.values.get("EGRESS_ALLOWED_HOSTS") is not None:
        body.values["EGRESS_ALLOWED_HOSTS"] = [
            h.lower() for h in as_list(body.values["EGRESS_ALLOWED_HOSTS"])]
    subjects = body.values.get("WORKER_MTLS_ALLOWED_SUBJECTS")
    if subjects is not None and not isinstance(subjects, list):
        # Subject DNs contain commas, so a typed value is one DN per line.
        body.values["WORKER_MTLS_ALLOWED_SUBJECTS"] = [
            s.strip() for s in str(subjects).splitlines() if s.strip()]
    # Region grants ("eu=12,15") and PromQL templates both contain commas, so
    # a typed value is one entry per line, never comma-split.
    for key in ("WORKER_REGIONS", "PROMETHEUS_QUERY_TEMPLATES"):
        val = body.values.get(key)
        if val is not None and not isinstance(val, list):
            body.values[key] = [s.strip() for s in str(val).splitlines() if s.strip()]
    if body.values.get("WORKER_REGIONS") is not None:
        from app.services.worker_fleet import InvalidRegion, normalize_region
        for entry in body.values["WORKER_REGIONS"]:
            name, sep, rest = str(entry).partition("=")
            try:
                normalize_region(name)
            except InvalidRegion as exc:
                raise HTTPException(status_code=400, detail=f"WORKER_REGIONS: {exc}")
            ids = [p for p in rest.replace(",", " ").replace(";", " ").split()]
            if sep and (not ids or not all(p.isdigit() for p in ids)):
                raise HTTPException(
                    status_code=400,
                    detail=f"WORKER_REGIONS: {entry!r} — use 'region' or "
                           "'region=<workspace id>,<workspace id>'")
    for entry in body.values.get("PROMETHEUS_QUERY_TEMPLATES") or []:
        name, sep, query = str(entry).partition("=")
        if not sep or not name.strip() or not query.strip():
            raise HTTPException(status_code=400,
                                detail=f"PROMETHEUS_QUERY_TEMPLATES: {entry!r} — use 'name=PromQL'")
    provider = body.values.get("WORKER_LLM_PROVIDER")
    if provider not in (None, "") and str(provider).strip().lower() not in (
            "inherit", "anthropic", "openai", "gemini", "ollama", "openai-compatible"):
        raise HTTPException(status_code=400,
                            detail="WORKER_LLM_PROVIDER: use inherit, anthropic, openai, "
                                   "gemini, ollama or openai-compatible")
    backend_kind = body.values.get("TRACE_BACKEND")
    if backend_kind not in (None, ""):
        from app.services.trace_backends import BACKENDS
        if str(backend_kind).strip().lower() not in BACKENDS:
            raise HTTPException(status_code=400, detail="TRACE_BACKEND: use jaeger or tempo")
        body.values["TRACE_BACKEND"] = str(backend_kind).strip().lower()
    if body.values.get("TRACE_BACKEND_URL"):
        from urllib.parse import urlsplit
        from app.core.net_guard import UnsafeUrlError, validate_outbound_url
        trace_url = str(body.values["TRACE_BACKEND_URL"])
        try:
            # Same rule as PROMETHEUS_URL: the admin naming the host is its
            # private-network exemption; metadata ranges stay refused.
            await validate_outbound_url(
                trace_url, extra_allowed_hosts=[urlsplit(trace_url).hostname or ""])
        except UnsafeUrlError as exc:
            raise HTTPException(status_code=400, detail=f"TRACE_BACKEND_URL: {exc}")
    if body.values.get("PROMETHEUS_URL"):
        from urllib.parse import urlsplit
        from app.core.net_guard import UnsafeUrlError, validate_outbound_url
        prom = str(body.values["PROMETHEUS_URL"])
        try:
            # The admin naming the host is the private-network exemption (an
            # in-cluster Prometheus is the normal case); metadata/link-local
            # ranges stay refused regardless.
            await validate_outbound_url(
                prom, extra_allowed_hosts=[urlsplit(prom).hostname or ""])
        except UnsafeUrlError as exc:
            raise HTTPException(status_code=400, detail=f"PROMETHEUS_URL: {exc}")

    # Cost price table (WP12d): validated and normalised here so the report
    # never has to guess what a malformed price meant.
    if any(k.startswith("COST_") for k in body.values):
        from app.services import cost as cost_svc
        validators = {"COST_CURRENCY": cost_svc.validate_currency,
                      "COST_LLM_PRICES": cost_svc.validate_llm_prices,
                      "COST_BROWSER_MINUTE_PRICE": cost_svc.validate_minute_price}
        body.values = dict(body.values)
        for key, check in validators.items():
            if key in body.values and body.values[key] is not None:
                try:
                    body.values[key] = check(body.values[key])
                except cost_svc.PriceTableError as exc:
                    raise HTTPException(status_code=400, detail=str(exc))
                # Blank = "not set": store nothing rather than an empty override.
                if body.values[key] in (None, {}):
                    body.values[key] = None

    # Tenant isolation (WP15a): refuse a mode this database cannot enforce, and
    # switch table flags BEFORE saving, so a refused DDL leaves nothing saved.
    if "RLS_MODE" in body.values or "RLS_LOG_SAMPLE_PERCENT" in body.values:
        await _check_rls_settings(session, proposed)

    for key, value in body.values.items():
        d = insvc.REGISTRY[key]
        row = (await session.exec(
            select(InstanceSetting).where(InstanceSetting.key == key))).first()

        if value is None:
            if row:
                await _audit_setting(session, admin, request, key, "reset",
                                     insvc.effective(key), insvc.env_default(key))
                await session.delete(row)
            continue
        if d.secret and value == "":
            continue  # masked field left untouched in the UI
        before = insvc.effective(key)
        after = proposed(key)
        if d.secret or not _same(key, before, after):
            await _audit_setting(session, admin, request, key, "update", before, after)

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


async def _check_rls_settings(session: AsyncSession, proposed) -> None:
    from app.services import rls
    raw = proposed("RLS_MODE")
    mode = str(raw or "off").strip().lower()
    if mode not in rls.MODES:
        raise HTTPException(status_code=400,
                            detail=f"RLS_MODE must be one of {', '.join(rls.MODES)}")
    try:
        pct = int(proposed("RLS_LOG_SAMPLE_PERCENT") if proposed("RLS_LOG_SAMPLE_PERCENT")
                  is not None else 100)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="RLS_LOG_SAMPLE_PERCENT must be a number")
    if not 0 <= pct <= 100:
        raise HTTPException(status_code=400, detail="RLS_LOG_SAMPLE_PERCENT must be 0-100")
    if mode == "enforce":
        # Fresh, not the 5-minute cache: the operator may just have created the role.
        mech = rls._remember(await session.run_sync(lambda s: rls.detect_sync(s.connection())))
        if not mech.effective:
            raise HTTPException(status_code=400, detail=(
                f"Row-level security cannot be enforced on this database: {mech.detail}"))
    try:
        await rls.reconcile(mode)
    except Exception as exc:  # noqa: BLE001 — e.g. lock_timeout on a busy table
        raise HTTPException(status_code=503, detail=(
            f"Could not switch row-level security on the covered tables ({exc.__class__.__name__}); "
            "nothing was saved. Retry when the database is quieter."))


@router.get("/admin/rls/status")
async def rls_status(
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_current_instance_admin),
):
    """Mode, whether enforcement is actually possible on this database, the
    per-table policy state, and what log mode has counted."""
    from app.services import rls
    return await rls.status(session)


@router.post("/admin/rls/violations/reset")
async def rls_reset_violations(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_current_instance_admin),
):
    from app.services import rls
    from app.services.audit import record
    before = await rls.violation_counts()
    await rls.reset_violation_counts()
    await record(session, entity_type="instance_setting", entity_id=0,
                 action="rls_violations_reset", user_id=admin.id,
                 changes={"total_before": before.get("total", 0)}, request=request)
    await session.commit()
    return await rls.violation_counts()


@router.delete("/admin/instance-settings/{key}", response_model=SettingRead)
async def reset_instance_setting(
    key: str,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_current_instance_admin),
    request: Request = None,
):
    if key not in insvc.REGISTRY:
        raise HTTPException(status_code=404, detail="Unknown setting key")
    row = (await session.exec(
        select(InstanceSetting).where(InstanceSetting.key == key))).first()
    if row:
        await _audit_setting(session, admin, request, key, "reset",
                             insvc.effective(key), insvc.env_default(key))
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
    granter: User = Depends(get_current_instance_admin),
    request: Request = None,
):
    user = await session.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_instance_admin = True
    session.add(user)
    from app.services.audit import record
    await record(session, entity_type="user", entity_id=user.id, action="instance_admin_grant",
                 user_id=granter.id, request=request, changes={"email": user.email})
    await session.commit()
    return InstanceAdminRead(id=user.id, email=user.email, full_name=user.full_name,
                             explicit=True, via_first_tenant=False)


@router.delete("/admin/instance-admins/{user_id}", response_model=InstanceAdminRead)
async def revoke_instance_admin(
    user_id: int,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_current_instance_admin),
    request: Request = None,
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
    from app.services.audit import record
    await record(session, entity_type="user", entity_id=user.id, action="instance_admin_revoke",
                 user_id=admin.id, request=request, changes={"email": user.email})
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


# ---------------------------------------------------------------------------
# Named IdP presets (app/services/idp_catalog.py)
#
# The protocol support is generic and already complete; these two endpoints
# only help an admin fill the form. `derive` is PURE — it writes nothing, and
# the client PUTs its output to /admin/instance-settings above, so there stays
# exactly one write path with one set of validations. It also means the admin
# sees precisely what is about to be saved before committing to it.
# ---------------------------------------------------------------------------

class PresetFieldRead(BaseModel):
    name: str
    label: str
    placeholder: str = ""
    help: str = ""
    required: bool = True
    secret: bool = False


class PresetRead(BaseModel):
    id: str
    label: str
    protocol: str
    fields: list[PresetFieldRead]
    warnings: list[str] = []
    docs_url: str = ""


class DeriveRequest(BaseModel):
    answers: Dict[str, Optional[str]] = {}


class DerivedRead(BaseModel):
    values: Dict[str, str]
    warnings: list[str] = []


@router.get("/admin/idp-catalog", response_model=list[PresetRead])
async def list_idp_presets(_: User = Depends(get_current_instance_admin)):
    from app.services import idp_catalog

    return [
        PresetRead(
            id=p.id, label=p.label, protocol=p.protocol,
            fields=[PresetFieldRead(**vars(f)) for f in p.fields],
            warnings=list(p.warnings), docs_url=p.docs_url)
        for p in idp_catalog.list_presets()
    ]


@router.post("/admin/idp-catalog/{preset_id}/derive", response_model=DerivedRead)
async def derive_idp_preset(
    preset_id: str,
    body: DeriveRequest,
    _: User = Depends(get_current_instance_admin),
):
    from app.services import idp_catalog

    try:
        idp_catalog.get_preset(preset_id)
    except idp_catalog.PresetError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        derived = idp_catalog.derive(preset_id, {k: v or "" for k, v in body.answers.items()})
    except idp_catalog.PresetError as exc:
        # A missing answer is the admin's to fix, not a server fault.
        raise HTTPException(status_code=400, detail=str(exc))
    return DerivedRead(values=derived.values, warnings=derived.warnings)
