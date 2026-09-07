"""SAML 2.0 SP behaviour, driven by a self-signed mock IdP. No database, no
network: python3-saml validates, we assert on what it accepts and refuses.

The mock IdP builds Responses the way Entra/Okta/ADFS do (signed Assertion
inside an unsigned Response, HTTP-POST binding) and lets each test break one
thing — the signature, the audience, the destination, the request id, the
validity window — so the refusal is attributable to that one thing.
"""
from __future__ import annotations

import base64
import datetime as dt
import uuid
from typing import Dict, List, Optional

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.services import saml_auth
from app.services.saml_auth import (SamlAuthError, SamlConfigError, build_settings,
                                    load_config, process_acs, request_data_for,
                                    sp_metadata_xml, start_login, validate_settings)

ACS = "https://traceiq.test/api/auth/saml/acs"
SP_ENTITY = "https://traceiq.test/api/auth/saml/metadata"
IDP_ENTITY = "https://idp.test/metadata"
IDP_SSO = "https://idp.test/sso"


# --------------------------------------------------------------------------
# mock IdP
# --------------------------------------------------------------------------

def _keypair(cn: str = "idp.test"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=365))
            .sign(key, hashes.SHA256()))
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    return key_pem, cert_pem


@pytest.fixture(scope="module")
def idp():
    return _keypair()


@pytest.fixture(scope="module")
def other_idp():
    return _keypair("evil.test")


def _ts(delta_seconds: int = 0) -> str:
    t = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=delta_seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_response(idp, *, email: Optional[str] = "ada@corp.example.com",
                  name_id: Optional[str] = None,
                  attrs: Optional[Dict[str, List[str]]] = None,
                  request_id: Optional[str] = "_req1",
                  sign_assertion: bool = True, sign_response: bool = False,
                  destination: str = ACS, recipient: str = ACS, audience: str = SP_ENTITY,
                  issuer: str = IDP_ENTITY, not_on_or_after: int = 300,
                  not_before: int = -60, assertion_id: Optional[str] = None,
                  tamper: Optional[callable] = None) -> str:
    """A base64 SAMLResponse. `tamper(xml) -> xml` edits the assertion AFTER
    signing, to prove the signature actually covers it."""
    from onelogin.saml2.utils import OneLogin_Saml2_Utils

    key_pem, cert_pem = idp
    aid = assertion_id or ("_a" + uuid.uuid4().hex)
    rid = "_r" + uuid.uuid4().hex
    attrs = dict(attrs or {})
    if email is not None and "email" not in attrs and name_id is None:
        attrs["email"] = [email]
    nid = name_id if name_id is not None else (email or "user")
    in_resp = f' InResponseTo="{request_id}"' if request_id else ""

    attr_xml = "".join(
        f'<saml:Attribute Name="{k}" NameFormat="urn:oasis:names:tc:SAML:2.0:attrname-format:basic">'
        + "".join(f'<saml:AttributeValue xsi:type="xs:string">{v}</saml:AttributeValue>' for v in vs)
        + "</saml:Attribute>"
        for k, vs in attrs.items())

    assertion = f'''<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" ID="{aid}" Version="2.0" IssueInstant="{_ts()}"><saml:Issuer>{issuer}</saml:Issuer><saml:Subject><saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">{nid}</saml:NameID><saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer"><saml:SubjectConfirmationData NotOnOrAfter="{_ts(not_on_or_after)}" Recipient="{recipient}"{in_resp}/></saml:SubjectConfirmation></saml:Subject><saml:Conditions NotBefore="{_ts(not_before)}" NotOnOrAfter="{_ts(not_on_or_after)}"><saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction></saml:Conditions><saml:AuthnStatement AuthnInstant="{_ts()}" SessionIndex="{aid}"><saml:AuthnContext><saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport</saml:AuthnContextClassRef></saml:AuthnContext></saml:AuthnStatement><saml:AttributeStatement>{attr_xml}</saml:AttributeStatement></saml:Assertion>'''
    if not attrs:
        # An empty AttributeStatement violates the schema; real IdPs omit it.
        assertion = assertion.replace("<saml:AttributeStatement></saml:AttributeStatement>", "")
    if sign_assertion:
        assertion = OneLogin_Saml2_Utils.add_sign(assertion, key_pem, cert_pem).decode()
    if tamper:
        assertion = tamper(assertion)

    response = f'''<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="{rid}" Version="2.0" IssueInstant="{_ts()}" Destination="{destination}"{in_resp}><saml:Issuer>{issuer}</saml:Issuer><samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>{assertion}</samlp:Response>'''
    if sign_response:
        response = OneLogin_Saml2_Utils.add_sign(response, key_pem, cert_pem).decode()
    return base64.b64encode(response.encode()).decode()


