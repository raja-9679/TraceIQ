"""Uploaded HAR files for the `har-replay` step (WP15d, H7).

A HAR is the most credential-dense file a browser produces (every cookie,
every Authorization header, every body). So on upload it is:

* sniffed — it must parse as HAR JSON (log.entries with request url/method
  and response status), whatever the file name or content type says;
* REDACTED with the project's data policy through the backend twin
  (`redaction.redact_har`) before a byte reaches the object store — the
  stored file, the only copy, is the redacted one;
* stored under `har/{project_id}/{uuid}.har`, a prefix derived from the
  project so the project and workspace purges can delete it.

The worker never gets a URL to it: it asks `/api/internal/har-files/{id}`
(worker secret) naming its run and case, and the backend serves the file only
when the HAR, the case and the live run are all in the same project. Dispatch
does the same check early so a wrong id fails the step with a clear message.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from app.services.redaction import RedactionPolicy, redact_har

MAX_HAR_BYTES = 20 * 1024 * 1024
MAX_ENTRIES = 20_000
MAX_HOSTS = 50
STEP_TYPE = "har-replay"


class HarError(ValueError):
    pass


def object_key(project_id: int, uid: str) -> str:
    return f"har/{int(project_id)}/{uid}.har"


def project_prefix(project_id: int) -> str:
    return f"har/{int(project_id)}/"


def parse_har(raw: bytes) -> Dict[str, Any]:
    """The parsed HAR, or HarError saying why it is not one."""
    if len(raw) > MAX_HAR_BYTES:
        raise HarError(f"HAR files are limited to {MAX_HAR_BYTES // (1024 * 1024)} MB")
    try:
        text = raw.decode("utf-8-sig")
        doc = json.loads(text)
    except (UnicodeDecodeError, ValueError):
        raise HarError("file is not HAR JSON")
    log = doc.get("log") if isinstance(doc, dict) else None
    entries = log.get("entries") if isinstance(log, dict) else None
    if not isinstance(entries, list):
        raise HarError("file is not a HAR: log.entries is missing")
    if not entries:
        raise HarError("HAR has no entries")
    if len(entries) > MAX_ENTRIES:
        raise HarError(f"HAR has more than {MAX_ENTRIES} entries")
    for i, e in enumerate(entries):
        req = e.get("request") if isinstance(e, dict) else None
        res = e.get("response") if isinstance(e, dict) else None
        if not (isinstance(req, dict) and isinstance(req.get("url"), str)
                and isinstance(req.get("method"), str)
                and isinstance(res, dict) and isinstance(res.get("status"), int)
                and not isinstance(res.get("status"), bool)):
            raise HarError(f"entry {i} is not a HAR request/response pair")
    return doc


def hosts_of(doc: Dict[str, Any]) -> List[str]:
    seen: List[str] = []
    for e in doc["log"]["entries"]:
        try:
            host = urlsplit(e["request"]["url"]).hostname
        except ValueError:
            host = None
        if host and host not in seen:
            seen.append(host)
            if len(seen) >= MAX_HOSTS:
                break
    return seen


def prepare_upload(raw: bytes, data_policy: Optional[dict]) -> Tuple[bytes, Dict[str, Any]]:
    """(redacted bytes to store, summary). Raises HarError."""
    doc = parse_har(raw)
    redacted = redact_har(doc, RedactionPolicy.from_data_policy(data_policy))
    body = json.dumps(redacted, separators=(",", ":")).encode("utf-8")
    return body, {
        "entry_count": len(redacted["log"]["entries"]),
        "hosts": hosts_of(redacted),
        "size_bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def har_ids(steps: Any) -> List[int]:
    out: List[int] = []
    for step in steps or []:
        if isinstance(step, dict) and step.get("type") == STEP_TYPE:
            hid = (step.get("params") or {}).get("har_id")
            if isinstance(hid, int) and not isinstance(hid, bool):
                out.append(hid)
            elif isinstance(hid, str) and hid.isdigit():
                out.append(int(hid))
    return out


def resolve_steps(steps: List[Any], project_id: Optional[int],
                  lookup) -> List[Any]:
    """Dispatch-time check: each har-replay step's HAR must belong to the
    case's project. Adds `params.har` = {id, name, sha256} or `params.har_error`
    (the worker fails the step with it). `lookup(project_id, ids)` returns
    {id: (name, sha256)} for the ids that ARE in that project."""
    ids = har_ids(steps)
    if not ids:
        return steps
    try:
        found = lookup(project_id, ids) if project_id is not None else {}
    except Exception as exc:  # noqa: BLE001 — never block dispatch; fail the step instead
        print(f"[har_files] lookup failed for project {project_id}: {exc}")
        found = {}
    out = []
    for step in steps:
        if not (isinstance(step, dict) and step.get("type") == STEP_TYPE):
            out.append(step)
            continue
        params = {k: v for k, v in (step.get("params") or {}).items() if k not in ("har", "har_error")}
        raw = params.get("har_id")
        hid = int(raw) if isinstance(raw, int) or (isinstance(raw, str) and raw.isdigit()) else None
        if hid is None:
            params["har_error"] = "har-replay needs params.har_id (an uploaded HAR of this project)"
        elif hid not in found:
            params["har_error"] = f"HAR {hid} is not an uploaded HAR of this project"
        else:
            name, digest = found[hid]
            params["har"] = {"id": hid, "name": name, "sha256": digest}
        out.append({**step, "params": params})
    return out


def lookup_sync(project_id: int, ids: List[int]) -> Dict[int, Tuple[str, str]]:
    from sqlmodel import Session, select
    from app.models import HarArchive
    from app.worker import sync_engine
    with Session(sync_engine) as session:
        rows = session.exec(select(HarArchive).where(
            HarArchive.project_id == project_id, HarArchive.id.in_(ids))).all()
        return {r.id: (r.name, r.sha256) for r in rows}
