"""Suite tree <-> directory of JSON files (`traceiq pull` / `traceiq push`).

Layout: one file per case, `<sub-suite dirs>/<case-slug>.<id>.json`, keys
sorted and two-space indented so a diff shows exactly what changed. Each file
records where it came from under `_traceiq` (case id, the server's content
hash at pull time, the suite it was in); `push` sends those back so the server
can refuse to overwrite a case someone changed in the UI meanwhile.
`.traceiq-sync.json` in the root remembers the suite, the suite->directory map
(so a new file's directory decides its suite) and every pulled case, which is
how a deleted file becomes a proposed deletion.

New case: add a file without `_traceiq` (and without `id`); it goes to the
suite of the directory it is in, or `suite_id` if set.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MANIFEST = ".traceiq-sync.json"
FIELDS = ("name", "steps", "tags", "priority", "code_paths")


def slug(text: str, fallback: str = "case") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:60].strip("-") or fallback


def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def content_hash(doc: Dict[str, Any]) -> str:
    """Mirror of backend app.services.case_sync.content_hash — keep identical."""
    content = {"name": doc.get("name") or "", "steps": doc.get("steps") or [],
               "tags": doc.get("tags") or [], "priority": doc.get("priority"),
               "code_paths": doc.get("code_paths") or []}
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def suite_dirs(export: Dict[str, Any]) -> Dict[int, str]:
    """suite id -> directory relative to the sync root ('' for the root)."""
    root = export["root_suite_id"]
    suites = {s["id"]: s for s in export.get("suites") or []}
    names: Dict[Tuple[Optional[int], str], List[int]] = {}
    for s in suites.values():
        if s["id"] != root:
            names.setdefault((s.get("parent_id"), slug(s["name"], "suite")), []).append(s["id"])

    def own_name(s: Dict[str, Any]) -> str:
        base = slug(s["name"], "suite")
        return base if len(names[(s.get("parent_id"), base)]) == 1 else f"{base}-{s['id']}"

    out: Dict[int, str] = {root: ""}

    def resolve(sid: int, depth: int = 0) -> str:
        if sid in out:
            return out[sid]
        s = suites[sid]
        parent = s.get("parent_id")
        if parent not in suites or depth > 50:
            out[sid] = own_name(s)
        else:
            prefix = resolve(parent, depth + 1)
            out[sid] = f"{prefix}/{own_name(s)}" if prefix else own_name(s)
        return out[sid]

    for sid in suites:
        resolve(sid)
    return out


def case_file(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: doc.get(k) for k in FIELDS}
    out["suite_id"] = doc["suite_id"]
    out["_traceiq"] = {"id": doc["id"], "base_hash": doc["hash"], "base_suite_id": doc["suite_id"]}
    return out


def plan_pull(export: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """(relative path -> file text, manifest) for an export."""
    dirs = suite_dirs(export)
    files: Dict[str, str] = {}
    cases: Dict[str, Any] = {}
    for doc in export.get("cases") or []:
        d = dirs.get(doc["suite_id"], "")
        rel = f"{d}/{slug(doc['name'])}.{doc['id']}.json" if d else f"{slug(doc['name'])}.{doc['id']}.json"
        files[rel] = dumps(case_file(doc))
        cases[str(doc["id"])] = {"path": rel, "hash": doc["hash"], "suite_id": doc["suite_id"]}
    manifest = {"format": export.get("format", 1), "project_id": export.get("project_id"),
                "root_suite_id": export["root_suite_id"],
                "suites": {d: sid for sid, d in sorted(dirs.items())}, "cases": cases}
    return files, manifest


def read_manifest(root: Path) -> Dict[str, Any]:
    path = root / MANIFEST
    if not path.exists():
        raise FileNotFoundError(f"{path} not found — run `traceiq pull <suite> --dir {root}` first")
    return json.loads(path.read_text(encoding="utf-8"))


def local_files(root: Path) -> Dict[str, Dict[str, Any]]:
    """relative path -> parsed JSON for every case file under root."""
    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted(root.rglob("*.json")):
        rel = path.relative_to(root).as_posix()
        if rel == MANIFEST or any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        out[rel] = json.loads(path.read_text(encoding="utf-8"))
    return out


def locally_modified(root: Path, manifest: Dict[str, Any]) -> List[str]:
    """Tracked files whose content no longer matches what was pulled."""
    changed = []
    for info in (manifest.get("cases") or {}).values():
        path = root / info["path"]
        if not path.exists():
            changed.append(info["path"])
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            changed.append(info["path"])
            continue
        if content_hash(doc) != info["hash"] or doc.get("suite_id", info["suite_id"]) != info["suite_id"]:
            changed.append(info["path"])
    return changed


def build_push(root: Path, manifest: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """(request body for POST /suites/{id}/sync, local problems)."""
    problems: List[str] = []
    dir_suites = {d: sid for d, sid in (manifest.get("suites") or {}).items()}
    cases: List[Dict[str, Any]] = []
    present = set()
    for rel, doc in local_files(root).items():
        if not isinstance(doc, dict):
            problems.append(f"{rel}: not a JSON object")
            continue
        meta = doc.get("_traceiq") if isinstance(doc.get("_traceiq"), dict) else {}
        item = {k: doc.get(k) for k in FIELDS if k in doc}
        if meta.get("id") is not None:
            item.update(id=meta["id"], base_hash=meta.get("base_hash"), base_suite_id=meta.get("base_suite_id"))
            item["suite_id"] = doc.get("suite_id", meta.get("base_suite_id"))
            present.add(str(meta["id"]))
        else:
            parent = str(Path(rel).parent.as_posix())
            parent = "" if parent == "." else parent
            item["suite_id"] = doc.get("suite_id") or dir_suites.get(parent) or manifest["root_suite_id"]
        cases.append(item)
    deleted = [{"id": int(cid), "base_hash": info["hash"]}
               for cid, info in sorted((manifest.get("cases") or {}).items(), key=lambda kv: int(kv[0]))
               if cid not in present]
    return {"cases": cases, "deleted": deleted}, problems