def idp_metadata_xml(cert_pem: str, entity: str = IDP_ENTITY, sso: str = IDP_SSO) -> str:
    bare = "".join(l for l in cert_pem.splitlines() if "CERTIFICATE" not in l)
    return f'''<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" xmlns:ds="http://www.w3.org/2000/09/xmldsig#" entityID="{entity}"><md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol"><md:KeyDescriptor use="signing"><ds:KeyInfo><ds:X509Data><ds:X509Certificate>{bare}</ds:X509Certificate></ds:X509Data></ds:KeyInfo></md:KeyDescriptor><md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</md:NameIDFormat><md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="{sso}"/><md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" Location="{sso}"/></md:IDPSSODescriptor></md:EntityDescriptor>'''


def getter(**overrides):
    base = {
        "SAML_SP_ACS_URL": ACS,
        "SAML_IDP_ENTITY_ID": IDP_ENTITY,
        "SAML_IDP_SSO_URL": IDP_SSO,
    }
    base.update(overrides)
    return lambda k: base.get(k)


def sp(idp, **overrides):
    cfg = load_config(getter(SAML_IDP_X509_CERT=idp[1], **overrides))
    settings = build_settings(cfg)
    validate_settings(settings)
    return cfg, settings


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

def test_not_configured_without_an_acs_url():
    assert not saml_auth.is_configured(lambda k: {"SAML_IDP_METADATA_URL": "https://idp/x"}.get(k))
    with pytest.raises(SamlConfigError, match="SAML_SP_ACS_URL"):
        load_config(lambda k: None)


def test_configured_with_metadata_url_or_manual_pair():
    assert saml_auth.is_configured(lambda k: {"SAML_SP_ACS_URL": ACS,
                                              "SAML_IDP_METADATA_URL": "https://idp/x"}.get(k))
    assert saml_auth.is_configured(getter(SAML_IDP_X509_CERT="x"))
    assert not saml_auth.is_configured(getter())  # sso url without a cert


def test_manual_idp_needs_both_url_and_cert():
    with pytest.raises(SamlConfigError, match="metadata"):
        load_config(getter())


def test_sp_key_and_cert_go_together():
    with pytest.raises(SamlConfigError, match="go together"):
        load_config(getter(SAML_IDP_X509_CERT="x", SAML_SP_PRIVATE_KEY="k"))


def test_sp_entity_id_defaults_to_the_metadata_url(idp):
    cfg, _ = sp(idp)
    assert cfg.sp_entity_id == SP_ENTITY


def test_attribute_lists_have_defaults_and_can_be_overridden(idp):
    cfg, _ = sp(idp)
    assert "email" in cfg.email_attrs and "memberOf" in cfg.group_attrs
    cfg2, _ = sp(idp, SAML_ATTR_EMAIL="upn, mail")
    assert cfg2.email_attrs == ["upn", "mail"]


