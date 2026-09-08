"""Named identity-provider presets over the instance-settings keys.

The protocol support (OIDC, SAML 2.0, LDAP) is generic and already complete —
see `app/api/auth.py`, `app/services/saml_auth.py`, `app/services/ldap_auth.py`.
What this module adds is the part an admin cannot be expected to know: that
Entra's issuer is `https://login.microsoftonline.com/{tenant}/v2.0`, that
Keycloak's is `{base}/realms/{realm}`, that Entra's SAML metadata URL needs an
`?appid=`, or that Active Directory binds on a UPN while OpenLDAP binds on a DN.

A preset asks two-to-four questions and derives the rest. It is a
**form-filling layer, not a new integration**: everything it produces is a key
already in `instance_settings.REGISTRY`, so all the existing validation,
save-time metadata fetching and secret encryption apply unchanged.

`derive()` is pure and writes nothing. Callers PUT its output to
/admin/instance-settings, which keeps exactly one write path with one set of
validations — and lets the UI show the admin precisely what is about to be
saved before they commit to it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Tuple

ENTRA_CLAIM_EMAIL = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"
ENTRA_CLAIM_NAME = "http://schemas.microsoft.com/identity/claims/displayname"
ENTRA_CLAIM_GROUPS = "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups"


class PresetError(ValueError):
    """Unknown preset, or a required answer that is missing or blank."""


@dataclass(frozen=True)
class PresetField:
    name: str
    label: str
    placeholder: str = ""
    help: str = ""
    required: bool = True
    secret: bool = False


@dataclass(frozen=True)
class IdpPreset:
    id: str
    label: str
    protocol: str                       # oidc | saml | ldap
    fields: Tuple[PresetField, ...]
    warnings: Tuple[str, ...] = ()
    docs_url: str = ""


@dataclass
class DerivedConfig:
    values: Dict[str, str]
    warnings: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# shared fields
# --------------------------------------------------------------------------

_BASE_URL = PresetField(
    "base_url", "TraceIQ base URL",
    placeholder="https://traceiq.acme.com",
    help="Where users reach this instance. The redirect, post-login and "
         "assertion-consumer URLs are derived from it, so it must be the URL "
         "the browser actually uses.")
_CLIENT_ID = PresetField("client_id", "Client ID", placeholder="the app's client id")
_CLIENT_SECRET = PresetField("client_secret", "Client secret", secret=True)


def _clean(answers: Dict[str, str], name: str) -> str:
    return str(answers.get(name) or "").strip()


def _require(answers: Dict[str, str], name: str) -> str:
    value = _clean(answers, name)
    if not value:
        raise PresetError(f"{name} is required")
    return value


def _base(value: str) -> str:
    """An absolute URL with no trailing slash."""
    return value.rstrip("/")


def _host(value: str) -> str:
    """A bare hostname: tolerate a pasted scheme and a trailing slash."""
    stripped = value.strip()
    for scheme in ("https://", "http://"):
        if stripped.lower().startswith(scheme):
            stripped = stripped[len(scheme):]
    return stripped.rstrip("/")


def _oidc_urls(answers: Dict[str, str]) -> Dict[str, str]:
    base = _base(_require(answers, "base_url"))
    return {
        "OIDC_REDIRECT_URI": f"{base}/api/auth/sso/callback",
        "OIDC_POST_LOGIN_REDIRECT": f"{base}/login",
    }


def _saml_urls(answers: Dict[str, str]) -> Dict[str, str]:
    base = _base(_require(answers, "base_url"))
    return {
        "SAML_SP_ACS_URL": f"{base}/api/auth/saml/acs",
        "SAML_SP_ENTITY_ID": f"{base}/api/auth/saml/metadata",
        "SAML_POST_LOGIN_REDIRECT": f"{base}/login",
    }


def _oidc_client(answers: Dict[str, str]) -> Dict[str, str]:
    return {
        "OIDC_CLIENT_ID": _require(answers, "client_id"),
        "OIDC_CLIENT_SECRET": _require(answers, "client_secret"),
    }


# --------------------------------------------------------------------------
# derivations
# --------------------------------------------------------------------------

def _entra_oidc(answers: Dict[str, str]) -> Dict[str, str]:
    tenant = _require(answers, "tenant_id")
    return {
        "OIDC_ISSUER": f"https://login.microsoftonline.com/{tenant}/v2.0",
        "OIDC_GROUPS_CLAIM": "groups",
        **_oidc_client(answers), **_oidc_urls(answers),
    }


def _okta_oidc(answers: Dict[str, str]) -> Dict[str, str]:
    return {
        "OIDC_ISSUER": f"https://{_host(_require(answers, 'okta_domain'))}/oauth2/default",
        "OIDC_GROUPS_CLAIM": "groups",
        **_oidc_client(answers), **_oidc_urls(answers),
    }


def _google_oidc(answers: Dict[str, str]) -> Dict[str, str]:
    return {
        "OIDC_ISSUER": "https://accounts.google.com",
        **_oidc_client(answers), **_oidc_urls(answers),
    }


def _keycloak_oidc(answers: Dict[str, str]) -> Dict[str, str]:
    kc = _base(_require(answers, "keycloak_url"))
    realm = _require(answers, "realm")
    return {
        "OIDC_ISSUER": f"{kc}/realms/{realm}",
        "OIDC_GROUPS_CLAIM": "groups",
        **_oidc_client(answers), **_oidc_urls(answers),
    }


def _auth0_oidc(answers: Dict[str, str]) -> Dict[str, str]:
    return {
        "OIDC_ISSUER": f"https://{_host(_require(answers, 'auth0_domain'))}/",
        **_oidc_client(answers), **_oidc_urls(answers),
    }


def _generic_oidc(answers: Dict[str, str]) -> Dict[str, str]:
    values = {
        "OIDC_ISSUER": _base(_require(answers, "issuer")),
        **_oidc_client(answers), **_oidc_urls(answers),
    }
    groups_claim = _clean(answers, "groups_claim")
    if groups_claim:
        values["OIDC_GROUPS_CLAIM"] = groups_claim
    return values


def _entra_saml(answers: Dict[str, str]) -> Dict[str, str]:
    tenant = _require(answers, "tenant_id")
    app_id = _require(answers, "app_id")
    return {
        "SAML_IDP_METADATA_URL": (
            f"https://login.microsoftonline.com/{tenant}/federationmetadata/"
            f"2007-06/federationmetadata.xml?appid={app_id}"),
        "SAML_ATTR_EMAIL": ENTRA_CLAIM_EMAIL,
        "SAML_ATTR_NAME": ENTRA_CLAIM_NAME,
        "SAML_ATTR_GROUPS": ENTRA_CLAIM_GROUPS,
        **_saml_urls(answers),
    }


def _okta_saml(answers: Dict[str, str]) -> Dict[str, str]:
    return {
        "SAML_IDP_METADATA_URL": _require(answers, "metadata_url"),
        **_saml_urls(answers),
    }


def _adfs_saml(answers: Dict[str, str]) -> Dict[str, str]:
    host = _host(_require(answers, "adfs_host"))
    return {
        "SAML_IDP_METADATA_URL": (
            f"https://{host}/FederationMetadata/2007-06/FederationMetadata.xml"),
        "SAML_ATTR_EMAIL": ENTRA_CLAIM_EMAIL,
        **_saml_urls(answers),
    }


def _generic_saml(answers: Dict[str, str]) -> Dict[str, str]:
    return {
        "SAML_IDP_METADATA_URL": _require(answers, "metadata_url"),
        **_saml_urls(answers),
    }


def _ldap_ad(answers: Dict[str, str]) -> Dict[str, str]:
    domain = _require(answers, "ad_domain")
    return {
        "LDAP_SERVER_URL": _require(answers, "server_url"),
        "LDAP_BIND_DN_TEMPLATE": "{username}@" + domain,
        "LDAP_EMAIL_DOMAIN": domain,
    }


def _ldap_openldap(answers: Dict[str, str]) -> Dict[str, str]:
    search_base = _require(answers, "search_base")
    return {
        "LDAP_SERVER_URL": _require(answers, "server_url"),
        "LDAP_BIND_DN_TEMPLATE": "uid={username}," + search_base,
        "LDAP_SEARCH_BASE": search_base,
        "LDAP_EMAIL_DOMAIN": _require(answers, "email_domain"),
    }


# --------------------------------------------------------------------------
# the catalog
# --------------------------------------------------------------------------

_ENTRA_GROUPS_WARNING = (
    "Entra emits group OBJECT IDs, not names. If you map groups to roles or "
    "teams, the left-hand side must be the object id (a GUID) — group display "
    "names will silently match nothing.")

_PRESETS: Tuple[IdpPreset, ...] = (
    IdpPreset(
        "entra-oidc", "Microsoft Entra ID (OIDC)", "oidc",
        fields=(_BASE_URL,
                PresetField("tenant_id", "Directory (tenant) ID",
                            placeholder="00000000-0000-0000-0000-000000000000",
                            help="Entra admin center → Overview → Directory (tenant) ID"),
                _CLIENT_ID, _CLIENT_SECRET),
        warnings=(_ENTRA_GROUPS_WARNING,),
        docs_url="docs/ENTERPRISE_AUTH.md",
    ),
    IdpPreset(
        "okta-oidc", "Okta (OIDC)", "oidc",
        fields=(_BASE_URL,
                PresetField("okta_domain", "Okta domain",
                            placeholder="acme.okta.com"),
                _CLIENT_ID, _CLIENT_SECRET),
        warnings=("This uses Okta's built-in `default` authorization server. If "
                  "your org uses a custom one, edit OIDC_ISSUER under Advanced "
                  "to point at it.",),
    ),
    IdpPreset(
        "google-oidc", "Google Workspace (OIDC)", "oidc",
        fields=(_BASE_URL, _CLIENT_ID, _CLIENT_SECRET),
        warnings=("Google does not emit a groups claim in the ID token, so "
                  "group-to-role and group-to-team mapping will not work with "
                  "this provider. Assign roles in TraceIQ instead.",),
    ),
    IdpPreset(
        "keycloak-oidc", "Keycloak (OIDC)", "oidc",
        fields=(_BASE_URL,
                PresetField("keycloak_url", "Keycloak base URL",
                            placeholder="https://keycloak.acme.com"),
                PresetField("realm", "Realm", placeholder="employees"),
                _CLIENT_ID, _CLIENT_SECRET),
        warnings=("Keycloak only sends a `groups` claim if the client has a "
                  "group-membership mapper. Add one (Client → Client scopes → "
                  "dedicated → Add mapper → Group Membership, 'Full group path' "
                  "off) or no groups will arrive.",),
    ),
    IdpPreset(
        "auth0-oidc", "Auth0 (OIDC)", "oidc",
        fields=(_BASE_URL,
                PresetField("auth0_domain", "Auth0 domain",
                            placeholder="acme.eu.auth0.com"),
                _CLIENT_ID, _CLIENT_SECRET),
        warnings=("Auth0 does not emit groups by default; they need a custom "
                  "claim added by an Action, and the claim name must then be "
                  "set as OIDC_GROUPS_CLAIM under Advanced.",),
    ),
    IdpPreset(
        "generic-oidc", "Any other OIDC provider", "oidc",
        fields=(_BASE_URL,
                PresetField("issuer", "Issuer URL",
                            placeholder="https://idp.acme.com",
                            help="The base URL that serves "
                                 "/.well-known/openid-configuration"),
                _CLIENT_ID, _CLIENT_SECRET,
                PresetField("groups_claim", "Groups claim", placeholder="groups",
                            required=False)),
    ),
    IdpPreset(
        "entra-saml", "Microsoft Entra ID (SAML 2.0)", "saml",
        fields=(_BASE_URL,
                PresetField("tenant_id", "Directory (tenant) ID",
                            placeholder="00000000-0000-0000-0000-000000000000"),
                PresetField("app_id", "Application (client) ID",
                            placeholder="the enterprise application's app id",
                            help="Entra → Enterprise applications → your app → "
                                 "Overview → Application ID")),
        warnings=(_ENTRA_GROUPS_WARNING,),
    ),
    IdpPreset(
        "okta-saml", "Okta (SAML 2.0)", "saml",
        fields=(_BASE_URL,
                PresetField("metadata_url", "IdP metadata URL",
                            placeholder="https://acme.okta.com/app/…/sso/saml/metadata",
                            help="Okta → your app → Sign On → Metadata URL")),
    ),
    IdpPreset(
        "adfs-saml", "ADFS (SAML 2.0)", "saml",
        fields=(_BASE_URL,
                PresetField("adfs_host", "ADFS hostname",
                            placeholder="adfs.acme.com")),
    ),
    IdpPreset(
        "generic-saml", "Any other SAML 2.0 IdP", "saml",
        fields=(_BASE_URL,
                PresetField("metadata_url", "IdP metadata URL",
                            placeholder="https://idp.acme.com/FederationMetadata.xml",
                            help="If your IdP has no metadata URL, save this "
                                 "preset and paste the XML under Advanced.")),
    ),
    IdpPreset(
        "ldap-ad", "Active Directory (LDAP)", "ldap",
        fields=(PresetField("server_url", "Server URL",
                            placeholder="ldaps://dc01.acme.com",
                            help="Prefer ldaps://. Plain ldap:// sends the "
                                 "user's password in clear unless StartTLS is on."),
                PresetField("ad_domain", "AD domain", placeholder="acme.com",
                            help="Users bind as username@domain (their UPN).")),
    ),
    IdpPreset(
        "ldap-openldap", "OpenLDAP", "ldap",
        fields=(PresetField("server_url", "Server URL",
                            placeholder="ldaps://ldap.acme.com"),
                PresetField("search_base", "Search base",
                            placeholder="ou=people,dc=acme,dc=com"),
                PresetField("email_domain", "Email domain",
                            placeholder="acme.com",
                            help="Used when a directory entry carries no mail "
                                 "attribute.")),
    ),
)

_DERIVERS: Dict[str, Callable[[Dict[str, str]], Dict[str, str]]] = {
    "entra-oidc": _entra_oidc,
    "okta-oidc": _okta_oidc,
    "google-oidc": _google_oidc,
    "keycloak-oidc": _keycloak_oidc,
    "auth0-oidc": _auth0_oidc,
    "generic-oidc": _generic_oidc,
    "entra-saml": _entra_saml,
    "okta-saml": _okta_saml,
    "adfs-saml": _adfs_saml,
    "generic-saml": _generic_saml,
    "ldap-ad": _ldap_ad,
    "ldap-openldap": _ldap_openldap,
}


def list_presets() -> List[IdpPreset]:
    return list(_PRESETS)


def get_preset(preset_id: str) -> IdpPreset:
    for preset in _PRESETS:
        if preset.id == preset_id:
            return preset
    raise PresetError(f"Unknown IdP preset {preset_id!r}")


def derive(preset_id: str, answers: Dict[str, str]) -> DerivedConfig:
    """Map a preset's answers onto instance-settings keys. Pure; writes nothing."""
    preset = get_preset(preset_id)
    missing = [f.name for f in preset.fields
               if f.required and not _clean(answers, f.name)]
    if missing:
        raise PresetError(
            f"{preset.label} needs {', '.join(sorted(missing))}")
    return DerivedConfig(values=_DERIVERS[preset.id](answers),
                         warnings=list(preset.warnings))
