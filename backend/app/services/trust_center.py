"""Trust center (WP13b, roadmap E24, decision C3): what a workspace may publish.

Everything public is built by WHITELISTING fields, like share links: a field
added to a model later stays private until someone decides otherwise.

Security posture is aggregate only. Each item is a yes/no, a date or a count
BUCKET - never a finding title, a person, a URL or a count precise enough to
track week by week. An item is shown only when the admin opted in to it AND
the product has data for it; an item without data is left out of the public
page (the admin config shows it as "hidden: no data yet") rather than printed
as "no", which would claim something we do not know.

Adding a source later (e.g. WP13a's controls summary, or restore drills once
they are recorded) is two edits: a fact in `collect_facts` and a
`PostureItem` in `POSTURE_ITEMS` whose `derive` reads it. The public page and
the config UI pick it up from the registry.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional

INCIDENT_STATUSES = ("investigating", "identified", "monitoring", "resolved")
MAX_COMPONENTS = 20
MAX_UPDATES = 100
MAX_BODY = 5000
MAX_INTRO = 4000

# ---------------------------------------------------------------------------
# Security posture
# ---------------------------------------------------------------------------


def count_bucket(n: Optional[int]) -> Optional[str]:
    """A coarse band: the public must not be able to diff exact counts."""
    if n is None:
        return None
    if n <= 0:
        return "None"
    if n < 5:
        return "1-4"
    if n < 20:
        return "5-19"
    return "20 or more"


def _date(v: Any) -> Optional[str]:
    return v.date().isoformat() if isinstance(v, datetime) else None


def _yes_no(v: Any) -> Optional[Dict[str, str]]:
    if v is None:
        return None
    return {"value": "Yes" if v else "No", "state": "good" if v else "attention"}


def _sso(f: Dict[str, Any]):
    return _yes_no(f.get("sso_enforced"))


def _mfa(f: Dict[str, Any]):
    return _yes_no(f.get("mfa_admins"))


def _sse(f: Dict[str, Any]):
    return _yes_no(f.get("encryption_at_rest"))


def _audit(f: Dict[str, Any]):
    at = f.get("audit_verified_at")
    if at is None:
        return None
    if f.get("audit_verified_ok"):
        return {"value": f"Chain verified on {_date(at)}", "state": "good"}
    return {"value": f"Last check on {_date(at)} did not verify", "state": "attention"}


def _findings(f: Dict[str, Any]):
    b = count_bucket(f.get("open_critical_findings"))
    if b is None:
        return None
    return {"value": b, "state": "good" if b == "None" else "attention"}


def _review(f: Dict[str, Any]):
    at = f.get("last_access_review_at")
    return {"value": _date(at), "state": "info"} if at else None


def _drill(f: Dict[str, Any]):
    at = f.get("last_restore_drill_at")
    return {"value": _date(at), "state": "info"} if at else None


def _controls(f: Dict[str, Any]):
    """Aggregate only: how many controls are passing, never which ones or why."""
    c = f.get("controls")
    if not c or not c.get("total"):
        return None
    good = int(c.get("passing", 0)) + int(c.get("excepted", 0))
    frameworks = ", ".join(c.get("frameworks") or [])
    value = f"{good} of {c['total']} passing" + (f" ({frameworks})" if frameworks else "")
    return {"value": value, "state": "ok" if good == c["total"] else "warn"}


@dataclass(frozen=True)
class PostureItem:
    key: str
    label: str
    hint: str            # for the admin: where the answer comes from
    derive: Callable[[Dict[str, Any]], Optional[Dict[str, str]]]


POSTURE_ITEMS: List[PostureItem] = [
    PostureItem("sso_enforced", "Single sign-on enforced",
                "Yes when password login is disabled and OIDC or SAML is configured (instance policy).", _sso),
    PostureItem("mfa_admins", "MFA for administrators",
                "Yes when MFA is required for everyone, or every admin of this workspace has MFA on.", _mfa),
    PostureItem("encryption_at_rest", "Data encrypted at rest (object storage)",
                "Yes when server-side encryption is configured for the object store.", _sse),
    PostureItem("audit_chain", "Tamper-evident audit log",
                "The date of the last audit-chain verification you ran from this page.", _audit),
    PostureItem("critical_findings", "Open critical or high security findings",
                "A count band (None, 1-4, 5-19, 20 or more) across the workspace. Never titles or details.", _findings),
    PostureItem("access_review", "Last access review completed",
                "Sign-off date of the newest access-review campaign.", _review),
    PostureItem("restore_drill", "Last backup restore drill",
                "Date of the newest PASSING restore drill reported by scripts/restore_drill.sh "
                "(instance-wide: one database serves every workspace).", _drill),
    PostureItem("controls", "Compliance controls",
                "How many of this workspace's controls are passing (an active exception counts). "
                "A count only: control names, evidence and reasons are never published.", _controls),
]
POSTURE_KEYS = tuple(i.key for i in POSTURE_ITEMS)


def derive_posture(facts: Dict[str, Any], selected: Optional[Iterable[str]]) -> List[Dict[str, str]]:
    """Public posture: opted-in items that have data, in registry order."""
    chosen = set(selected or ())
    out = []
    for item in POSTURE_ITEMS:
        if item.key not in chosen:
            continue
        v = item.derive(facts)
        if v is None or v.get("value") is None:
            continue
        out.append({"key": item.key, "label": item.label,
                    "value": str(v["value"]), "state": v["state"]})
    return out


def posture_preview(facts: Dict[str, Any], selected: Optional[Iterable[str]]) -> List[Dict[str, Any]]:
    """Admin view: every item, whether it is published and what it would say."""
    chosen = set(selected or ())
    out = []
    for item in POSTURE_ITEMS:
        v = item.derive(facts)
        out.append({"key": item.key, "label": item.label, "hint": item.hint,
                    "published": item.key in chosen, "available": v is not None,
                    "value": v["value"] if v else None, "state": v["state"] if v else None})
    return out


def clean_posture_keys(keys: Any) -> List[str]:
    if not isinstance(keys, list):
        raise ValueError("posture_items must be a list")
    bad = [k for k in keys if k not in POSTURE_KEYS]
    if bad:
        raise ValueError(f"unknown posture item(s): {', '.join(map(str, bad))}")
    return [k for k in POSTURE_KEYS if k in keys]


async def collect_facts(session, workspace_id: int, center: Any) -> Dict[str, Any]:
    """Facts the product already has. None = unknown (the item stays hidden)."""
    from sqlalchemy import func
    from sqlmodel import select

    from app.models import AccessReviewCampaign, Project, SecurityFinding, User, UserWorkspace
    from app.services.access_service import _ROLE_MAP, _workspace_role_level
    from app.services.instance_settings import effective

    def _on(key: str) -> bool:
        return str(effective(key) or "").strip().lower() in ("1", "true", "yes", "on")

    sso_configured = bool(effective("OIDC_ISSUER") or effective("SAML_IDP_METADATA_URL")
                          or effective("SAML_IDP_METADATA_XML") or effective("SAML_IDP_SSO_URL"))
    facts: Dict[str, Any] = {
        "sso_enforced": _on("PASSWORD_LOGIN_DISABLED") and sso_configured,
        "encryption_at_rest": bool(str(effective("MINIO_SSE_ALGORITHM") or "").strip()),
        "audit_verified_at": getattr(center, "audit_verified_at", None),
        "audit_verified_ok": getattr(center, "audit_verified_ok", None),
    }
    # Restore drills (WP13a DrillResult) are instance-wide: the newest PASS.
    from app.services import compliance
    facts["last_restore_drill_at"] = (await compliance._drills(session))[0]
    rows = await compliance.controls_overview(session, workspace_id)
    counts = compliance.status_counts(rows)
    facts["controls"] = ({**counts, "total": len(rows),
                          "frameworks": sorted({r["framework"] for r in rows if r.get("framework")})}
                         if rows else None)

    if _on("MFA_REQUIRED"):
        facts["mfa_admins"] = True
    else:
        admins = []
        for uw in (await session.exec(select(UserWorkspace).where(
                UserWorkspace.workspace_id == workspace_id))).all():
            if await _workspace_role_level(uw, session) >= _ROLE_MAP["admin"]:
                u = await session.get(User, uw.user_id)
                if u is not None and getattr(u, "is_active", True):
                    admins.append(u)
        facts["mfa_admins"] = all(bool(u.mfa_enabled) for u in admins) if admins else None

    projects = select(Project.id).where(Project.workspace_id == workspace_id)
    n = (await session.exec(select(func.count()).select_from(SecurityFinding).where(
        SecurityFinding.project_id.in_(projects),
        SecurityFinding.superseded_by_id.is_(None),
        SecurityFinding.severity.in_(("critical", "high")),
        SecurityFinding.status.in_(("open", "in_progress", "fixed_pending_verification"))))).one()
    facts["open_critical_findings"] = int(n or 0)

    facts["last_access_review_at"] = (await session.exec(
        select(func.max(AccessReviewCampaign.signed_off_at)).where(
            AccessReviewCampaign.workspace_id == workspace_id,
            AccessReviewCampaign.status == "signed_off"))).one()
    return facts


# ---------------------------------------------------------------------------
# Incident posts: escape-first markdown
# ---------------------------------------------------------------------------

_BOLD = re.compile(r"\*\*(\S(?:[^*\n]*?\S)?)\*\*")
_ITALIC = re.compile(r"(?<![*\w])[*_](\S(?:[^*_\n]*?\S)?)[*_](?![*\w])")
_CODE = re.compile(r"`([^`\n]+)`")
_BULLET = re.compile(r"^\s*[-*] ")


def _inline(escaped: str) -> str:
    # Input is already HTML-escaped: these substitutions can only wrap
    # escaped text in the three tags below, never let markup through.
    escaped = _CODE.sub(r"<code>\1</code>", escaped)
    escaped = _BOLD.sub(r"<strong>\1</strong>", escaped)
    return _ITALIC.sub(r"<em>\1</em>", escaped)


def render_markdown_safe(text: Optional[str]) -> str:
    """A tiny markdown subset (paragraphs, line breaks, "- " lists, **bold**,
    *italic*, `code`) rendered AFTER escaping the whole input. No links or
    images: an incident update has no need for them and they are the usual
    way script gets in. Output contains only p, br, ul, li, strong, em, code."""
    if not text:
        return ""
    escaped = html.escape(str(text)[:MAX_BODY].replace("\r\n", "\n"), quote=True)
    blocks = re.split(r"\n\s*\n", escaped.strip())
    out = []
    for block in blocks:
        lines = [ln.rstrip() for ln in block.split("\n") if ln.strip()]
        if lines and all(_BULLET.match(ln) for ln in lines):
            items = "".join("<li>" + _inline(_BULLET.sub("", ln)) + "</li>" for ln in lines)
            out.append(f"<ul>{items}</ul>")
        elif lines:
            out.append("<p>" + "<br>".join(_inline(ln) for ln in lines) + "</p>")
    return "".join(out)


def clean_components(v: Any) -> List[str]:
    if v is None:
        return []
    if not isinstance(v, list):
        raise ValueError("components must be a list of names")
    out = []
    for c in v:
        name = str(c).strip()[:80]
        if name and name not in out:
            out.append(name)
    if len(out) > MAX_COMPONENTS:
        raise ValueError(f"at most {MAX_COMPONENTS} components")
    return out


def _iso(v: Any) -> Optional[str]:
    return v.isoformat() if isinstance(v, datetime) else (v if isinstance(v, str) else None)


def public_incident(post: Any) -> Dict[str, Any]:
    updates = sorted(post.updates or [], key=lambda u: str(u.get("at") or ""), reverse=True)
    return {
        "id": post.id, "title": post.title, "status": post.status,
        "components": [str(c) for c in (post.components or [])],
        "started_at": _iso(post.published_at or post.created_at),
        "resolved_at": _iso(post.resolved_at),
        "updates": [{"at": u.get("at"), "status": u.get("status"),
                     "body_html": render_markdown_safe(u.get("body"))} for u in updates],
    }


# ---------------------------------------------------------------------------
# Status (reduced) and the whole public page
# ---------------------------------------------------------------------------


def public_monitor(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {"name": entry.get("name"), "state": entry.get("state"),
            "uptime_24h": entry.get("uptime_24h"), "uptime_7d": entry.get("uptime_7d")}


def overall_state(monitors: List[Dict[str, Any]]) -> str:
    if not monitors:
        return "unknown"
    if any(m.get("state") == "down" for m in monitors):
        return "degraded" if any(m.get("state") == "up" for m in monitors) else "down"
    return "operational"


def public_document(doc: Any) -> Dict[str, Any]:
    return {"id": doc.id, "title": doc.title, "description": doc.description}


def public_page(center: Any, monitors: List[Dict[str, Any]], posts: Iterable[Any],
                posture: List[Dict[str, str]], documents: Iterable[Any]) -> Dict[str, Any]:
    """Callers pass only published posts and listed documents; this decides
    which fields of each leave the server."""
    mons = [public_monitor(m) for m in monitors]
    return {
        "title": center.title,
        "intro": center.intro or "",
        "contact_email": center.contact_email or None,
        "status": ({"overall": overall_state(mons), "monitors": mons}
                   if center.status_page_ids else None),
        "incidents": [public_incident(p) for p in posts],
        "posture": posture,
        "documents": [public_document(d) for d in documents],
        "access_requests": bool(center.allow_access_requests),
        "generated_at": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# Gated documents: what may be uploaded
# ---------------------------------------------------------------------------

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024

# Extension -> content type served. Office formats are zip containers, so
# their magic is the zip header; the extension tells which one it is.
_OFFICE = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def sniff_document(head: bytes, filename: str) -> Optional[str]:
    """Content type from the bytes (and, for zip-based Office files, the
    extension). None = not an accepted document. HTML/SVG are refused
    outright: served from the object store they would run script."""
    name = (filename or "").lower()
    ext = name[name.rfind("."):] if "." in name else ""
    if head.startswith(b"%PDF-"):
        return "application/pdf" if ext in ("", ".pdf") else None
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png" if ext == ".png" else None
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg" if ext in (".jpg", ".jpeg") else None
    if head.startswith(b"PK\x03\x04"):
        return _OFFICE.get(ext)
    if ext in (".txt", ".md"):
        if b"\x00" in head:
            return None
        try:
            sample = head.decode("utf-8")
        except UnicodeDecodeError:
            # A multi-byte character cut at the sniff boundary is fine.
            try:
                sample = head[:-4].decode("utf-8")
            except UnicodeDecodeError:
                return None
        if re.search(r"<\s*(script|html|svg|iframe)", sample, re.I):
            return None
        return "text/plain"
    return None


def safe_filename(name: Optional[str]) -> str:
    base = (name or "document").replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .") or "document"
    return base[:120]