def test_settings_from_idp_metadata(idp):
    cfg = load_config(getter(SAML_IDP_ENTITY_ID=None, SAML_IDP_SSO_URL=None,
                             SAML_IDP_METADATA_URL="https://idp.test/federationmetadata.xml"))
    settings = build_settings(cfg, idp_metadata_xml(idp[1]))
    validate_settings(settings)
    assert settings["idp"]["entityId"] == IDP_ENTITY
    assert settings["idp"]["singleSignOnService"]["url"] == IDP_SSO
    assert settings["idp"].get("x509cert") or settings["idp"].get("x509certMulti")


def test_metadata_configured_but_not_loaded_is_an_error(idp):
    cfg = load_config(getter(SAML_IDP_METADATA_URL="https://idp.test/md"))
    with pytest.raises(SamlConfigError, match="not loaded"):
        build_settings(cfg, None)


def test_pinned_certificate_overrides_metadata(idp, other_idp):
    cfg = load_config(getter(SAML_IDP_METADATA_URL="https://idp.test/md",
                             SAML_IDP_X509_CERT=other_idp[1]))
    settings = build_settings(cfg, idp_metadata_xml(idp[1]))
    assert "x509certMulti" not in settings["idp"]
    assert settings["idp"]["x509cert"].strip() == other_idp[1].strip()


def test_unparseable_metadata_is_a_config_error(idp):
    cfg = load_config(getter(SAML_IDP_METADATA_URL="https://idp.test/md"))
    with pytest.raises(SamlConfigError, match="metadata"):
        build_settings(cfg, "<not-metadata/>")


def test_security_defaults_are_strict(idp):
    _, settings = sp(idp)
    sec = settings["security"]
    assert settings["strict"] is True
    assert sec["wantAssertionsSigned"] is True
    assert sec["rejectDeprecatedAlgorithm"] is True


def test_turning_off_assertion_signing_requires_message_signing(idp):
    _, settings = sp(idp, SAML_WANT_ASSERTIONS_SIGNED="false")
    assert settings["security"]["wantAssertionsSigned"] is False
    assert settings["security"]["wantMessagesSigned"] is True


def test_request_data_comes_from_the_configured_acs_url(idp):
    cfg, _ = sp(idp)
    rd = request_data_for(cfg)
    assert rd == {"https": "on", "http_host": "traceiq.test",
                  "script_name": "/api/auth/saml/acs", "get_data": {}, "post_data": {}}


def test_sp_metadata_advertises_entity_and_acs(idp):
    _, settings = sp(idp)
    xml = sp_metadata_xml(settings)
    assert f'entityID="{SP_ENTITY}"' in xml
    assert f'Location="{ACS}"' in xml
    assert "HTTP-POST" in xml


def test_start_login_redirects_to_the_idp_with_a_request_id(idp):
    cfg, settings = sp(idp)
    url, request_id = start_login(settings, cfg, relay_state="rs-token")
    assert url.startswith(IDP_SSO + "?")
    assert "SAMLRequest=" in url and "RelayState=rs-token" in url
    assert request_id.startswith("ONELOGIN_") or request_id


# --------------------------------------------------------------------------
# the ACS: what is accepted
# --------------------------------------------------------------------------

def test_valid_signed_assertion_yields_the_identity(idp):
    cfg, settings = sp(idp)
    resp = make_response(idp, attrs={"email": ["Ada@Corp.Example.com"],
                                     "displayName": ["Ada Lovelace"],
                                     "groups": ["qa", "traceiq-admins"]})
    ident = process_acs(settings, cfg, resp, request_id="_req1")
    assert ident.email == "ada@corp.example.com"
    assert ident.full_name == "Ada Lovelace"
    assert ident.groups == ["qa", "traceiq-admins"]
    assert ident.assertion_id.startswith("_a")
    assert ident.not_on_or_after and ident.session_index


def test_signed_response_with_signed_assertion_is_accepted(idp):
    cfg, settings = sp(idp)
    resp = make_response(idp, sign_response=True)
    assert process_acs(settings, cfg, resp, "_req1").email == "ada@corp.example.com"


