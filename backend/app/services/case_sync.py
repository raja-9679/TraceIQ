"""Two-way git sync of test cases (WP11 item 3) — the pure part.

`GET /api/suites/{id}/sync` exports a suite tree as one document per case;
the CLI (`traceiq pull`) writes each to its own JSON file with sorted keys so
diffs are clean. `POST /api/suites/{id}/sync` takes the edited set back and
turns it into CaseProposals — never direct writes, so a git push goes through
the same review queue (and the same workspace auto-apply policy) as an agent.

Conflict detection is optimistic: each exported document carries the hash of
its synced content at export time (`base_hash`) and the suite it was in
(`base_suite_id`). If the server copy has changed since, the import reports a
conflict for that case instead of proposing an overwrite. The hash is also
copied into UPDATE/DELETE proposal payloads (`sync_base_hash`) and re-checked
when a human accepts, so a proposal that sat in the queue while someone edited
the case cannot silently clobber that edit.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

# What a synced file controls. Suite membership is tracked separately (a move
# is its own proposal), dataset/run_matrix/executor stay UI-managed for now.
SYNC_FIELDS = ("name", "steps", "tags", "priority", "code_paths")
FORMAT_VERSION = 1
MAX_SYNC_CASES = 2000
# Per-document and whole-request bounds: each accepted document becomes a
# stored CaseProposal payload.
MAX_DOC_BYTES = 1024 * 1024
MAX_DOC_STEPS = 1000
MAX_NAME_CHARS = 500
MAX_LIST_ITEMS = 200
MAX_SYNC_BYTES = 25 * 1024 * 1024


def content_of(doc: Dict[str, Any]) -> Dict[str, Any]:
    """The synced fields of a case/doc, with defaults filled the way the
    database stores them so an untouched export hashes identically."""
    return {
        "name": doc.get("name") or "",
        "steps": doc.get("steps") or [],
        "tags": doc.get("tags") or [],
        "priority": doc.get("priority"),
        "code_paths": doc.get("code_paths") or [],
    }


def content_hash(doc: Dict[str, Any]) -> str:
    canonical = json.dumps(content_of(doc), sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def case_doc(case: Any) -> Dict[str, Any]:
    steps = [s.dict() if hasattr(s, "dict") else s for s in (case.steps or [])]
    doc = {"id": case.id, "suite_id": case.test_suite_id, "name": case.name, "steps": steps,
           "tags": case.tags or [], "priority": case.priority, "code_paths": case.code_paths or []}
    doc["hash"] = content_hash(doc)
    return doc


def validate_doc(doc: Any) -> Optional[str]:
    """Why a submitted document is unusable, or None."""
    if not isinstance(doc, dict):
        return "case document must be an object"
    if doc.get("id") is not None and (not isinstance(doc["id"], int) or isinstance(doc["id"], bool)):
        return "id must be an integer"
    if not isinstance(doc.get("name"), str) or not doc["name"].strip():
        return "name is required"
    if len(doc["name"]) > MAX_NAME_CHARS:
        return f"name is longer than {MAX_NAME_CHARS} characters"
    if isinstance(doc.get("steps"), list) and len(doc["steps"]) > MAX_DOC_STEPS:
        return f"more than {MAX_DOC_STEPS} steps"
    for key in ("tags", "code_paths"):
        if isinstance(doc.get(key), list) and len(doc[key]) > MAX_LIST_ITEMS:
            return f"{key} has more than {MAX_LIST_ITEMS} entries"
    if len(json.dumps(doc, default=str)) > MAX_DOC_BYTES:
        return f"case document exceeds {MAX_DOC_BYTES // 1024} KB"
    if not isinstance(doc.get("steps", []), list) or not all(isinstance(s, dict) for s in doc.get("steps") or []):
        return "steps must be a list of step objects"
    for key in ("tags", "code_paths"):
        v = doc.get(key)
        if v is not None and (not isinstance(v, list) or not all(isinstance(x, str) for x in v)):
            return f"{key} must be a list of strings"
    if doc.get("priority") is not None and not isinstance(doc["priority"], str):
        return "priority must be a string or null"
    for key in ("suite_id", "base_suite_id"):
        v = doc.get(key)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool)):
            return f"{key} must be an integer"
    return None


def changed_fields(server_doc: Dict[str, Any], local_doc: Dict[str, Any]) -> Dict[str, Any]:
    a, b = content_of(server_doc), content_of(local_doc)
    return {k: b[k] for k in SYNC_FIELDS if a[k] != b[k]}


def classify(server_doc: Optional[Dict[str, Any]], local_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Decide what one submitted document means against the server copy.

    Returns {"kind": unchanged|update|conflict, ...}. A document whose content
    already equals the server's is `unchanged` even if its base is stale —
    there is nothing to propose and nothing to lose."""
    if server_doc is None:
        return {"kind": "conflict", "reason": "case no longer exists on the server"}
    server_hash = server_doc["hash"]
    local_hash = content_hash(local_doc)
    target_suite = local_doc.get("suite_id") or server_doc["suite_id"]
    moved = target_suite != server_doc["suite_id"]
    if local_hash == server_hash and not moved:
        return {"kind": "unchanged"}
    base_hash = local_doc.get("base_hash")
    if not base_hash:
        return {"kind": "conflict", "reason": "no base_hash: re-pull before editing",
                "server_hash": server_hash}
    base_suite = local_doc.get("base_suite_id")
    if server_hash != base_hash and local_hash != server_hash:
        return {"kind": "conflict", "reason": "changed on the server since it was pulled",
                "server_hash": server_hash, "base_hash": base_hash}
    if moved and base_suite is not None and base_suite != server_doc["suite_id"]:
        return {"kind": "conflict", "reason": "moved on the server since it was pulled",
                "server_hash": server_hash, "base_hash": base_hash}
    return {"kind": "update",
            "changes": changed_fields(server_doc, local_doc) if local_hash != server_hash else {},
            "move_to": target_suite if moved else None,
            "base_hash": server_hash}
