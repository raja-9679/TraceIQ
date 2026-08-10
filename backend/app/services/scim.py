"""SCIM 2.0 wire-format mapping — the protocol half of workstream F2.

TraceIQ previously had no deprovisioning path at all: an employee removed from
Okta or Entra kept their account, their sessions and their refresh token
indefinitely. SSO without SCIM only automates the *joining*.

This module is deliberately pure — no ORM, no session — because the risk here is
the wire format, not the database work. Real identity providers do not agree
with each other:

  Okta   {"op": "replace", "value": {"active": false}}      (no path)
  Entra  {"op": "Replace", "path": "active", "value": "False"}  (capital op,
                                                             STRING boolean)

`bool("False")` is `True`, so the obvious implementation leaves a deprovisioned
user active — a silent failure in the one direction that matters. Everything
here is written against what providers actually send.

The endpoints live in `app/api/scim.py`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"

# The only filter attributes Okta and Entra actually use. Anything else is
# refused rather than ignored: silently dropping a filter returns the whole
# directory to a client that asked for one user, and the client then acts on
# whatever came back first.
FILTERABLE = {"username": "userName", "externalid": "externalId",
              "displayname": "displayName"}

_FILTER_RE = re.compile(r'^\s*(\w+)\s+(\w+)\s+"(.*)"\s*$')
_MEMBER_PATH_RE = re.compile(r'^members\[\s*value\s+eq\s+"([^"]+)"\s*\]$', re.I)


class ScimError(Exception):
    """A SCIM-shaped error. Carries the HTTP status the provider must see —
    the status code is load-bearing for IdP retry behaviour, e.g. a 409 without
    `scimType: uniqueness` makes Okta retry a create indefinitely."""

    def __init__(self, status: int, detail: str, scim_type: Optional[str] = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.scim_type = scim_type

    def to_dict(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "schemas": [ERROR_SCHEMA],
            "status": str(self.status),
            "detail": self.detail,
        }
        if self.scim_type:
            body["scimType"] = self.scim_type
        return body


@dataclass
class PatchOps:
    """The subset of a PATCH request TraceIQ acts on."""
    active: Optional[bool] = None
    display_name: Optional[str] = None
    add_members: List[str] = field(default_factory=list)
    remove_members: List[str] = field(default_factory=list)
    replace_members: Optional[List[str]] = None


def _split_name(full_name: str) -> Dict[str, str]:
    parts = (full_name or "").strip().split()
    given = parts[0] if parts else ""
    family = " ".join(parts[1:]) if len(parts) > 1 else ""
    return {"formatted": full_name or "", "givenName": given, "familyName": family}


def user_to_scim(user: Any) -> Dict[str, Any]:
    """Map a User row onto a SCIM User resource."""
    doc: Dict[str, Any] = {
        "schemas": [USER_SCHEMA],
        # SCIM ids are strings. Emitting an integer means the client sends an
        # integer back and path matching stops working.
        "id": str(user.id),
        "userName": user.email,
        "name": _split_name(user.full_name),
        "displayName": user.full_name or user.email,
        "emails": [{"value": user.email, "type": "work", "primary": True}],
        "active": bool(user.is_active),
        "meta": {"resourceType": "User"},
    }
    external_id = getattr(user, "scim_external_id", None)
    if external_id:
        # Okta reconciles on externalId; dropping it makes every sync look like
        # a fresh user and duplicates the account.
        doc["externalId"] = external_id
    created = getattr(user, "created_at", None)
    if created is not None:
        doc["meta"]["created"] = created.isoformat()
    return doc


def group_to_scim(*, team_id: int, display_name: str, member_ids: List[int],
                  external_id: Optional[str] = None) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "schemas": [GROUP_SCHEMA],
        "id": str(team_id),
        "displayName": display_name,
        "members": [{"value": str(m)} for m in member_ids],
        "meta": {"resourceType": "Group"},
    }
    if external_id:
        doc["externalId"] = external_id
    return doc


def list_response(resources: List[Dict[str, Any]], *, total: int,
                  start_index: int, count: int) -> Dict[str, Any]:
    return {
        "schemas": [LIST_SCHEMA],
        "totalResults": total,
        "startIndex": start_index,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


def parse_filter(raw: Optional[str]) -> Optional[Tuple[str, str]]:
    """`userName eq "x"` → `("userName", "x")`, or None when absent.

    Only `eq` on the three attributes providers use is supported; anything else
    raises rather than being ignored.
    """
    if not raw or not raw.strip():
        return None
    match = _FILTER_RE.match(raw)
    if not match:
        raise ScimError(400, f"Unsupported filter: {raw}", scim_type="invalidFilter")
    attr, op, value = match.groups()
    if op.lower() != "eq":
        raise ScimError(400, f"Only the 'eq' operator is supported, got {op!r}",
                        scim_type="invalidFilter")
    canonical = FILTERABLE.get(attr.lower())
    if not canonical:
        raise ScimError(400, f"Cannot filter on {attr!r}", scim_type="invalidFilter")
    return canonical, value


def _as_bool(value: Any) -> Optional[bool]:
    """Providers send booleans as booleans OR as strings. `bool("False")` is
    True, so this is the difference between deprovisioning someone and only
    appearing to."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1"):
            return True
        if lowered in ("false", "0"):
            return False
    return None