def test_email_falls_back_to_an_email_format_name_id(idp):
    cfg, settings = sp(idp)
    resp = make_response(idp, email=None, name_id="grace@corp.example.com")
    assert process_acs(settings, cfg, resp, "_req1").email == "grace@corp.example.com"


def test_name_is_composed_from_given_and_surname(idp):
    cfg, settings = sp(idp)
    resp = make_response(idp, attrs={"email": ["g@corp.example.com"],
                                     "givenName": ["Grace"], "sn": ["Hopper"]})
    assert process_acs(settings, cfg, resp, "_req1").full_name == "Grace Hopper"


def test_entra_style_claim_uris_are_understood(idp):
    cfg, settings = sp(idp)
    resp = make_response(idp, email=None, name_id="opaque-id", attrs={
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress": ["e@corp.example.com"],
        "http://schemas.microsoft.com/identity/claims/displayname": ["E. Dijkstra"],
        "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups": ["g1", "g2"],
    })
    ident = process_acs(settings, cfg, resp, "_req1")
    assert (ident.email, ident.full_name, ident.groups) == ("e@corp.example.com", "E. Dijkstra", ["g1", "g2"])


def test_group_dns_are_reduced_to_names(idp):
    cfg, settings = sp(idp)
    resp = make_response(idp, attrs={"email": ["a@corp.example.com"],
                                     "memberOf": ["CN=qa,OU=Groups,DC=corp,DC=example,DC=com",
                                                  "CN=traceiq-admins,OU=Groups,DC=corp,DC=example,DC=com"]})
    assert process_acs(settings, cfg, resp, "_req1").groups == ["qa", "traceiq-admins"]


def test_a_configured_email_attribute_wins(idp):
    cfg, settings = sp(idp, SAML_ATTR_EMAIL="upn")
    resp = make_response(idp, attrs={"email": ["wrong@corp.example.com"],
                                     "upn": ["right@corp.example.com"]})
    assert process_acs(settings, cfg, resp, "_req1").email == "right@corp.example.com"


# --------------------------------------------------------------------------
# the ACS: what is refused
# --------------------------------------------------------------------------

def _refused(settings, cfg, resp, request_id="_req1") -> str:
    with pytest.raises(SamlAuthError) as exc:
        process_acs(settings, cfg, resp, request_id)
    return exc.value.reason


def test_unsigned_response_is_refused(idp):
    cfg, settings = sp(idp)
    reason = _refused(settings, cfg, make_response(idp, sign_assertion=False))
    assert "sign" in reason.lower()


def test_signed_response_but_unsigned_assertion_is_refused_by_default(idp):
    cfg, settings = sp(idp)
    reason = _refused(settings, cfg, make_response(idp, sign_assertion=False, sign_response=True))
    assert "assertion" in reason.lower()


def test_signed_response_alone_is_enough_when_configured(idp):
    cfg, settings = sp(idp, SAML_WANT_ASSERTIONS_SIGNED="false")
    resp = make_response(idp, sign_assertion=False, sign_response=True)
    assert process_acs(settings, cfg, resp, "_req1").email == "ada@corp.example.com"


def test_signature_from_another_idp_is_refused(idp, other_idp):
    cfg, settings = sp(idp)
    reason = _refused(settings, cfg, make_response(other_idp))
    assert "signature" in reason.lower()


def test_tampering_after_signing_is_refused(idp):
    cfg, settings = sp(idp)
    resp = make_response(idp, tamper=lambda xml: xml.replace(
        "ada@corp.example.com", "mallory@corp.example.com"))
    reason = _refused(settings, cfg, resp)
    assert "signature" in reason.lower() or "digest" in reason.lower()


def test_wrong_in_response_to_is_refused(idp):
    cfg, settings = sp(idp)
    reason = _refused(settings, cfg, make_response(idp, request_id="_someone_elses"))
    assert "inresponseto" in reason.lower()


