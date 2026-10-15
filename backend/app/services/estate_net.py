"""Real network access for estate scans (WP9) — the `Net` the checks run on.

Passive only, and every HTTP(S) touch goes through `app/core/net_guard.py`:

* DNS via dnspython. Imported lazily: the dependency is new and an older
  backend image without it must still import this module (the scan then
  reports DNS as unavailable instead of crashing the worker at import).
* crt.sh for certificate transparency: net_guard-validated, no redirects,
  size-capped, timed out.
* Page GETs: net_guard-validated, certificate verification ON, no redirects
  followed (a redirect is itself the evidence for the HTTP→HTTPS check), body
  capped.
* The certificate is read from an ordinary verified TLS handshake — the same
  one a browser makes. A certificate that fails verification is reported as
  that failure; nothing here ever connects with verification disabled.
"""
from __future__ import annotations

import asyncio
import json
import socket
import ssl
from typing import Any, Dict, List, Optional, Tuple

from app.services.estate_checks import (
    CertUntrusted, DnsError, Net, NxDomain, Unreachable, cert_facts, crtsh_url,
)

USER_AGENT = "TraceIQ-Estate/1.0 (passive posture check)"
PAGE_MAX_BYTES = 512 * 1024
CT_MAX_BYTES = 8 * 1024 * 1024
PAGE_TIMEOUT = 10.0
CT_TIMEOUT = 30.0
DNS_TIMEOUT = 4.0


class DnsPythonClient:
    """`DnsClient` over dnspython. TXT strings are joined (a long SPF record
    is split into 255-byte chunks on the wire)."""

    def __init__(self, timeout: float = DNS_TIMEOUT):
        import dns.resolver  # lazy: see module docstring
        self._resolver = dns.resolver.Resolver()
        self._resolver.lifetime = timeout
        self._resolver.timeout = timeout

    def query(self, name: str, rdtype: str) -> List[str]:
        import dns.exception
        import dns.resolver
        try:
            ans = self._resolver.resolve(name, rdtype, raise_on_no_answer=False)
        except dns.resolver.NXDOMAIN as e:
            raise NxDomain(name) from e
        except (dns.resolver.NoNameservers, dns.exception.Timeout,
                dns.resolver.NoAnswer, dns.resolver.YXDOMAIN) as e:
            raise DnsError(f"{rdtype} {name}: {type(e).__name__}") from e
        except dns.exception.DNSException as e:
            raise DnsError(f"{rdtype} {name}: {e}") from e
        if ans.rrset is None:
            return []
        # A CNAME'd name answers SOA with the TARGET zone's SOA; that does not
        # make the queried name an apex.
        if rdtype == "SOA" and str(ans.canonical_name).rstrip(".").lower() != name.rstrip(".").lower():
            return []
        out = []
        for r in ans:
            if rdtype == "TXT":
                out.append(b"".join(r.strings).decode("utf-8", errors="replace"))
            else:
                out.append(r.to_text())
        return out


def _validate(url: str) -> None:
    from app.core.net_guard import UnsafeUrlError, validate_outbound_url
    try:
        asyncio.run(validate_outbound_url(url))
    except UnsafeUrlError as e:
        raise Unreachable(f"refused by the outbound-URL guard: {e}") from e


def guarded_get(url: str, *, timeout: float = PAGE_TIMEOUT,
                max_bytes: int = PAGE_MAX_BYTES) -> Tuple[int, Dict[str, str], str]:
    """GET with verification on, no redirects, body capped. Raises Unreachable
    on any transport failure (CertUntrusted when the certificate is the cause)."""
    import httpx

    _validate(url)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, verify=True,
                          headers={"User-Agent": USER_AGENT}) as client:
            with client.stream("GET", url) as resp:
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    buf += chunk
                    if len(buf) >= max_bytes:
                        break
                headers = {k.lower(): v for k, v in resp.headers.items()}
                body = bytes(buf[:max_bytes]).decode(resp.encoding or "utf-8", errors="replace")
                return resp.status_code, headers, body
    except httpx.ConnectError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e) or isinstance(e.__cause__, ssl.SSLCertVerificationError):
            raise CertUntrusted(str(e)[:300]) from e
        raise Unreachable(str(e)[:300]) from e
    except httpx.HTTPError as e:
        raise Unreachable(str(e)[:300]) from e


def peer_certificate(host: str, port: int = 443, timeout: float = PAGE_TIMEOUT) -> Dict[str, Any]:
    """cert_facts() of the certificate a verified handshake to host:443 presents."""
    _validate(f"https://{host}/")
    ctx = ssl.create_default_context()   # CERT_REQUIRED + hostname check
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
    except ssl.SSLCertVerificationError as e:
        raise CertUntrusted(e.verify_message or str(e)) from e
    except (OSError, ssl.SSLError) as e:
        raise Unreachable(str(e)[:300]) from e
    if not der:
        raise Unreachable("no certificate presented")
    return cert_facts(der)


def crtsh_entries(apex: str) -> Any:
    status, _h, body = guarded_get(crtsh_url(apex), timeout=CT_TIMEOUT, max_bytes=CT_MAX_BYTES)
    if status != 200:
        raise Unreachable(f"crt.sh answered {status}")
    try:
        return json.loads(body)
    except ValueError as e:
        # A capped (truncated) response is not valid JSON either.
        raise Unreachable("crt.sh returned unreadable or oversized JSON") from e


class _NoDns:
    """Stand-in when dnspython is not installed (older image)."""

    def query(self, name: str, rdtype: str) -> List[str]:
        raise DnsError("dnspython is not installed in this backend image — rebuild it")


def default_net(dns: Optional[Any] = None) -> Net:
    if dns is None:
        try:
            dns = DnsPythonClient()
        except ImportError:
            dns = _NoDns()
    return Net(dns=dns, fetch=guarded_get, cert=peer_certificate, ct=crtsh_entries)
