"""Compliance evidence pack (WP13a, decision C1).

One zip an admin hands an auditor for a workspace and a date range:

    README.txt
    audit/audit-log.csv                 the workspace's audit rows in the range (chain order)
    audit/chain-verification.json       the hash-chain check, at generation time
    access-reviews/current-access-report.{csv,html}
    access-reviews/campaign-<id>.{csv,html}   reviews started or signed off in the range
    controls/controls.json + summary.html     status, evidence results, exceptions
    data-policies/projects.json         effective data policy per project
    security/project-<id>.sarif         current findings per project (SARIF 2.1.0)
    security/finding-sla.json           SLA days + open/overdue counts per project
    restore-drills/history.json         drills reported in the range (verdicts only)
    manifest.json                       every file above with SHA-256 + size, signed

Integrity: `manifest.json` lists each file's SHA-256 and size and carries an
HMAC-SHA256 over its own canonical JSON (minus the signature block), keyed by
a key derived for this purpose only (`secrets.purpose_keys`). Change a byte of
any file and its hash stops matching; change the manifest to match and the
signature stops verifying; sign it with another instance's key and the
signature does not verify here.

Memory stays bounded: the zip is written to a spooled temp file (spills to
disk past a few MB), audit rows are read in id batches and written per batch,
and only one project's findings are held at a time.

Every query here filters by the pack's workspace. The audit chain is global
(each row links to the previous row of ANY workspace), so the chain result is
reported for the whole table, but which other workspace broke it is never
included — see compliance.verify_audit_chain.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import html
import io
import json
import posixpath
import zipfile
from datetime import datetime
from typing import Any, BinaryIO, Dict, List, Optional, Sequence, Tuple

FORMAT = "traceiq-evidence-pack/v1"
SIGNATURE_ALGORITHM = "HMAC-SHA256"
KEY_PURPOSE = "traceiq/evidence-pack-manifest/v1"
MANIFEST_NAME = "manifest.json"

#: Verify-side limits: a zip is attacker-supplied input.
MAX_VERIFY_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_VERIFY_ENTRIES = 10_000
MAX_VERIFY_UNCOMPRESSED = 4 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024

AUDIT_COLUMNS = (
    "id", "timestamp", "workspace_id", "entity_type", "entity_id", "action",
    "user_id", "actor_type", "actor_label", "ip_address", "user_agent",
    "changes", "row_hash",
)


# ---------------------------------------------------------------------------
# Manifest: canonical form, signing, verification (pure)
# ---------------------------------------------------------------------------

def canonical(manifest: Dict[str, Any]) -> bytes:
    body = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str,
                      ensure_ascii=False).encode("utf-8")


def sign_manifest(manifest: Dict[str, Any], key: bytes) -> Dict[str, Any]:
    from app.core.secrets import key_id
    signed = {k: v for k, v in manifest.items() if k != "signature"}
    signed["signature"] = {
        "algorithm": SIGNATURE_ALGORITHM,
        "key_id": key_id(key),
        "value": hmac.new(key, canonical(signed), hashlib.sha256).hexdigest(),
    }
    return signed


def check_signature(manifest: Dict[str, Any], keys: Sequence[bytes]) -> Tuple[bool, Optional[int]]:
    """(valid, index of the key that verified it: 0 = current, >0 = previous)."""
    sig = manifest.get("signature") if isinstance(manifest, dict) else None
    if not isinstance(sig, dict) or sig.get("algorithm") != SIGNATURE_ALGORITHM:
        return False, None
    value = sig.get("value")
    if not isinstance(value, str):
        return False, None
    body = canonical(manifest)
    for index, key in enumerate(keys):
        expected = hmac.new(key, body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, value):
            return True, index
    return False, None


def _safe_member(path: Any) -> bool:
    if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path:
        return False
    norm = posixpath.normpath(path)
    return norm == path and not norm.startswith("..") and "/../" not in f"/{norm}/"


def _find_manifest(names: Sequence[str]) -> Optional[str]:
    candidates = [n for n in names if posixpath.basename(n) == MANIFEST_NAME and n.count("/") <= 1]
    return min(candidates, key=len) if candidates else None


def verify_pack(fileobj: BinaryIO, keys: Sequence[bytes],
                manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Recompute every listed file's hash and check the manifest signature.

    `manifest` overrides the one inside the zip (an auditor may keep the
    manifest separately from the archive). Never extracts anything to disk.
    """
    report: Dict[str, Any] = {"verified": False, "signature_valid": False, "key": None,
                              "files_checked": 0, "mismatched": [], "missing": [],
                              "unexpected": [], "errors": []}
    try:
        zf = zipfile.ZipFile(fileobj)
    except (zipfile.BadZipFile, OSError, ValueError):
        report["errors"].append("not a zip archive")
        return report
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_VERIFY_ENTRIES:
            report["errors"].append(f"too many entries (limit {MAX_VERIFY_ENTRIES})")
            return report
        if sum(i.file_size for i in infos) > MAX_VERIFY_UNCOMPRESSED:
            report["errors"].append("archive expands beyond the verification limit")
            return report
        names = [i.filename for i in infos]
        manifest_entry = _find_manifest(names)
        root = (posixpath.dirname(manifest_entry) + "/") if manifest_entry and "/" in manifest_entry else ""
        if manifest is None:
            if not manifest_entry:
                report["errors"].append("manifest.json not found in the archive")
                return report
            info = zf.getinfo(manifest_entry)
            if info.file_size > MAX_MANIFEST_BYTES:
                report["errors"].append("manifest.json is too large")
                return report
            try:
                manifest = json.loads(zf.read(manifest_entry).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                report["errors"].append("manifest.json is not valid JSON")
                return report
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            report["errors"].append(f"manifest is not a {FORMAT} manifest")
            return report

        ok, key_index = check_signature(manifest, keys)
        report["signature_valid"] = ok
        report["key"] = None if key_index is None else ("current" if key_index == 0 else "previous")
        report["signed_key_id"] = (manifest.get("signature") or {}).get("key_id")
        for field in ("pack_id", "workspace_id", "workspace_name", "period", "generated_at",
                      "generated_by"):
            report[field] = manifest.get(field)

        listed = manifest.get("files")
        if not isinstance(listed, list):
            report["errors"].append("manifest has no file list")
            return report
        present = set(names)
        expected_names = set()
        for entry in listed:
            path = entry.get("path") if isinstance(entry, dict) else None
            if not _safe_member(path):
                report["errors"].append(f"unsafe path in manifest: {str(path)[:100]}")
                continue
            member = root + path
            expected_names.add(member)
            if member not in present:
                report["missing"].append(path)
                continue
            digest, size = hashlib.sha256(), 0
            with zf.open(member) as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            report["files_checked"] += 1
            if digest.hexdigest() != entry.get("sha256") or size != entry.get("size"):
                report["mismatched"].append(path)
        report["unexpected"] = sorted(
            (n[len(root):] if n.startswith(root) else n)
            for n in present - expected_names - {manifest_entry})
    report["verified"] = (report["signature_valid"] and not report["mismatched"]
                          and not report["missing"] and not report["unexpected"]
                          and not report["errors"])
    return report


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

class PackWriter:
    """Zip writer that records every file's SHA-256 and size as it goes."""

    def __init__(self, fileobj: BinaryIO, root: str):
        self.zf = zipfile.ZipFile(fileobj, "w", compression=zipfile.ZIP_DEFLATED)
        self.root = root.rstrip("/") + "/"
        self.files: List[Dict[str, Any]] = []

    def add(self, path: str, data: bytes) -> None:
        self.zf.writestr(self.root + path, data)
        self.files.append({"path": path, "sha256": hashlib.sha256(data).hexdigest(),
                           "size": len(data)})

    def add_json(self, path: str, obj: Any) -> None:
        self.add(path, (json.dumps(obj, indent=2, sort_keys=True, default=_jsonable) + "\n").encode())

    def stream(self, path: str):
        """Context manager: write chunks of bytes, hashed as they go."""
        writer = self

        class _Stream:
            def __enter__(self):
                self.digest, self.size = hashlib.sha256(), 0
                self.fh = writer.zf.open(writer.root + path, "w", force_zip64=True)
                return self

            def write(self, chunk: bytes) -> None:
                self.digest.update(chunk)
                self.size += len(chunk)
                self.fh.write(chunk)

            def __exit__(self, *exc):
                self.fh.close()
                writer.files.append({"path": path, "sha256": self.digest.hexdigest(),
                                     "size": self.size})
                return False
        return _Stream()

    def finish(self, manifest: Dict[str, Any]) -> None:
        self.zf.writestr(self.root + MANIFEST_NAME,
                         (json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n").encode())
        self.zf.close()


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds") + "Z"
    return str(value)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return _jsonable(value) if value else None


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in (text or "").lower()).strip("-")[:40] or "project"


def readme(workspace: str, period: Dict[str, str], generated_at: str) -> bytes:
    return f"""TraceIQ compliance evidence pack
================================

Workspace: {workspace}
Period:    {period['start']} to {period['end']} (UTC)
Generated: {generated_at}

manifest.json lists every file in this archive with its SHA-256 and size, and
carries an HMAC-SHA256 signature over the manifest made with this TraceIQ
instance's evidence-pack key. To check the archive has not been altered, upload
it on the Evidence packs page (or POST /api/evidence-packs/verify). Any edited,
added or removed file, or an edited manifest, fails verification.

You can also check hashes yourself, e.g. `sha256sum audit/audit-log.csv`.

audit/audit-log.csv is in chain order (ascending id). The hash chain spans the
whole instance, so chain-verification.json reports on the full chain as of
generation time; the CSV alone cannot be re-verified without the rows of
other workspaces, which are deliberately not included.

Restore drills are instance-wide; history.json shows each drill's verdicts,
not its details (those contain instance-wide figures).
""".encode()


def controls_html(workspace: str, rows: List[Dict[str, Any]], generated_at: str) -> bytes:
    e = html.escape
    body = []
    for r in rows:
        results = "".join(f"<li><b>{e(x['status'])}</b> — {e(x['label'])}: {e(x['reason'])}</li>"
                          for x in r["results"]) or "<li>no automated sources</li>"
        exc = r.get("active_exception")
        exc_html = (f"<div class='exc'>Exception until {e(_iso(exc['expires_at']) or '')}: "
                    f"{e(exc['reason'])} (approved by {e(exc['approved_by'] or '?')})</div>") if exc else ""
        body.append(
            f"<tr><td>{e(r['framework'])}</td><td>{e(r['reference'])}</td><td>{e(r['title'])}</td>"
            f"<td>{e(r['owner'] or '—')}</td><td class='s-{e(r['status'])}'>{e(r['status'].replace('_', ' '))}</td>"
            f"<td>{e(r['status_reason'])}<ul>{results}</ul>{exc_html}</td></tr>")
    rows_html = "".join(body) or "<tr><td colspan='6'>No controls defined.</td></tr>"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Controls — {e(workspace)}</title>
<style>
body{{font:12px/1.45 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#111;margin:24px}}
h1{{font-size:18px;margin:0 0 4px}} .meta{{color:#555;margin-bottom:12px}}
table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #ccc;padding:4px 6px;text-align:left;vertical-align:top}}
th{{background:#f3f4f6}} ul{{margin:4px 0 0 16px;padding:0}} .exc{{margin-top:4px;color:#7a5b00}}
.s-passing{{color:#166534;font-weight:600}} .s-failing{{color:#b91c1c;font-weight:600}}
.s-stale{{color:#92400e;font-weight:600}} .s-excepted{{color:#6b21a8;font-weight:600}}
@page{{size:A4 landscape;margin:12mm}}
</style></head><body>
<h1>Controls — {e(workspace)}</h1><div class="meta">Status as of {e(generated_at)}</div>
<table><thead><tr><th>Framework</th><th>Ref</th><th>Control</th><th>Owner</th><th>Status</th><th>Evidence</th></tr></thead>
<tbody>{rows_html}</tbody></table></body></html>""".encode()


async def _write_audit_csv(session, writer: PackWriter, workspace_id: int,
                           start: datetime, end: datetime, batch: int = 2000) -> int:
    from sqlmodel import select
    from app.models import AuditLog
    count, last_id = 0, 0
    with writer.stream("audit/audit-log.csv") as out:
        buf = io.StringIO()
        csv.writer(buf).writerow(AUDIT_COLUMNS)
        out.write(buf.getvalue().encode())
        while True:
            rows = (await session.exec(select(AuditLog).where(
                AuditLog.workspace_id == workspace_id, AuditLog.timestamp >= start,
                AuditLog.timestamp <= end, AuditLog.id > last_id)
                .order_by(AuditLog.id).limit(batch))).all()
            if not rows:
                break
            buf = io.StringIO()
            w = csv.writer(buf)
            for row in rows:
                w.writerow([getattr(row, c, "") for c in AUDIT_COLUMNS])
            out.write(buf.getvalue().encode())
            count += len(rows)
            last_id = rows[-1].id
            for row in rows:          # keep the identity map from growing with the export
                session.expunge(row)
    return count


async def build_pack(session, pack: Any, fileobj: BinaryIO, *,
                     generated_by: Dict[str, Any], key: bytes,
                     now: Optional[datetime] = None) -> Dict[str, Any]:
    """Write the pack zip into `fileobj`; returns the signed manifest."""
    from sqlmodel import select
    from app.api.access_reviews import _campaign_out
    from app.models import AccessReviewCampaign, DrillResult, Project, SecurityFinding, Workspace
    from app.services import access_review, compliance, sarif
    from app.services import finding_lifecycle as lifecycle
    from app.services.data_policy import describe_for_project

    now = now or datetime.utcnow()
    ws_id = pack.workspace_id
    workspace = await session.get(Workspace, ws_id)
    ws_name = workspace.name if workspace else f"workspace {ws_id}"
    start, end = pack.period_start, pack.period_end
    period = {"start": _iso(start), "end": _iso(end)}
    generated_at = _iso(now)
    root = f"traceiq-evidence-ws{ws_id}-{now:%Y%m%d}-{pack.id}"
    writer = PackWriter(fileobj, root)

    writer.add("README.txt", readme(ws_name, period, generated_at))

    # --- audit -------------------------------------------------------------
    exported = await _write_audit_csv(session, writer, ws_id, start, end)
    chain = await compliance.verify_audit_chain(session, ws_id)
    writer.add_json("audit/chain-verification.json", {
        **chain, "checked_at": generated_at, "scope": "entire instance audit chain",
        "workspace_rows_exported": exported, "period": period})

    # --- access reviews ----------------------------------------------------
    report = await access_review.build_report(session, ws_id)
    items = access_review.items_from_report(report)
    writer.add("access-reviews/current-access-report.csv", access_review.render_csv(items).encode())
    writer.add("access-reviews/current-access-report.html", access_review.render_html(
        f"Access report — {ws_name}", items,
        meta={"scope": "workspace", "workspace": ws_name, "generated_at": now}).encode())
    campaigns = (await session.exec(select(AccessReviewCampaign).where(
        AccessReviewCampaign.workspace_id == ws_id, AccessReviewCampaign.created_at <= end)
        .order_by(AccessReviewCampaign.id))).all()
    included_campaigns = []
    for c in campaigns:
        if c.created_at < start and not (c.signed_off_at and c.signed_off_at >= start):
            continue
        out = await _campaign_out(session, c)
        writer.add(f"access-reviews/campaign-{c.id}.csv", access_review.render_csv(out["items"]).encode())
        writer.add(f"access-reviews/campaign-{c.id}.html", access_review.render_html(c.name, out["items"], meta={
            "scope": out["scope"], "status": c.status, "started": c.created_at,
            "started_by": out["created_by"], "signed_off_by": out["signed_off_by"],
            "signed_off_at": c.signed_off_at, "sign_off_note": c.sign_off_note}).encode())
        included_campaigns.append({"id": c.id, "name": c.name, "status": c.status,
                                   "signed_off_at": c.signed_off_at})

    # --- controls ----------------------------------------------------------
    rows = await compliance.controls_overview(session, ws_id, now=now, with_detail=True)
    writer.add_json("controls/controls.json", {
        "workspace": ws_name, "evaluated_at": generated_at,
        "counts": compliance.status_counts(rows), "controls": rows})
    writer.add("controls/summary.html", controls_html(ws_name, rows, generated_at))

    # --- data policies + security -----------------------------------------
    try:
        from app.services.instance_settings import effective
        ceiling = effective("MAX_CAPTURE_LEVEL")
    except Exception:  # noqa: BLE001
        ceiling = None
    projects = (await session.exec(select(Project).where(Project.workspace_id == ws_id)
                                   .order_by(Project.id))).all()
    project_meta = [(p.id, p.name) for p in projects]
    writer.add_json("data-policies/projects.json", [
        {"project_id": p.id, "project": p.name, **describe_for_project(p, ceiling=ceiling)}
        for p in projects])

    sla = lifecycle.resolve_sla(workspace.finding_sla_days if workspace else None)
    sla_projects = []
    for pid, name in project_meta:
        findings = (await session.exec(select(SecurityFinding).where(
            SecurityFinding.project_id == pid,
            SecurityFinding.superseded_by_id == None)  # noqa: E711
            .order_by(SecurityFinding.id.desc()).limit(5000))).all()
        writer.add_json(f"security/project-{pid}-{_slug(name)}.sarif",
                        sarif.build_sarif(findings, automation_id=f"traceiq/project/{pid}"))
        open_by: Dict[str, int] = {}
        overdue_by: Dict[str, int] = {}
        for f in findings:
            if (f.status or "open") in lifecycle.ACTIONABLE:
                open_by[f.severity] = open_by.get(f.severity, 0) + 1
                if lifecycle.is_overdue(f, now):
                    overdue_by[f.severity] = overdue_by.get(f.severity, 0) + 1
        sla_projects.append({"project_id": pid, "project": name, "open_by_severity": open_by,
                             "overdue_by_severity": overdue_by})
        for f in findings:
            session.expunge(f)
    writer.add_json("security/finding-sla.json", {
        "sla_days": sla, "evaluated_at": generated_at, "projects": sla_projects})

    # --- restore drills ----------------------------------------------------
    drills = (await session.exec(select(DrillResult).where(
        DrillResult.ran_at >= start, DrillResult.ran_at <= end)
        .order_by(DrillResult.ran_at.desc()).limit(500))).all()
    writer.add_json("restore-drills/history.json", {
        "scope": "instance-wide (a drill restores the whole database)", "period": period,
        "drills": [compliance.drill_view(d, full=False) for d in drills]})

    manifest = sign_manifest({
        "format": FORMAT, "pack_id": pack.id, "workspace_id": ws_id, "workspace_name": ws_name,
        "period": period, "generated_at": generated_at, "generated_by": generated_by,
        "contents": {"audit_rows": exported, "access_review_campaigns": included_campaigns,
                     "controls": len(rows), "projects": len(project_meta), "restore_drills": len(drills)},
        "files": sorted(writer.files, key=lambda f: f["path"]),
    }, key)
    writer.finish(manifest)
    return manifest
