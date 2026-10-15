"""The real client address, and CIDR allowlists checked against it (WP8).

Behind nginx (the shipped frontend image proxies `/api/`) the TCP peer the
backend sees is the proxy, so an allowlist checked against it would either
admit everyone or no one. `X-Forwarded-For` carries the real address, but any
client can send that header — honouring it unconditionally would let an
attacker *choose* the address an allowlist is checked against.

So: the header is read only when the TCP peer is a configured trusted proxy
(`TRUSTED_PROXY_CIDRS`, default empty = trust nobody, use the peer address),
and it is walked right-to-left skipping trusted hops. The left-most entries are
whatever the client sent; only the hop appended by the first proxy we trust is
believed. An unparseable hop is returned as-is, so it fails every CIDR match —
a malformed header never widens access.
"""
from __future__ import annotations

import ipaddress
import json
from typing import Iterable, List, Optional, Sequence, Union

Network = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]


def as_list(value) -> List[str]:
    """A list setting from the DB (JSON list) or the environment (JSON or a
    comma-separated string)."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            return [str(v).strip() for v in json.loads(text) if str(v).strip()]
        except ValueError:
            pass
    return [part.strip() for part in text.split(",") if part.strip()]


def parse_cidrs(values: Iterable[str]) -> List[Network]:
    """Parse CIDRs (a bare address is a /32 or /128). Raises ValueError on a
    bad entry — callers validating user input surface it; callers enforcing
    use `parse_cidrs_lenient`."""
    out: List[Network] = []
    for raw in values:
        out.append(ipaddress.ip_network(str(raw).strip(), strict=False))
    return out


def parse_cidrs_lenient(values: Iterable[str]) -> List[Network]:
    out: List[Network] = []
    for raw in values:
        try:
            out.append(ipaddress.ip_network(str(raw).strip(), strict=False))
        except ValueError:
            continue
    return out


def normalize_cidrs(values: Optional[Sequence[str]]) -> Optional[List[str]]:
    """Validate and canonicalise a user-supplied allowlist. None/empty → None
    (no restriction)."""
    if not values:
        return None
    nets = parse_cidrs(values)
    return [str(n) for n in nets] or None


def ip_in(ip: str, networks: Sequence[Network]) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    # An IPv4-mapped IPv6 peer (::ffff:10.0.0.1 on a dual-stack socket) must
    # match an IPv4 rule, or an allowlist silently refuses legitimate callers.
    mapped = getattr(addr, "ipv4_mapped", None)
    for net in networks:
        if addr.version == net.version and addr in net:
            return True
        if mapped is not None and net.version == 4 and mapped in net:
            return True
    return False


def resolve_client_ip(peer: str, forwarded_for: Optional[str],
                      trusted: Sequence[Network]) -> str:
    """Pure core of `client_ip`, for tests."""
    if not trusted or not ip_in(peer, trusted) or not forwarded_for:
        return peer
    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    for hop in reversed(hops):
        if not ip_in(hop, trusted):
            return hop
    # Every hop is a trusted proxy: the left-most is the closest we have.
    return hops[0] if hops else peer


def trusted_proxies() -> List[Network]:
    from app.services.instance_settings import effective
    return parse_cidrs_lenient(as_list(effective("TRUSTED_PROXY_CIDRS")))


def peer_is_trusted_proxy(request) -> bool:
    peer = request.client.host if getattr(request, "client", None) else ""
    trusted = trusted_proxies()
    return bool(trusted) and ip_in(peer, trusted)


def client_ip(request) -> str:
    peer = request.client.host if getattr(request, "client", None) else ""
    return resolve_client_ip(peer, request.headers.get("x-forwarded-for"),
                             trusted_proxies())


def allowed(ip: str, cidrs: Optional[Sequence[str]]) -> bool:
    """True when `cidrs` is empty (no restriction) or `ip` falls in one."""
    if not cidrs:
        return True
    return ip_in(ip, parse_cidrs_lenient(cidrs))
