"""Named IdP presets: the form-filling layer over the instance-settings keys.

A preset asks the two-to-four things only the admin can know (a tenant id, an
Okta domain, a Keycloak realm) and derives everything else. Nothing here writes:
`derive` is pure, and the caller PUTs the result to /admin/instance-settings so
there stays exactly one write path with one set of validations.

The load-bearing test is `test_every_derived_key_exists_in_the_registry` — a
typo'd key would be rejected by the settings endpoint at runtime with a 400 the
admin cannot act on, so it is caught here instead.
"""
from __future__ import annotations

import pytest

from app.services import idp_catalog
from app.services.idp_catalog import PresetError, derive, get_preset, list_presets
from app.services.instance_settings import REGISTRY

BASE = "https://traceiq.example.com"


# --------------------------------------------------------------------------
# catalog shape
# --------------------------------------------------------------------------

def test_catalog_offers_the_named_providers_admins_look_for():
    ids = {p.id for p in list_presets()}
    assert {"entra-oidc", "okta-oidc", "google-oidc", "keycloak-oidc",
            "entra-saml", "okta-saml", "ldap-ad"} <= ids


def test_every_preset_declares_a_supported_protocol():
    for preset in list_presets():
        assert preset.protocol in ("oidc", "saml", "ldap"), preset.id


def test_every_preset_has_a_generic_escape_hatch_sibling():
    ids = {p.id for p in list_presets()}
    assert {"generic-oidc", "generic-saml"} <= ids


def test_unknown_preset_is_rejected():
    with pytest.raises(PresetError):
        get_preset("sign-in-with-carrier-pigeon")


def test_deriving_an_unknown_preset_is_rejected():
    with pytest.raises(PresetError):
        derive("sign-in-with-carrier-pigeon", {})


# --------------------------------------------------------------------------
# the guard that matters
# --------------------------------------------------------------------------

def test_every_derived_key_exists_in_the_registry():
    """A preset may only write keys the settings registry knows about."""
    for preset in list_presets():
        answers = {f.name: _plausible(f.name) for f in preset.fields}
        result = derive(preset.id, answers)
        unknown = sorted(k for k in result.values if k not in REGISTRY)
        assert not unknown, f"{preset.id} derives unregistered keys: {unknown}"


def test_every_derived_key_belongs_to_the_presets_own_group():
    """An OIDC preset must not write SAML or LDAP keys, and vice versa."""
    group_for = {"oidc": "sso", "saml": "saml", "ldap": "ldap"}
    for preset in list_presets():
        answers = {f.name: _plausible(f.name) for f in preset.fields}
        result = derive(preset.id, answers)
        expected = group_for[preset.protocol]
        strays = sorted(k for k in result.values
                        if REGISTRY[k].group not in (expected, "federation"))
        assert not strays, f"{preset.id} strays outside {expected}: {strays}"


def test_secret_answers_are_flagged_so_the_ui_can_mask_them():
    okta = get_preset("okta-oidc")
    secret_fields = {f.name for f in okta.fields if f.secret}
    assert "client_secret" in secret_fields


# --------------------------------------------------------------------------
# OIDC derivations
# --------------------------------------------------------------------------

def test_entra_oidc_derives_the_issuer_from_the_tenant_id():
    result = derive("entra-oidc", {"base_url": BASE, "tenant_id": "aaaa-bbbb",
                                   "client_id": "cid", "client_secret": "sec"})
    assert result.values["OIDC_ISSUER"] == "https://login.microsoftonline.com/aaaa-bbbb/v2.0"
    assert result.values["OIDC_CLIENT_ID"] == "cid"
    assert result.values["OIDC_CLIENT_SECRET"] == "sec"


def test_okta_oidc_derives_the_issuer_from_the_okta_domain():
    result = derive("okta-oidc", {"base_url": BASE, "okta_domain": "acme.okta.com",
                                  "client_id": "cid", "client_secret": "sec"})
    assert result.values["OIDC_ISSUER"] == "https://acme.okta.com/oauth2/default"


def test_okta_domain_is_accepted_with_or_without_a_scheme():
    bare = derive("okta-oidc", {"base_url": BASE, "okta_domain": "acme.okta.com",
                                "client_id": "c", "client_secret": "s"})
    schemed = derive("okta-oidc", {"base_url": BASE, "okta_domain": "https://acme.okta.com/",
                                   "client_id": "c", "client_secret": "s"})
    assert bare.values["OIDC_ISSUER"] == schemed.values["OIDC_ISSUER"]


def test_google_uses_the_fixed_google_issuer():
    result = derive("google-oidc", {"base_url": BASE, "client_id": "cid",
                                    "client_secret": "sec"})
    assert result.values["OIDC_ISSUER"] == "https://accounts.google.com"


def test_keycloak_derives_the_realm_issuer():
    result = derive("keycloak-oidc", {"base_url": BASE, "keycloak_url": "https://kc.acme.com",
                                      "realm": "employees", "client_id": "cid",
                                      "client_secret": "sec"})
    assert result.values["OIDC_ISSUER"] == "https://kc.acme.com/realms/employees"


def test_keycloak_url_trailing_slash_does_not_double_up():
    result = derive("keycloak-oidc", {"base_url": BASE, "keycloak_url": "https://kc.acme.com/",
                                      "realm": "employees", "client_id": "c",
                                      "client_secret": "s"})
    assert result.values["OIDC_ISSUER"] == "https://kc.acme.com/realms/employees"


