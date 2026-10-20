"""Live-suite harness for running EVERYTHING under row-level security (WP15a).

    TRACEIQ_RLS_MODE=enforce ./run-tests-live.sh -q

In production the tenant scope is bound by `get_current_principal`. These
tests call endpoint functions directly with a session and a user, so that
dependency never runs and RLS_MODE alone would change nothing. With RLS_MODE
set to log/enforce in the environment, this conftest wraps every endpoint
function in `app.api.*` that takes `Depends(get_session)` plus one of the auth
dependencies: before the body runs it binds the scope for the user/principal
it was given — exactly what the dependency would have done for that request —
and restores the previous state afterwards, so test setup code in between
runs unscoped like any script. Endpoints behind `get_current_instance_admin`
get the bypass, as in production.

Without RLS_MODE in the environment this file does nothing.
"""
from __future__ import annotations

import functools
import importlib
import inspect
import os
import pkgutil

_MODE = (os.getenv("RLS_MODE") or "").strip().lower()


def _wrap_endpoints() -> int:
    from fastapi.params import Depends as DependsParam

    import app.api
    from app.api.admin import get_current_tenant_admin
    from app.api.instance_settings import get_current_instance_admin
    from app.core.auth import AuthPrincipal, get_current_principal, get_current_user
    from app.core.database import get_session
    from app.services import rls
    from sqlalchemy.ext.asyncio import AsyncSession

    rls.install()
    kinds = {get_current_principal: "scope", get_current_user: "scope",
             get_current_tenant_admin: "scope", get_current_instance_admin: "bypass"}

    def wrap(fn, sig, session_name, auth_name, kind):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                bound = sig.bind_partial(*args, **kwargs).arguments
            except TypeError:
                return await fn(*args, **kwargs)
            session, who = bound.get(session_name), bound.get(auth_name)
            # Real sessions only: unit tests hand these functions mocks.
            if (not isinstance(session, AsyncSession) or who is None
                    or isinstance(who, DependsParam)):
                return await fn(*args, **kwargs)
            previous = rls.get_scope(session)
            if kind == "bypass":
                await rls.bypass(session, "instance_admin")
            else:
                principal = who if hasattr(who, "user") else AuthPrincipal(user=who)
                await rls.bind_principal(session, principal)
            try:
                return await fn(*args, **kwargs)
            finally:
                try:
                    await rls._set_scope(session, previous, was_enforcing=True)
                except Exception:  # noqa: BLE001 — a failed transaction; rollback resets it
                    if previous is None:
                        session.info.pop(rls._INFO_KEY, None)
                    else:
                        session.info[rls._INFO_KEY] = previous
        return wrapper

    count = 0
    for info in pkgutil.walk_packages(app.api.__path__, "app.api."):
        try:
            mod = importlib.import_module(info.name)
        except Exception:  # noqa: BLE001
            continue
        router = getattr(mod, "router", None)
        for route in getattr(router, "routes", []) or []:
            fn = getattr(route, "endpoint", None)
            if fn is None or not inspect.iscoroutinefunction(fn):
                continue
            if getattr(mod, fn.__name__, None) is not fn:
                continue
            sig = inspect.signature(fn)
            session_name = auth_name = kind = None
            for name, param in sig.parameters.items():
                dep = getattr(param.default, "dependency", None)
                if dep is get_session:
                    session_name = name
                elif dep in kinds:
                    auth_name, kind = name, kinds[dep]
            if session_name and auth_name:
                setattr(mod, fn.__name__, wrap(fn, sig, session_name, auth_name, kind))
                count += 1
    return count


if _MODE in ("log", "enforce"):
    WRAPPED = _wrap_endpoints()
    print(f"[rls] RLS_MODE={_MODE}: {WRAPPED} endpoint functions scope their session")
