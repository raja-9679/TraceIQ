"""LDAP / Active Directory authentication (bind-as-user).

TraceIQ never stores or syncs directory passwords and needs no service
account: it binds against the directory AS the logging-in user, using
LDAP_BIND_DN_TEMPLATE to turn the typed username into a bind DN (AD
userPrincipalName like `{username}@corp.example.com` works as a bind name
too). A successful bind = valid credentials.

All settings are admin-editable instance settings (group "ldap"):
  LDAP_SERVER_URL        ldaps://dc01:636 (recommended) or ldap://dc01:389
  LDAP_BIND_DN_TEMPLATE  '{username}@corp.example.com' or
                         'uid={username},ou=people,dc=example,dc=com'
  LDAP_SEARCH_BASE       optional; enables mail/displayName lookup after bind
  LDAP_EMAIL_DOMAIN      fallback domain when usernames aren't emails
  LDAP_STARTTLS          upgrade a plain ldap:// connection before binding

ldap3 is synchronous — callers run authenticate() in a thread.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from app.services.instance_settings import effective

logger = logging.getLogger(__name__)


class LdapAuthError(Exception):
    """Invalid credentials or unreachable directory. The message is safe to
    show to the user (no DN/attribute internals)."""


@dataclass
class LdapIdentity:
    email: str
    full_name: str
    username: str
    # Directory group names (memberOf, reduced from DNs). Consumed by
    # app/services/federation.py to map onto workspace roles and teams. Empty
    # when the directory returns no memberOf or no search was possible — a
    # federated login then falls back to the configured default role.
    groups: Optional[list] = None


def is_configured() -> bool:
    return bool(effective("LDAP_SERVER_URL") and effective("LDAP_BIND_DN_TEMPLATE"))


def _sanitize_username(username: str) -> str:
    u = (username or "").strip()
    # DN-injection guard: the username is substituted into a DN/UPN template.
    # Allow word chars plus the usual AD name punctuation; anything else
    # (comma, equals, parens, NUL, ...) is refused rather than escaped.
    if not u or not re.fullmatch(r"[A-Za-z0-9._@'-]{1,256}", u):
        raise LdapAuthError("Invalid username or password")
    return u


def authenticate(username: str, password: str) -> LdapIdentity:
    """Bind as the user; return their identity. Raises LdapAuthError on bad
    credentials, misconfiguration, or an unreachable server."""
    import ldap3
    from ldap3.core.exceptions import LDAPException
    from ldap3.utils.conv import escape_filter_chars

    username = _sanitize_username(username)
    if not password:
        raise LdapAuthError("Invalid username or password")
    if not is_configured():
        raise LdapAuthError("LDAP is not configured on this instance")

    server_url = str(effective("LDAP_SERVER_URL")).strip()
    bind_template = str(effective("LDAP_BIND_DN_TEMPLATE"))
    if "{username}" not in bind_template:
        # A template without the placeholder is a constant DN — every login
        # would bind as that one entry, so anyone who knows that entry's
        # password could then pick which account to become. Refuse it.
        logger.error("[ldap] LDAP_BIND_DN_TEMPLATE is missing the {username} placeholder")
        raise LdapAuthError("LDAP is misconfigured on this instance — contact your admin")
    bind_dn = bind_template.replace("{username}", username)
    search_base = str(effective("LDAP_SEARCH_BASE") or "").strip()
    email_domain = str(effective("LDAP_EMAIL_DOMAIN") or "").strip().lstrip("@")
    use_starttls = bool(effective("LDAP_STARTTLS"))

    # TLS: ldap3 otherwise auto-creates a Tls() with validate=CERT_NONE, so
    # BOTH ldaps:// and StartTLS would accept any certificate — an on-path
    # attacker could then harvest the corporate password and forge bind
    # success. Require certificate validation for any encrypted transport
    # unless an admin explicitly opts out (LDAP_TLS_INSECURE).
    tls_obj = None
    scheme_is_tls = server_url.lower().startswith("ldaps://")
    if scheme_is_tls or use_starttls:
        import ssl
        insecure = bool(effective("LDAP_TLS_INSECURE"))
        ca_cert = str(effective("LDAP_CA_CERT") or "").strip()
        tls_kwargs = {"validate": ssl.CERT_NONE if insecure else ssl.CERT_REQUIRED}
        if ca_cert:
            tls_kwargs["ca_certs_file"] = ca_cert
        tls_obj = ldap3.Tls(**tls_kwargs)

    try:
        # get_info=NONE skips the schema read; without it ldap3 validates
        # filter attribute names against the schema and refuses AD attributes
        # (userPrincipalName) on non-AD servers and vice versa. Servers treat
        # unknown attributes in a filter as non-matching, which is what we want.
        server = ldap3.Server(server_url, connect_timeout=10, get_info=ldap3.NONE, tls=tls_obj)
        conn = ldap3.Connection(
            server, user=bind_dn, password=password,
            receive_timeout=10, read_only=True)
        if use_starttls:
            conn.start_tls()
        if not conn.bind():
            raise LdapAuthError("Invalid username or password")
    except LdapAuthError:
        raise
    except LDAPException as exc:
        logger.warning("[ldap] cannot reach/bind %s: %s", server_url, exc)
        raise LdapAuthError("Corporate directory is unreachable — try again or contact your admin")

    email = ""
    full_name = ""
    groups: list = []

    def _single(entry, attr) -> str:
        # ldap3 returns a list for multi-valued attributes. Never stringify a
        # list into an "email" (that used to produce "['a@x','b@x']" as an
        # address); treat multi-valued/absent as empty so the username-based
        # fallback applies.
        if attr not in entry:
            return ""
        v = entry[attr].value
        if isinstance(v, (list, tuple)):
            return str(v[0]) if len(v) == 1 else ""
        return str(v) if v is not None else ""

    def _member_of(entry) -> list:
        # memberOf is inherently multi-valued, so _single's "a list means
        # ambiguous, treat as empty" rule does not apply here.
        from app.services.federation import group_names_from_dns
        if "memberOf" not in entry:
            return []
        raw = entry["memberOf"].value
        return group_names_from_dns(raw if isinstance(raw, (list, tuple)) else [raw])

    def _take(entry) -> tuple:
        return _single(entry, "mail"), (_single(entry, "displayName") or _single(entry, "cn"))

    attrs = ["mail", "displayName", "cn", "memberOf"]
    bind_is_dn = "=" in bind_dn
    try:
        if bind_is_dn:
            # Read ONLY the entry we authenticated as (BASE scope on the bind DN).
            # A subtree search keyed on a mutable, possibly non-unique `mail`
            # could return a DIFFERENT entry — an attacker who can set their own
            # `mail` to the victim's address would then be logged in as the
            # victim. The bound entry is the one identity we can trust.
            conn.search(bind_dn, "(objectClass=*)",
                        search_scope=ldap3.BASE, attributes=attrs)
            if len(conn.entries) == 1:
                email, full_name = _take(conn.entries[0])
                groups = _member_of(conn.entries[0])
        elif search_base:
            # UPN/username bind: no DN to self-read. Enrich the display name from
            # an UNAMBIGUOUS single match, but never let a searched `mail`
            # override the authenticated username as the identity — the username
            # (a UPN, i.e. an email) is the trusted identity here.
            safe = escape_filter_chars(username)
            conn.search(
                search_base,
                f"(|(userPrincipalName={safe})(sAMAccountName={safe})(uid={safe}))",
                attributes=attrs)
            if len(conn.entries) == 1:
                _, full_name = _take(conn.entries[0])
                groups = _member_of(conn.entries[0])
    except LDAPException as exc:
        # Attribute lookup is best-effort; the bind already proved identity.
        logger.info("[ldap] attribute search failed (continuing): %s", exc)
    finally:
        try:
            conn.unbind()
        except Exception:
            pass

    if not email or email.lower() in ("none", "[]"):
        if "@" in username:
            email = username
        elif email_domain:
            email = f"{username}@{email_domain}"
        else:
            raise LdapAuthError(
                "Directory returned no email for this account — set LDAP_EMAIL_DOMAIN "
                "or use your full email as the username")

    return LdapIdentity(
        email=email.lower(),
        full_name=full_name or username,
        username=username,
        groups=groups)
