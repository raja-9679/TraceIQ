"""SAML 2.0 service-provider login (workstream F3).

TraceIQ is the SP; the customer's IdP (Entra ID, Okta, ADFS, PingFederate,
Shibboleth…) is the asserting party. SP-initiated web-browser SSO with the
HTTP-Redirect binding for the AuthnRequest and HTTP-POST for the Response.
IdP-initiated sign-in is off unless `SAML_ALLOW_IDP_INITIATED` is on.

Design
------
Everything that touches XML or signatures is delegated to python3-saml
(OneLogin), which is where the signature-wrapping, comment-injection and
canonicalisation defences live. Hand-rolling SAML validation is how SP
implementations get CVEs. `xmlsec` ships binary wheels that bundle
libxmlsec1, so the backend image needs no apt packages for this.

This module is deliberately framework-free (no FastAPI, no DB): it turns
instance settings into a python3-saml configuration, builds the SP metadata,
starts a login, and validates an ACS post into a `SamlIdentity`. The route
layer in app/api/auth.py does the HTTP and the provisioning, and the tests
drive this module with a self-signed mock IdP.

What is checked on the way in (python3-saml, `strict` mode): the Response
and/or Assertion signature against the IdP certificate(s) from metadata,
Issuer, Destination (against the configured ACS URL — derived from the
setting, never from request headers, which a proxy or attacker can set),
Audience (our entity id), NotBefore/NotOnOrAfter, SubjectConfirmation
Recipient, and InResponseTo against the AuthnRequest id we issued. On top of
that, this module rejects any assertion id it has already accepted (Redis,
TTL = the assertion's own validity window) so a captured Response cannot be
replayed inside its lifetime.

Not supported in this version: Single Logout, and IdP-signed *artifact*
binding. Encrypted assertions ARE supported when an SP key pair is configured
(`SAML_SP_PRIVATE_KEY` / `SAML_SP_X509_CERT`); the same pair signs
AuthnRequests.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

HTTP_REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
HTTP_POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
NAMEID_EMAIL = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
NAMEID_UNSPECIFIED = "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified"

# Attribute names IdPs commonly use. The instance setting can override; these
# are what we try when it is blank, in order.
DEFAULT_EMAIL_ATTRS = (
    "email", "mail", "emailaddress",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "urn:oid:0.9.2342.19200300.100.1.3",
)
DEFAULT_NAME_ATTRS = (
    "displayName", "name", "cn",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
    "http://schemas.microsoft.com/identity/claims/displayname",
    "urn:oid:2.16.840.1.113730.3.1.241",
)
DEFAULT_GROUP_ATTRS = (
    "groups", "memberOf", "roles",
    "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups",
    "http://schemas.xmlsoap.org/claims/Group",
)

# python3-saml's metadata cache: IdP metadata rarely changes, but certificate
# rotation happens, so keep the TTL short and refetch once on a signature
# failure (see the route layer).
_METADATA_TTL = 3600.0


class SamlConfigError(Exception):
    """The SAML settings cannot produce a working SP. Surfaced as 503 at login
    and as 400 when an admin saves the form."""


class SamlAuthError(Exception):
    """The Response was rejected. `reason` is python3-saml's explanation and
    goes to the log, never to the browser (it names internals such as the
    expected Destination)."""

    def __init__(self, message: str, reason: str = ""):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class SamlConfig:
    sp_entity_id: str
    acs_url: str
    idp_metadata_url: str
    idp_metadata_xml: str
    idp_entity_id: str
    idp_sso_url: str
    idp_x509_cert: str
    sp_x509_cert: str
    sp_private_key: str
    email_attrs: List[str]
    name_attrs: List[str]
    group_attrs: List[str]
    want_assertions_signed: bool
    allow_idp_initiated: bool
    post_login_redirect: str
    allowed_email_domains: str

    @property
    def uses_metadata(self) -> bool:
        return bool(self.idp_metadata_url or self.idp_metadata_xml)

    @property
    def can_decrypt(self) -> bool:
        return bool(self.sp_private_key and self.sp_x509_cert)


@dataclass
class SamlIdentity:
    name_id: str
    email: str
    full_name: str
    groups: List[str]
    assertion_id: str
    not_on_or_after: Optional[int]      # epoch seconds, from the assertion
    session_index: Optional[str]
    attributes: Dict[str, List[str]] = field(default_factory=dict)


# --------------------------------------------------------------------------
# settings -> config
# --------------------------------------------------------------------------

def _split(raw: Any) -> List[str]:
    return [p.strip() for p in str(raw or "").replace("\n", ",").split(",") if p.strip()]


def _bool(raw: Any, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def is_configured(get: Optional[Callable[[str], Any]] = None) -> bool:
    """Enough settings to offer a SAML button. Does not prove they work."""
    effective = get or _effective
    idp = effective("SAML_IDP_METADATA_URL") or effective("SAML_IDP_METADATA_XML") or (
        effective("SAML_IDP_SSO_URL") and effective("SAML_IDP_X509_CERT"))
    return bool(idp and effective("SAML_SP_ACS_URL"))


def load_config(get: Optional[Callable[[str], Any]] = None) -> SamlConfig:
    """Read the SAML settings, or raise SamlConfigError with a specific reason."""
    effective = get or _effective
    g = lambda k: str(effective(k) or "").strip()  # noqa: E731

    acs = g("SAML_SP_ACS_URL")
    if not acs:
        raise SamlConfigError("SAML_SP_ACS_URL is required (https://<traceiq>/api/auth/saml/acs)")
    parsed = urlparse(acs)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise SamlConfigError(f"SAML_SP_ACS_URL is not an absolute URL: {acs!r}")

    metadata_url, metadata_xml = g("SAML_IDP_METADATA_URL"), g("SAML_IDP_METADATA_XML")
    sso_url, cert = g("SAML_IDP_SSO_URL"), g("SAML_IDP_X509_CERT")
    if not (metadata_url or metadata_xml or (sso_url and cert)):
        raise SamlConfigError(
            "Provide the IdP metadata (URL or pasted XML), or both SAML_IDP_SSO_URL "
            "and SAML_IDP_X509_CERT")
    if (sso_url and not cert) or (cert and not sso_url):
        if not (metadata_url or metadata_xml):
            raise SamlConfigError("SAML_IDP_SSO_URL and SAML_IDP_X509_CERT go together")

    sp_key, sp_cert = g("SAML_SP_PRIVATE_KEY"), g("SAML_SP_X509_CERT")
    if bool(sp_key) != bool(sp_cert):
        raise SamlConfigError("SAML_SP_PRIVATE_KEY and SAML_SP_X509_CERT go together")

    return SamlConfig(
        sp_entity_id=g("SAML_SP_ENTITY_ID") or acs.rsplit("/acs", 1)[0] + "/metadata",
        acs_url=acs,
        idp_metadata_url=metadata_url,
        idp_metadata_xml=metadata_xml,
        idp_entity_id=g("SAML_IDP_ENTITY_ID"),
        idp_sso_url=sso_url,
        idp_x509_cert=cert,
        sp_x509_cert=sp_cert,
        sp_private_key=sp_key,
        email_attrs=_split(g("SAML_ATTR_EMAIL")) or list(DEFAULT_EMAIL_ATTRS),
        name_attrs=_split(g("SAML_ATTR_NAME")) or list(DEFAULT_NAME_ATTRS),
        group_attrs=_split(g("SAML_ATTR_GROUPS")) or list(DEFAULT_GROUP_ATTRS),
        want_assertions_signed=_bool(effective("SAML_WANT_ASSERTIONS_SIGNED"), True),
        allow_idp_initiated=_bool(effective("SAML_ALLOW_IDP_INITIATED"), False),
        post_login_redirect=g("SAML_POST_LOGIN_REDIRECT") or g("OIDC_POST_LOGIN_REDIRECT"),
        allowed_email_domains=g("SAML_ALLOWED_EMAIL_DOMAINS"),
    )


def _effective(key: str) -> Any:
    from app.services.instance_settings import effective
    return effective(key)


# --------------------------------------------------------------------------
# config -> python3-saml settings
# --------------------------------------------------------------------------

def build_settings(cfg: SamlConfig, idp_metadata_xml: Optional[str] = None) -> dict:
    """The python3-saml settings dict. `idp_metadata_xml` is the fetched (or
    pasted) IdP metadata; explicit SAML_IDP_* values win over it so an operator
    can pin a certificate while still reading the SSO URL from metadata."""
    from onelogin.saml2.constants import OneLogin_Saml2_Constants as C
    from onelogin.saml2.idp_metadata_parser import OneLogin_Saml2_IdPMetadataParser

    settings: dict = {
        "strict": True,
        "debug": False,
        "sp": {
            "entityId": cfg.sp_entity_id,
            "assertionConsumerService": {"url": cfg.acs_url, "binding": HTTP_POST},
            "NameIDFormat": NAMEID_EMAIL,
            "x509cert": cfg.sp_x509_cert,
            "privateKey": cfg.sp_private_key,
        },
        "idp": {},
        "security": {
            "authnRequestsSigned": cfg.can_decrypt,
            "logoutRequestSigned": False,
            "logoutResponseSigned": False,
            "wantMessagesSigned": False,
            "wantAssertionsSigned": cfg.want_assertions_signed,
            "wantAssertionsEncrypted": False,
            "wantNameId": True,
            "wantNameIdEncrypted": False,
            "wantAttributeStatement": False,
            "requestedAuthnContext": False,
            "allowRepeatAttributeName": True,
            "rejectDeprecatedAlgorithm": True,
            "signatureAlgorithm": C.RSA_SHA256,
            "digestAlgorithm": C.SHA256,
            "allowSingleLabelDomains": False,
        },
    }
    if cfg.want_assertions_signed is False:
        # If assertions may be unsigned the Response itself must be, or nothing is.
        settings["security"]["wantMessagesSigned"] = True

    metadata_xml = cfg.idp_metadata_xml or idp_metadata_xml
    if metadata_xml:
        try:
            parsed = OneLogin_Saml2_IdPMetadataParser.parse(
                metadata_xml, required_sso_binding=HTTP_REDIRECT)
        except Exception as exc:
            raise SamlConfigError(f"IdP metadata could not be parsed: {exc}") from exc
        idp = parsed.get("idp") or {}
        if not idp.get("singleSignOnService", {}).get("url"):
            raise SamlConfigError(
                "IdP metadata has no HTTP-Redirect SingleSignOnService endpoint")
        settings["idp"] = idp
    elif cfg.uses_metadata:
        raise SamlConfigError("IdP metadata is configured but was not loaded")

    idp = settings["idp"]
    if cfg.idp_entity_id:
        idp["entityId"] = cfg.idp_entity_id
    if cfg.idp_sso_url:
        idp["singleSignOnService"] = {"url": cfg.idp_sso_url, "binding": HTTP_REDIRECT}
    if cfg.idp_x509_cert:
        idp.pop("x509certMulti", None)
        idp["x509cert"] = cfg.idp_x509_cert
    if not idp.get("entityId"):
        raise SamlConfigError("SAML_IDP_ENTITY_ID is required when the metadata does not carry one")
    if not (idp.get("x509cert") or idp.get("x509certMulti")):
        raise SamlConfigError("No IdP signing certificate — set SAML_IDP_X509_CERT or use metadata")
    return settings


def validate_settings(settings: dict) -> None:
    """Let python3-saml check the dict the way it will at login time."""
    from onelogin.saml2.settings import OneLogin_Saml2_Settings
    try:
        OneLogin_Saml2_Settings(settings, sp_validation_only=False)
    except Exception as exc:
        raise SamlConfigError(f"SAML settings rejected: {exc}") from exc


def sp_metadata_xml(settings: dict) -> str:
    from onelogin.saml2.settings import OneLogin_Saml2_Settings
    s = OneLogin_Saml2_Settings(settings, sp_validation_only=True)
    xml = s.get_sp_metadata()
    errors = s.validate_metadata(xml)
    if errors:
        raise SamlConfigError(f"SP metadata invalid: {', '.join(errors)}")
    return xml.decode() if isinstance(xml, bytes) else xml


# --------------------------------------------------------------------------
# the two protocol steps
# --------------------------------------------------------------------------

def request_data_for(cfg: SamlConfig, post_data: Optional[dict] = None) -> dict:
    """python3-saml's view of 'the current request', derived from the
    configured ACS URL rather than from Host / X-Forwarded-* headers. The only
    thing it is used for is the Destination check, and that must compare
    against what the operator configured, not what the client sent."""
    p = urlparse(cfg.acs_url)
    return {
        "https": "on" if p.scheme == "https" else "off",
        "http_host": p.netloc,
        "script_name": p.path,
        "get_data": {},
        "post_data": post_data or {},
    }


def start_login(settings: dict, cfg: SamlConfig, relay_state: str) -> tuple[str, str]:
    """Return (redirect_url, authn_request_id). The id must travel with the
    browser (inside the signed RelayState) so the ACS can check InResponseTo."""
    from onelogin.saml2.auth import OneLogin_Saml2_Auth
    auth = OneLogin_Saml2_Auth(request_data_for(cfg), old_settings=settings)
    url = auth.login(return_to=relay_state)
    return url, auth.get_last_request_id()


def process_acs(settings: dict, cfg: SamlConfig, saml_response_b64: str,
                request_id: Optional[str]) -> SamlIdentity:
    """Validate a posted SAMLResponse and extract the identity.

    `request_id` is the AuthnRequest id from our RelayState; None means an
    IdP-initiated Response, which is only accepted when the setting allows it.
    """
    from onelogin.saml2.auth import OneLogin_Saml2_Auth
    from onelogin.saml2.response import OneLogin_Saml2_Response

    if request_id is None and not cfg.allow_idp_initiated:
        raise SamlAuthError(
            "Unsolicited SAML response refused",
            reason="no RelayState from this SP and SAML_ALLOW_IDP_INITIATED is off")

    auth = OneLogin_Saml2_Auth(
        request_data_for(cfg, {"SAMLResponse": saml_response_b64}),
        old_settings=settings)

    if request_id is None:
        # IdP-initiated: the Response must not answer a request. python3-saml
        # (unlike the PHP toolkit) has no `rejectUnsolicitedResponsesWithInResponseTo`
        # and only compares InResponseTo when it was GIVEN a request id, so an
        # SP-initiated Response captured in flight and re-posted without its
        # RelayState would otherwise be accepted as an unsolicited login.
        try:
            in_response_to = OneLogin_Saml2_Response(
                auth.get_settings(), saml_response_b64).get_in_response_to()
        except Exception as exc:
            raise SamlAuthError("SAML response could not be processed", reason=str(exc)) from exc
        if in_response_to:
            raise SamlAuthError(
                "SAML response rejected",
                reason=f"unsolicited Response carries InResponseTo={in_response_to!r}; "
                       "an IdP-initiated Response must not answer a request")

    try:
        auth.process_response(request_id=request_id)
    except Exception as exc:  # malformed base64 / XML surface as exceptions
        raise SamlAuthError("SAML response could not be processed", reason=str(exc)) from exc
    errors = auth.get_errors()
    if errors or not auth.is_authenticated():
        raise SamlAuthError(
            "SAML response rejected",
            reason=f"{', '.join(errors) or 'not authenticated'}: {auth.get_last_error_reason() or ''}")

    attributes: Dict[str, List[str]] = {}
    for k, v in (auth.get_attributes() or {}).items():
        attributes[k] = [str(x) for x in (v if isinstance(v, (list, tuple)) else [v]) if x is not None]

    name_id = (auth.get_nameid() or "").strip()
    email = _first(attributes, cfg.email_attrs)
    if not email and "@" in name_id:
        email = name_id
    email = email.strip().lower()
    if not email or "@" not in email:
        raise SamlAuthError(
            "SAML response carries no email address",
            reason=f"NameID={name_id!r}, attributes={sorted(attributes)}")

    full_name = _first(attributes, cfg.name_attrs) or _compose_name(attributes) or email.split("@")[0]
    groups: List[str] = []
    for attr in cfg.group_attrs:
        groups.extend(_lookup(attributes, attr))
    from app.services.federation import group_names_from_dns, normalize_groups
    groups = normalize_groups(group_names_from_dns(groups) if any("=" in g for g in groups) else groups)

    return SamlIdentity(
        name_id=name_id,
        email=email,
        full_name=full_name,
        groups=groups,
        assertion_id=auth.get_last_assertion_id() or "",
        not_on_or_after=auth.get_last_assertion_not_on_or_after(),
        session_index=auth.get_session_index(),
        attributes=attributes,
    )


def _lookup(attributes: Dict[str, List[str]], name: str) -> List[str]:
    if name in attributes:
        return attributes[name]
    lowered = name.lower()
    for k, v in attributes.items():
        if k.lower() == lowered:
            return v
    return []


def _first(attributes: Dict[str, List[str]], names: List[str]) -> str:
    for name in names:
        for value in _lookup(attributes, name):
            if value and value.strip():
                return value.strip()
    return ""


def _compose_name(attributes: Dict[str, List[str]]) -> str:
    given = _first(attributes, ["givenName", "firstName",
                                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname",
                                "urn:oid:2.5.4.42"])
    sn = _first(attributes, ["sn", "surname", "lastName",
                             "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname",
                             "urn:oid:2.5.4.4"])
    return " ".join(p for p in (given, sn) if p)


# --------------------------------------------------------------------------
# replay protection
# --------------------------------------------------------------------------

def replay_ttl_seconds(identity: SamlIdentity, now: Optional[float] = None) -> int:
    """How long to remember an assertion id: until it would have expired
    anyway, plus a minute of clock slack, never less than five minutes."""
    now = now if now is not None else time.time()
    if identity.not_on_or_after:
        return max(300, int(identity.not_on_or_after - now) + 60)
    return 300


async def remember_assertion(identity: SamlIdentity) -> bool:
    """True the first time an assertion id is seen; False on replay.

    Redis SET NX with the assertion's own validity as TTL. Fails CLOSED: if
    Redis is unreachable the login is refused rather than risking a replay."""
    if not identity.assertion_id:
        return False
    from app.core.redis import RedisClient
    key = f"saml:assertion:{identity.assertion_id}"
    ok = await RedisClient.get_instance().set(key, "1", nx=True, ex=replay_ttl_seconds(identity))
    return bool(ok)


# --------------------------------------------------------------------------
# IdP metadata fetch (remote), with a short cache
# --------------------------------------------------------------------------

_metadata_cache: Dict[str, tuple[float, str]] = {}


async def fetch_idp_metadata(url: str, *, force: bool = False) -> str:
    """Fetch IdP metadata through the outbound URL guard (an admin-supplied URL
    is still a URL the server will connect to). Cached for an hour."""
    cached = _metadata_cache.get(url)
    if cached and not force and cached[0] > time.monotonic():
        return cached[1]
    from app.core.net_guard import safe_get
    try:
        resp = await safe_get(url, timeout=10.0, headers={"Accept": "application/samlmetadata+xml, application/xml, text/xml"})
    except Exception as exc:
        raise SamlConfigError(f"IdP metadata could not be fetched from {url}: {exc}") from exc
    if resp.status_code != 200:
        raise SamlConfigError(f"IdP metadata URL answered HTTP {resp.status_code}")
    xml = resp.text
    if "EntityDescriptor" not in xml:
        raise SamlConfigError("IdP metadata URL did not return SAML metadata")
    _metadata_cache[url] = (time.monotonic() + _METADATA_TTL, xml)
    return xml


def forget_idp_metadata(url: Optional[str] = None) -> None:
    if url is None:
        _metadata_cache.clear()
    else:
        _metadata_cache.pop(url, None)


async def load_settings(cfg: SamlConfig, *, force_refresh: bool = False) -> dict:
    """Config + (fetched) metadata -> validated python3-saml settings."""
    xml = None
    if cfg.idp_metadata_url and not cfg.idp_metadata_xml:
        xml = await fetch_idp_metadata(cfg.idp_metadata_url, force=force_refresh)
    settings = build_settings(cfg, xml)
    validate_settings(settings)
    return settings