def test_generic_oidc_passes_the_issuer_through_untouched():
    result = derive("generic-oidc", {"base_url": BASE, "issuer": "https://idp.acme.com/op",
                                     "client_id": "cid", "client_secret": "sec"})
    assert result.values["OIDC_ISSUER"] == "https://idp.acme.com/op"


def test_oidc_presets_derive_the_three_urls_admins_get_wrong():
    result = derive("entra-oidc", {"base_url": BASE, "tenant_id": "t",
                                   "client_id": "c", "client_secret": "s"})
    assert result.values["OIDC_REDIRECT_URI"] == f"{BASE}/api/auth/sso/callback"
    assert result.values["OIDC_POST_LOGIN_REDIRECT"] == f"{BASE}/login"


def test_base_url_trailing_slash_does_not_double_up_in_derived_urls():
    result = derive("entra-oidc", {"base_url": BASE + "/", "tenant_id": "t",
                                   "client_id": "c", "client_secret": "s"})
    assert result.values["OIDC_REDIRECT_URI"] == f"{BASE}/api/auth/sso/callback"


# --------------------------------------------------------------------------
# SAML derivations
# --------------------------------------------------------------------------

def test_entra_saml_derives_the_metadata_url_including_the_appid():
    result = derive("entra-saml", {"base_url": BASE, "tenant_id": "aaaa-bbbb",
                                   "app_id": "cccc-dddd"})
    assert result.values["SAML_IDP_METADATA_URL"] == (
        "https://login.microsoftonline.com/aaaa-bbbb/federationmetadata/"
        "2007-06/federationmetadata.xml?appid=cccc-dddd")


def test_entra_saml_prefills_the_entra_claim_uris():
    result = derive("entra-saml", {"base_url": BASE, "tenant_id": "t", "app_id": "a"})
    assert result.values["SAML_ATTR_EMAIL"] == (
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress")
    assert result.values["SAML_ATTR_GROUPS"] == (
        "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups")


def test_adfs_derives_the_conventional_federation_metadata_path():
    result = derive("adfs-saml", {"base_url": BASE, "adfs_host": "adfs.acme.com"})
    assert result.values["SAML_IDP_METADATA_URL"] == (
        "https://adfs.acme.com/FederationMetadata/2007-06/FederationMetadata.xml")


def test_saml_presets_derive_the_acs_url():
    result = derive("okta-saml", {"base_url": BASE,
                                  "metadata_url": "https://acme.okta.com/app/x/sso/saml/metadata"})
    assert result.values["SAML_SP_ACS_URL"] == f"{BASE}/api/auth/saml/acs"


# --------------------------------------------------------------------------
# LDAP derivations
# --------------------------------------------------------------------------

def test_active_directory_derives_the_upn_bind_template():
    result = derive("ldap-ad", {"server_url": "ldaps://dc01.acme.com",
                                "ad_domain": "acme.com"})
    assert result.values["LDAP_BIND_DN_TEMPLATE"] == "{username}@acme.com"
    assert result.values["LDAP_EMAIL_DOMAIN"] == "acme.com"


def test_openldap_derives_a_uid_bind_template_from_the_search_base():
    result = derive("ldap-openldap", {"server_url": "ldaps://ldap.acme.com",
                                      "search_base": "ou=people,dc=acme,dc=com",
                                      "email_domain": "acme.com"})
    assert result.values["LDAP_BIND_DN_TEMPLATE"] == "uid={username},ou=people,dc=acme,dc=com"


# --------------------------------------------------------------------------
# required answers, and the quirks the warnings exist for
# --------------------------------------------------------------------------

def test_a_missing_required_answer_names_the_field():
    with pytest.raises(PresetError) as exc:
        derive("entra-oidc", {"base_url": BASE, "client_id": "c", "client_secret": "s"})
    assert "tenant_id" in str(exc.value)


def test_a_blank_required_answer_is_treated_as_missing():
    with pytest.raises(PresetError):
        derive("entra-oidc", {"base_url": BASE, "tenant_id": "   ",
                              "client_id": "c", "client_secret": "s"})


def test_google_warns_that_it_emits_no_groups():
    result = derive("google-oidc", {"base_url": BASE, "client_id": "c", "client_secret": "s"})
    assert any("group" in w.lower() for w in result.warnings)


def test_entra_warns_that_groups_arrive_as_object_ids():
    result = derive("entra-oidc", {"base_url": BASE, "tenant_id": "t",
                                   "client_id": "c", "client_secret": "s"})
    assert any("object id" in w.lower() for w in result.warnings)


def test_keycloak_warns_that_the_groups_claim_needs_a_mapper():
    result = derive("keycloak-oidc", {"base_url": BASE, "keycloak_url": "https://kc",
                                      "realm": "r", "client_id": "c", "client_secret": "s"})
    assert any("mapper" in w.lower() for w in result.warnings)


# --------------------------------------------------------------------------

def _plausible(field_name: str) -> str:
    """A syntactically valid answer for any field, for the sweep tests."""
    return {
        "base_url": BASE,
        "issuer": "https://idp.example.com",
        "keycloak_url": "https://kc.example.com",
        "server_url": "ldaps://dc01.example.com",
        "metadata_url": "https://idp.example.com/metadata",
        "adfs_host": "adfs.example.com",
        "okta_domain": "example.okta.com",
        "auth0_domain": "example.eu.auth0.com",
        "search_base": "ou=people,dc=example,dc=com",
        "ad_domain": "example.com",
        "email_domain": "example.com",
    }.get(field_name, "value")