def test_wrong_destination_is_refused(idp):
    cfg, settings = sp(idp)
    reason = _refused(settings, cfg, make_response(idp, destination="https://evil.test/acs"))
    assert "received at" in reason.lower()


def test_wrong_audience_is_refused(idp):
    cfg, settings = sp(idp)
    reason = _refused(settings, cfg, make_response(idp, audience="https://other-sp.test"))
    assert "audience" in reason.lower()


def test_wrong_recipient_is_refused(idp):
    cfg, settings = sp(idp)
    reason = _refused(settings, cfg, make_response(idp, recipient="https://evil.test/acs"))
    assert "subjectconfirmation" in reason.lower()


def test_expired_assertion_is_refused(idp):
    cfg, settings = sp(idp)
    reason = _refused(settings, cfg, make_response(idp, not_on_or_after=-5))
    # python3-saml reports the SubjectConfirmationData window first; the
    # Conditions window would fail the same way one check later.
    assert "subjectconfirmation" in reason.lower()


def test_not_yet_valid_assertion_is_refused(idp):
    cfg, settings = sp(idp)
    reason = _refused(settings, cfg, make_response(idp, not_before=600))
    assert reason


def test_unknown_issuer_is_refused(idp):
    cfg, settings = sp(idp)
    reason = _refused(settings, cfg, make_response(idp, issuer="https://someone-else.test"))
    assert "issuer" in reason.lower()


def test_response_without_an_email_is_refused(idp):
    cfg, settings = sp(idp)
    reason = _refused(settings, cfg, make_response(idp, email=None, name_id="opaque-id"))
    assert "NameID" in reason


def test_garbage_is_refused_not_crashed(idp):
    cfg, settings = sp(idp)
    with pytest.raises(SamlAuthError):
        process_acs(settings, cfg, "not-base64!!", "_req1")
    with pytest.raises(SamlAuthError):
        process_acs(settings, cfg, base64.b64encode(b"<html/>").decode(), "_req1")


# --------------------------------------------------------------------------
# IdP-initiated
# --------------------------------------------------------------------------

def test_idp_initiated_is_refused_by_default(idp):
    cfg, settings = sp(idp)
    reason = _refused(settings, cfg, make_response(idp, request_id=None), request_id=None)
    assert "SAML_ALLOW_IDP_INITIATED" in reason


def test_idp_initiated_is_accepted_when_allowed(idp):
    cfg, settings = sp(idp, SAML_ALLOW_IDP_INITIATED="true")
    resp = make_response(idp, request_id=None)
    assert process_acs(settings, cfg, resp, None).email == "ada@corp.example.com"


def test_replayed_sp_initiated_response_is_not_accepted_as_idp_initiated(idp):
    # Allowing IdP-initiated must not turn a captured SP-initiated Response
    # (which carries InResponseTo) into a valid unsolicited login.
    cfg, settings = sp(idp, SAML_ALLOW_IDP_INITIATED="true")
    reason = _refused(settings, cfg, make_response(idp, request_id="_stale"), request_id=None)
    assert "inresponseto" in reason.lower() and "_stale" in reason


# --------------------------------------------------------------------------
# replay cache arithmetic
# --------------------------------------------------------------------------

def test_replay_ttl_tracks_the_assertion_validity():
    ident = saml_auth.SamlIdentity("n", "e", "f", [], "_a", not_on_or_after=1000 + 600,
                                   session_index=None)
    assert saml_auth.replay_ttl_seconds(ident, now=1000) == 660
    ident_short = saml_auth.SamlIdentity("n", "e", "f", [], "_a", not_on_or_after=1000 + 10,
                                         session_index=None)
    assert saml_auth.replay_ttl_seconds(ident_short, now=1000) == 300
    ident_none = saml_auth.SamlIdentity("n", "e", "f", [], "_a", None, None)
    assert saml_auth.replay_ttl_seconds(ident_none) == 300
