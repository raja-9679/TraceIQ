"""Where federated (SSO / LDAP) users land — the policy half of workstream F1.

Before this, SSO and LDAP JIT both called `provision_standalone_user`, which
mints a Tenant per user and makes them its Tenant Admin. For one person that is
indistinguishable from registering; for an organisation it is a fault — 500
employees become 500 isolated tenants with 500 tenant admins and no shared
project between them.

An operator now declares the shape explicitly:

  standalone  every federated user gets their own tenant (the pre-F1
              behaviour, and still the default so existing installs upgrade
              unchanged)
  workspace   federated users join FEDERATED_WORKSPACE_ID with a default role,
              refined by IdP group mapping
  deny        no JIT provisioning at all — only accounts that already exist
              (invited, or SCIM-provisioned) may sign in

Two design commitments worth stating, because both are the kind of thing a
later change quietly breaks:

**Misconfiguration fails closed.** `workspace` mode with no workspace id raises
rather than degrading to `standalone`. Degrading would reproduce the exact bug
in a deployment whose admin believes they configured their way out of it.

**Group mapping is evaluated on every login, not only at creation** (see
`sync_federated_access` in `user_provisioning.py`). Create-only mapping means
removing someone from the admin group in Okta never removes their TraceIQ
admin — an IdP that looks authoritative but isn't is worse than no mapping.

Role names are constrained to workspace roles: enterprise directories often let
users create groups, so a group that could name `Tenant Admin` would be a
self-service privilege escalation.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.services.instance_settings import effective as _effective

logger = logging.getLogger(__name__)

MODE_STANDALONE = "standalone"
MODE_WORKSPACE = "workspace"
MODE_DENY = "deny"
MODES = (MODE_STANDALONE, MODE_WORKSPACE, MODE_DENY)

# Workspace-level roles an IdP group may name, least privileged first. The
# order is also the precedence used when a user matches several groups.
WORKSPACE_ROLE_NAMES = ("Workspace Member", "Workspace Admin")

# UserWorkspace.role predates role_id and is still read by access_service for
# rows without one, so both columns are kept consistent.
LEGACY_ROLE_LEVEL = {"Workspace Admin": "admin", "Workspace Member": "member"}


class FederationConfigError(Exception):
    """The federation settings cannot be honoured. Surfaced to the user as a
    5xx ("SSO is misconfigured — contact your administrator") and logged with
    the offending value; never silently downgraded to another mode."""


@dataclass(frozen=True)
class FederationPolicy:
    mode: str
    workspace_id: Optional[int]
    default_role: str
    group_role_map: Dict[str, str]
    group_team_map: Dict[str, str]

    @property
    def maps_groups(self) -> bool:
        return bool(self.group_role_map or self.group_team_map)


def parse_mapping(raw: Optional[str]) -> Dict[str, str]:
    """`group=target` pairs separated by commas or newlines.

    Group keys are lowercased (directories are inconsistent about case);
    targets are not, because they are matched against `Role.name` / `Team.name`.
    """
    out: Dict[str, str] = {}
    for chunk in re.split(r"[,\n]", str(raw or "")):
        if "=" not in chunk:
            continue
        group, _, target = chunk.partition("=")
        group, target = group.strip().lower(), target.strip()
        if group and target:
            out[group] = target
    return out


def resolve_policy(get: Optional[Any] = None) -> FederationPolicy:
    """Read the current federation settings, or raise FederationConfigError.

    `get` defaults to the live instance-settings lookup. Passing another getter
    is how `validate_proposed` checks an admin's unsaved form against the same
    rules the login path will apply.
    """
    effective = get or _effective
    mode = str(effective("FEDERATED_PROVISIONING_MODE") or MODE_STANDALONE).strip().lower()
    if mode not in MODES:
        raise FederationConfigError(
            f"FEDERATED_PROVISIONING_MODE is {mode!r}; expected one of {', '.join(MODES)}")

    raw_ws = str(effective("FEDERATED_WORKSPACE_ID") or "").strip()
    workspace_id: Optional[int] = None
    if raw_ws and raw_ws != "0":
        try:
            workspace_id = int(raw_ws)
        except ValueError:
            raise FederationConfigError(
                f"FEDERATED_WORKSPACE_ID is {raw_ws!r}; expected a workspace id")

    if mode == MODE_WORKSPACE and workspace_id is None:
        raise FederationConfigError(
            "FEDERATED_PROVISIONING_MODE is 'workspace' but FEDERATED_WORKSPACE_ID "
            "is not set — refusing to fall back to a tenant per user")

    default_role = str(effective("FEDERATED_DEFAULT_ROLE") or "").strip() or "Workspace Member"
    if default_role not in WORKSPACE_ROLE_NAMES:
        raise FederationConfigError(
            f"FEDERATED_DEFAULT_ROLE is {default_role!r}; expected one of "
            f"{', '.join(WORKSPACE_ROLE_NAMES)}")

    group_role_map = parse_mapping(effective("FEDERATED_GROUP_ROLE_MAP"))
    for group, role in group_role_map.items():
        if role not in WORKSPACE_ROLE_NAMES:
            raise FederationConfigError(
                f"FEDERATED_GROUP_ROLE_MAP maps group {group!r} to {role!r}, which is "
                f"not a workspace role ({', '.join(WORKSPACE_ROLE_NAMES)}). An identity "
                "provider group cannot grant tenant-wide administration")

    return FederationPolicy(
        mode=mode,
        workspace_id=workspace_id,
        default_role=default_role,
        group_role_map=group_role_map,
        group_team_map=parse_mapping(effective("FEDERATED_GROUP_TEAM_MAP")),
    )


def validate_proposed(values: Dict[str, Any]) -> Optional[FederationPolicy]:
    """Resolve the policy an admin's unsaved settings edit would produce, or
    raise FederationConfigError.

    Because resolve_policy fails closed, a typo here takes every federated login
    down until somebody notices. Validating at save time turns that into an
    error message on the form. Proposed values win over stored ones so that
    *clearing* a required field is caught too.

    Returns None when the edit touches no federation setting. The caller still
    has to check that the target workspace exists — that needs a database, which
    this module deliberately does not have.
    """
    if not any(k.startswith("FEDERATED_") for k in values):
        return None

    def get(key: str) -> Any:
        if key in values:
            return values[key]
        return _effective(key)

    return resolve_policy(get)


def role_for_groups(policy: FederationPolicy, groups: List[str]) -> str:
    """The workspace role a user's IdP groups earn, or the default role."""
    lowered = {g.strip().lower() for g in groups or []}
    matched = [policy.group_role_map[g] for g in lowered if g in policy.group_role_map]
    if not matched:
        return policy.default_role
    return max(matched, key=lambda name: WORKSPACE_ROLE_NAMES.index(name))


