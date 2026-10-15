"""Worker egress allowlist (WP8).

A browser under test makes requests to whatever the page tells it to — third
-party scripts, trackers, and (if the app or a test is hostile) the internal
network the worker sits on. `net_guard` covers URLs the *backend* fetches; this
covers what the *worker's browser* fetches.

Opt-in per project (`Project.network_policy.enforce`) or instance-wide
(`EGRESS_ALLOWLIST_ENFORCED`). When enforced the worker aborts every
http(s)/ws(s) request whose host is not on the effective allowlist and records
it as a blocked network event (evidence). Semantics deliberately match
net_guard:

* **link-local / metadata (169.254.0.0/16, fe80::/10), multicast, unspecified
  and reserved addresses are never allowed**, whatever the allowlist says;
* **private and loopback addresses need `allow_private`**, and a project may
  only turn that on when the instance allows private targets at all
  (`ALLOW_PRIVATE_NETWORK_TARGETS`) — the instance is the ceiling, as it is for
  capture level. Without it, an allowlisted public name that resolves to
  10.x is refused (DNS-rebinding into the worker's network);
* an allowlist entry matches the host and its subdomains.

The environment's `base_url` host is allowed implicitly — a policy that blocks
the application under test is a configuration error, not a control.

Resolved here at dispatch and carried in the job (`settings.egress_policy`), so
a worker never decides its own policy.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

_HOST_RE = re.compile(r"^(\*\.)?[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?)*$")
_MAX_HOSTS = 200


class NetworkPolicy(BaseModel):
    enforce: bool = False
    allowed_hosts: List[str] = Field(default_factory=list)
    allow_private: bool = False

    @field_validator("allowed_hosts")
    @classmethod
    def _hosts(cls, value: List[str]) -> List[str]:
        out: List[str] = []
        for raw in value or []:
            host = normalize_host(raw)
            if host and host not in out:
                out.append(host)
        if len(out) > _MAX_HOSTS:
            raise ValueError(f"at most {_MAX_HOSTS} hosts")
        return out


def normalize_host(raw: Any) -> Optional[str]:
    """'https://App.Example.com:8443/x' → 'app.example.com'. A leading '*.'
    is accepted and dropped (entries always cover subdomains). IP literals are
    kept as-is. Raises ValueError for anything that is not a hostname."""
    text = str(raw or "").strip().lower()
    if not text:
        return None
    if "://" in text:
        text = urlsplit(text).hostname or ""
    else:
        text = text.split("/")[0]
        if text.count(":") == 1:          # host:port, not an IPv6 literal
            text = text.split(":")[0]
    text = text.strip("[]")
    if text.startswith("*."):
        text = text[2:]
    import ipaddress
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        pass
    if not _HOST_RE.match(text):
        raise ValueError(f"not a hostname: {raw!r}")
    return text


def resolve_egress_policy(stored: Optional[Dict[str, Any]], *,
                          instance_enforced: bool,
                          instance_hosts: Iterable[str],
                          instance_allows_private: bool,
                          base_url: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The policy a job carries, or None when egress is not enforced (the
    worker then behaves exactly as before)."""
    try:
        policy = NetworkPolicy(**(stored or {}))
    except Exception:
        # A corrupt stored policy must not silently disable an instance-forced
        # control, nor crash dispatch: treat it as "enforce, nothing extra".
        policy = NetworkPolicy(enforce=True)
    if not (policy.enforce or instance_enforced):
        return None
    hosts: List[str] = list(policy.allowed_hosts)
    for raw in list(instance_hosts or []) + ([base_url] if base_url else []):
        try:
            host = normalize_host(raw)
        except ValueError:
            continue
        if host and host not in hosts:
            hosts.append(host)
    return {
        "enforce": True,
        "allowed_hosts": hosts,
        "allow_private": bool(policy.allow_private and instance_allows_private),
    }


def effective_for_project(project, environment: Optional[Dict[str, Any]] = None
                          ) -> Optional[Dict[str, Any]]:
    from app.services.instance_settings import effective
    from app.core.client_ip import as_list
    return resolve_egress_policy(
        getattr(project, "network_policy", None) if project else None,
        instance_enforced=bool(effective("EGRESS_ALLOWLIST_ENFORCED")),
        instance_hosts=as_list(effective("EGRESS_ALLOWED_HOSTS")),
        instance_allows_private=bool(effective("ALLOW_PRIVATE_NETWORK_TARGETS")),
        base_url=(environment or {}).get("base_url"),
    )
