"""Backend e2e tests: they drive a RUNNING API over HTTP (plus its database).

They used to run unconditionally against a hard-coded 127.0.0.1:8000 and
fail in every suite run without a server — four permanent errors everyone
learned to ignore, which is how real failures hide. They now skip unless
TRACEIQ_E2E_API_URL is set (e.g. http://127.0.0.1:8000/api), the same
opt-in shape as tests/integration's TRACEIQ_LIVE_DB. TraceIQ's browser E2E
suite is e2e/ at the repo root (e2e/run-e2e.sh).
"""
import os

import pytest
import asyncio
from sqlmodel import select
from app.core.database import get_session_context
from app.models import User, Workspace, Role, Project, UserProjectAccess, Tenant, UserSystemRole
from app.core.auth import get_password_hash
import httpx
import time


E2E_API_URL = os.getenv("TRACEIQ_E2E_API_URL", "")


def pytest_collection_modifyitems(config, items):
    if E2E_API_URL:
        return
    skip = pytest.mark.skip(reason="needs a running API; set TRACEIQ_E2E_API_URL")
    for item in items:
        if "/tests/e2e/" in str(item.fspath).replace(os.sep, "/"):
            item.add_marker(skip)


@pytest.fixture(scope="session")
async def api_client():
    async with httpx.AsyncClient(base_url=E2E_API_URL, timeout=30.0) as client:
        yield client

@pytest.fixture(scope="session")
async def auth_headers(api_client):
    async with get_session_context() as session:
        # 1. Setup User
        email = f"e2e_tester_{int(time.time())}@traceiq.io"
        user = User(email=email, full_name="E2E Tester", hashed_password=get_password_hash("password"))
        session.add(user)
        await session.commit()
        await session.refresh(user)
        
        # 2. Setup Tenant
        tenant = Tenant(name=f"E2E Tenant {int(time.time())}", owner_id=user.id)
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)
        
        # 3. Assign Tenant Admin Role
        ta_role = (await session.exec(select(Role).where(Role.name == "Tenant Admin"))).first()
        if not ta_role:
             ta_role = (await session.exec(select(Role))).first()
        
        if ta_role:
            usr = UserSystemRole(user_id=user.id, role_id=ta_role.id, tenant_id=tenant.id)
            session.add(usr)
            await session.commit()

        # Login
        login_data = {"username": email, "password": "password"}
        resp = await api_client.post("/auth/login", data=login_data)
        if resp.status_code != 200:
             print(f"Login Failed: {resp.status_code} {resp.text}")
        assert resp.status_code == 200
        token = resp.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

import uuid

@pytest.fixture(scope="function")
async def setup_project(api_client, auth_headers):
    # Create Workspace
    ws_name = f"E2E WS {uuid.uuid4().hex}"
    ws_resp = await api_client.post("/workspaces", json={"name": ws_name}, headers=auth_headers)
    assert ws_resp.status_code == 200
    ws_id = ws_resp.json()["id"]

    # Create Project
    proj_name = f"E2E Proj {uuid.uuid4().hex}"
    proj_resp = await api_client.post("/projects", json={"name": proj_name, "workspace_id": ws_id}, headers=auth_headers)
    assert proj_resp.status_code == 200
    project_id = proj_resp.json()["id"]

    return {"project_id": project_id, "workspace_id": ws_id}
