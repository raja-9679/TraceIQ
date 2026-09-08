"""The admin endpoints over app/services/idp_catalog.py.

No database: the router is mounted on a bare FastAPI app and the
instance-admin dependency is overridden, because the catalog is pure data and
`derive` writes nothing. The write itself goes through the existing
PUT /admin/instance-settings, which has its own tests.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api import instance_settings as api
from app.api.instance_settings import get_current_instance_admin


def _client(*, admin: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(api.router, prefix="/api")

    def _allow():
        return object()

    def _deny():
        raise HTTPException(status_code=403, detail="Instance admin required")

    app.dependency_overrides[get_current_instance_admin] = _allow if admin else _deny
    return TestClient(app)


# --- catalog ---------------------------------------------------------------

def test_catalog_lists_presets_with_their_fields():
    r = _client().get("/api/admin/idp-catalog")
    assert r.status_code == 200
    presets = {p["id"]: p for p in r.json()}
    assert "entra-oidc" in presets
    entra = presets["entra-oidc"]
    assert entra["protocol"] == "oidc"
    assert [f["name"] for f in entra["fields"]] == [
        "base_url", "tenant_id", "client_id", "client_secret"]
    assert any(f["secret"] for f in entra["fields"])


def test_catalog_requires_an_instance_admin():
    r = _client(admin=False).get("/api/admin/idp-catalog")
    assert r.status_code == 403


# --- derive ----------------------------------------------------------------

def test_derive_returns_the_settings_the_admin_is_about_to_save():
    r = _client().post("/api/admin/idp-catalog/entra-oidc/derive", json={"answers": {
        "base_url": "https://traceiq.acme.com",
        "tenant_id": "aaaa-bbbb", "client_id": "cid", "client_secret": "sec"}})
    assert r.status_code == 200
    body = r.json()
    assert body["values"]["OIDC_ISSUER"] == (
        "https://login.microsoftonline.com/aaaa-bbbb/v2.0")
    assert body["values"]["OIDC_REDIRECT_URI"] == (
        "https://traceiq.acme.com/api/auth/sso/callback")
    assert body["warnings"], "Entra's group-object-id quirk must be surfaced"


def test_derive_requires_an_instance_admin():
    r = _client(admin=False).post("/api/admin/idp-catalog/entra-oidc/derive",
                                  json={"answers": {}})
    assert r.status_code == 403


def test_derive_rejects_an_unknown_preset_with_404():
    """The detail must name the preset, so this cannot pass merely because the
    route is absent — a missing route 404s with Starlette's "Not Found"."""
    r = _client().post("/api/admin/idp-catalog/nope/derive", json={"answers": {}})
    assert r.status_code == 404
    assert "nope" in r.json()["detail"]


def test_derive_names_the_missing_field_in_a_400():
    r = _client().post("/api/admin/idp-catalog/entra-oidc/derive", json={"answers": {
        "base_url": "https://traceiq.acme.com", "client_id": "c", "client_secret": "s"}})
    assert r.status_code == 400
    assert "tenant_id" in r.json()["detail"]


def test_derive_echoes_the_secret_so_the_ui_can_save_it():
    """The admin just typed it, the endpoint is admin-gated, and the UI has to
    PUT it to /admin/instance-settings — so it comes back deliberately."""
    r = _client().post("/api/admin/idp-catalog/okta-oidc/derive", json={"answers": {
        "base_url": "https://traceiq.acme.com", "okta_domain": "acme.okta.com",
        "client_id": "cid", "client_secret": "shh"}})
    assert r.json()["values"]["OIDC_CLIENT_SECRET"] == "shh"


def test_derived_values_are_all_keys_the_settings_endpoint_accepts():
    """PUT /admin/instance-settings 400s on unknown keys, so a derived map that
    contained one would be unsaveable."""
    from app.services.instance_settings import REGISTRY
    r = _client().post("/api/admin/idp-catalog/keycloak-oidc/derive", json={"answers": {
        "base_url": "https://traceiq.acme.com", "keycloak_url": "https://kc.acme.com",
        "realm": "employees", "client_id": "c", "client_secret": "s"}})
    assert all(k in REGISTRY for k in r.json()["values"])