def _member_values(value: Any) -> List[str]:
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value:
        if isinstance(item, dict) and item.get("value") is not None:
            out.append(str(item["value"]))
        elif isinstance(item, (str, int)):
            out.append(str(item))
    return out


def parse_patch(body: Dict[str, Any]) -> PatchOps:
    """Reduce a SCIM PatchOp to the operations TraceIQ implements."""
    operations = body.get("Operations") or body.get("operations")
    if not operations or not isinstance(operations, list):
        raise ScimError(400, "PATCH requires a non-empty Operations array",
                        scim_type="invalidValue")

    ops = PatchOps()
    for operation in operations:
        if not isinstance(operation, dict):
            raise ScimError(400, "Each operation must be an object",
                            scim_type="invalidValue")
        verb = str(operation.get("op") or "").strip().lower()
        if verb not in ("add", "replace", "remove"):
            raise ScimError(400, f"Unsupported op {operation.get('op')!r}",
                            scim_type="invalidSyntax")
        path = str(operation.get("path") or "").strip()
        value = operation.get("value")

        # Filtered member removal: path='members[value eq "7"]', no value.
        member_match = _MEMBER_PATH_RE.match(path)
        if member_match:
            ops.remove_members.append(member_match.group(1))
            continue

        if path.lower() == "members":
            if verb == "remove":
                ops.remove_members.extend(_member_values(value))
            elif verb == "add":
                ops.add_members.extend(_member_values(value))
            else:
                ops.replace_members = _member_values(value)
            continue

        if path.lower() == "active":
            parsed = _as_bool(value)
            if parsed is None:
                raise ScimError(400, f"active must be a boolean, got {value!r}",
                                scim_type="invalidValue")
            ops.active = parsed
            continue

        if path.lower() == "displayname":
            ops.display_name = str(value) if value is not None else None
            continue

        # Pathless form (Okta): the value is a partial resource.
        if not path and isinstance(value, dict):
            if "active" in value:
                parsed = _as_bool(value["active"])
                if parsed is None:
                    raise ScimError(400, f"active must be a boolean, got "
                                         f"{value['active']!r}",
                                    scim_type="invalidValue")
                ops.active = parsed
            name = value.get("name")
            if isinstance(name, dict) and name.get("formatted"):
                ops.display_name = str(name["formatted"])
            elif value.get("displayName"):
                ops.display_name = str(value["displayName"])
            continue

        # Anything else (name.givenName, title, department…) is accepted and
        # ignored: refusing unknown attributes makes an IdP mark the whole sync
        # as failed over a field TraceIQ has no use for.

    return ops
