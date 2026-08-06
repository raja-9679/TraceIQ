"""Outbound URL validation for user-supplied targets (SSRF guard).

Any endpoint that fetches a URL the caller controls must route through
`validate_outbound_url` first, and must re-validate after every redirect hop —
a permitted public host can 302 straight to 169.254.169.254. Use
`safe_get` when you just need a GET, since it handles the redirect chain.

Design note on private networks: TraceIQ's self-hosted users legitimately point
it at internal apps (192.168.x, 10.x, or a Docker service name), so private
targets cannot be banned outright without breaking the product. They are denied
by default and enabled with ALLOW_PRIVATE_NETWORK_TARGETS=true. Link-local is
never allowed regardless — that range carries cloud instance-metadata
credentials and is never a legitimate test target.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Iterable, Optional
from urllib.parse import urlsplit

import httpx

ALLOWED_SCHEMES = ("http", "https")
MAX_REDIRECTS = 5


class UnsafeUrlError(ValueError):
    """Raised when a user-supplied URL resolves somewhere it must not reach."""


def _settings():
    # Imported lazily so this module stays importable in tests that don't have
    # a full environment configured.
    from app.core.config import settings
    return settings


def _is_never_allowed(ip: ipaddress._BaseAddress) -> Optional[str]:
    """Ranges denied even when private targets are permitted.

    Loopback is deliberately excluded here and handled as private instead, so
    ::1 behaves the same as 127.0.0.1 — IPv6 loopback is inside a block that
    `is_reserved` also matches, and treating it as "never allowed" would make
    the two loopback forms follow different rules.
    """
    if ip.is_loopback:
        return None
    if ip.is_link_local:
        # 169.254.0.0/16 and fe80::/10 — cloud metadata (169.254.169.254).
        return "a link-local/metadata address"
    if ip.is_multicast:
        return "a multicast address"
    if ip.is_unspecified:
        return "an unspecified address"
    if ip.is_reserved:
        return "a reserved address"
    if getattr(ip, "is_site_local", False):
        return "a site-local address"
    return None


def _is_private(ip: ipaddress._BaseAddress) -> bool:
    return ip.is_private or ip.is_loopback


async def _resolve(host: str) -> list[ipaddress._BaseAddress]:
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo, host, None, 0, socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"could not resolve host {host!r}: {exc}") from exc
    out = []
    for info in infos:
        try:
            out.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    if not out:
        raise UnsafeUrlError(f"host {host!r} resolved to no usable address")
    return out


async def validate_outbound_url(
    raw: str,
    *,
    allow_private: Optional[bool] = None,
    extra_allowed_hosts: Iterable[str] = (),
) -> str:
    """Return `raw` unchanged if it is safe to fetch, else raise UnsafeUrlError.

    Resolves the hostname and checks **every** returned address, so a DNS name
    that maps to a private IP is caught. This is still not a complete defence
    against DNS rebinding (the address can change between this check and the
    connection); treat it as raising the cost, with network egress rules as the
    real control.
    """
    settings = _settings()
    if allow_private is None:
        # Effective policy: admin UI (DB) override, else environment.
        from app.services.instance_settings import effective
        allow_private = bool(effective("ALLOW_PRIVATE_NETWORK_TARGETS"))

    parts = urlsplit(raw)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(
            f"scheme {parts.scheme!r} is not allowed (permitted: {', '.join(ALLOWED_SCHEMES)})"
        )
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("URL has no host")

    from app.services.instance_settings import effective as _eff_setting
    allowed_hosts = {
        h.strip().lower()
        for h in list(_eff_setting("OUTBOUND_ALLOWED_HOSTS") or [])
        + list(extra_allowed_hosts)
        if h and h.strip()
    }
    # An allowlisted host is a *private-network* exemption only. It must NOT
    # bypass the never-allowed ranges (cloud metadata 169.254.169.254,
    # link-local, etc.) — otherwise an allowlisted name CNAMEd to the metadata
    # IP would reach it. So we still resolve and run _is_never_allowed; we only
    # skip the private-address rejection for allowlisted hosts.
    is_allowlisted = host.lower() in allowed_hosts
    try:
        resolved = await _resolve(host)
    except UnsafeUrlError:
        if is_allowlisted:
            # Can't resolve here (e.g. an internal name only the worker can see);
            # trust the explicit operator allowlist rather than hard-failing.
            return raw
        raise

    for ip in resolved:
        reason = _is_never_allowed(ip)
        if reason:
            raise UnsafeUrlError(f"{host} resolves to {reason} ({ip}) — refused")
        if _is_private(ip) and not allow_private and not is_allowlisted:
            raise UnsafeUrlError(
                f"{host} resolves to the private/loopback address {ip}. If this is "
                "an internal app you intend to test, set "
                "ALLOW_PRIVATE_NETWORK_TARGETS=true (or add the host to "
                "OUTBOUND_ALLOWED_HOSTS). Only do this on a trusted, "
                "single-tenant deployment."
            )
    return raw


async def safe_get(
    url: str,
    *,
    timeout: float = 10.0,
    headers: Optional[dict] = None,
    allow_private: Optional[bool] = None,
) -> httpx.Response:
    """GET `url`, validating the target before each hop of the redirect chain.

    Redirects are followed manually because httpx's `follow_redirects=True`
    would connect to the redirect target without re-running validation.
    """
    current = await validate_outbound_url(url, allow_private=allow_private)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            resp = await client.get(current, headers=headers or {})
            if resp.status_code not in (301, 302, 303, 307, 308):
                return resp
            location = resp.headers.get("location")
            if not location:
                return resp
            current = await validate_outbound_url(
                str(httpx.URL(current).join(location)), allow_private=allow_private
            )
    raise UnsafeUrlError(f"too many redirects (>{MAX_REDIRECTS}) starting from {url}")
