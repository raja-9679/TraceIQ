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
    bind_dn = str(effective("LDAP_BIND_DN_TEMPLATE")).replace("{username}", username)
    search_base = str(effective("LDAP_SEARCH_BASE") or "").strip()
    email_domain = str(effective("LDAP_EMAIL_DOMAIN") or "").strip().lstrip("@")
    use_starttls = bool(effective("LDAP_STARTTLS"))

    try:
        # get_info=NONE skips the schema read; without it ldap3 validates
        # filter attribute names against the schema and refuses AD attributes
        # (userPrincipalName) on non-AD servers and vice versa. Servers treat
        # unknown attributes in a filter as non-matching, which is what we want.
        server = ldap3.Server(server_url, connect_timeout=10, get_info=ldap3.NONE)
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

    def _take(entry) -> tuple:
        mail = str(entry.mail) if "mail" in entry else ""
        name = (str(entry.displayName) if "displayName" in entry else "") or \
               (str(entry.cn) if "cn" in entry else "")
        return mail, name

    attrs = ["mail", "displayName", "cn"]
    try:
        if search_base:
            safe = escape_filter_chars(username)
            conn.search(
                search_base,
                f"(|(userPrincipalName={safe})(sAMAccountName={safe})(uid={safe})(mail={safe}))",
                attributes=attrs)
            if conn.entries:
                email, full_name = _take(conn.entries[0])
        # Subtree search can be denied (OpenLDAP's default ACL is `by * none`
        # for other entries) while self-read still works. When the bind name
        # is a real DN, read the user's own entry directly.
        if not email and "=" in bind_dn:
            conn.search(bind_dn, "(objectClass=*)",
                        search_scope=ldap3.BASE, attributes=attrs)
            if conn.entries:
                email, full_name = _take(conn.entries[0])
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
        username=username)