def teams_for_groups(policy: FederationPolicy, groups: List[str]) -> List[str]:
    """Team names the user's groups map onto, in map order, deduplicated."""
    lowered = {g.strip().lower() for g in groups or []}
    out: List[str] = []
    for group, team in policy.group_team_map.items():
        if group in lowered and team not in out:
            out.append(team)
    return out


def normalize_groups(raw: Any) -> List[str]:
    """Group names out of an IdP claim of unknown shape.

    OIDC `groups` may be a JSON array or a single delimited string; SAML
    attributes are always strings. Non-string members are dropped rather than
    stringified — `"7"` is not a group name anyone configured.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = re.split(r"[,\s]+", raw)
    elif isinstance(raw, (list, tuple, set)):
        parts = [p for p in raw if isinstance(p, str)]
    else:
        return []
    return [p.strip() for p in parts if isinstance(p, str) and p.strip()]


def group_names_from_dns(values: Any) -> List[str]:
    """LDAP `memberOf` values (DNs) reduced to group names.

    `CN=QA Team,OU=Groups,DC=corp` → `QA Team`. RFC 4514 escapes a literal
    comma inside an RDN value, so splitting on a bare comma would truncate
    `Payments\\, EU` to `Payments`; the escape is honoured and unescaped.
    """
    out: List[str] = []
    for value in values or []:
        if not isinstance(value, str) or not value.strip():
            continue
        dn = value.strip()
        first = re.split(r"(?<!\\),", dn)[0]
        name = first.split("=", 1)[1] if "=" in first else first
        name = re.sub(r"\\(.)", r"\1", name).strip()
        if name:
            out.append(name)
    return out
