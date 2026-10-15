"""Quarterly access review: who can do what, and the evidence that someone looked (WP7).

The report answers the auditor's question for one workspace (workspace admin)
or the whole instance (instance admin): every person with access — their
workspace role, teams, project roles and how each project role was obtained —
plus every live API key, with the facts a reviewer needs to decide: last login,
MFA, whether they sign in through SSO or with a password, who created a key and
when it was last used.

A *campaign* freezes that report into reviewable items. A reviewer marks each
keep/revoke with a note; sign-off records reviewer + timestamp in the audit
trail. Revocations are a separate, explicit step after sign-off — a review that
silently removed access the moment someone clicked "revoke" would make the
review form the most dangerous page in the product.

Builders are async (API path); the CSV/HTML renderers are pure.
"""
from __future__ import annotations

import csv
import html
import io
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import func
from sqlmodel import select

SSO_METHODS = {"sso_oidc": "sso (oidc)", "sso_saml": "sso (saml)", "ldap": "ldap"}
PASSWORD_METHODS = {"password", "mfa_totp", "mfa_enrollment"}


def auth_method(last_method: Optional[str], *, scim_managed: bool, has_login: bool) -> str:
    if last_method in SSO_METHODS:
        return SSO_METHODS[last_method]
    if last_method in PASSWORD_METHODS:
        return "password"
    if not has_login:
        return "never signed in" + (" (scim-provisioned)" if scim_managed else "")
    return last_method or "unknown"


async def _role_names(session) -> Dict[int, str]:
    from app.models import Role
    return {r.id: r.name for r in (await session.exec(select(Role))).all()}


async def _last_logins(session, user_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """Latest successful login audit row per user: when, and by which method."""
    from app.models import AuditLog
    if not user_ids:
        return {}
    latest = (await session.exec(
        select(func.max(AuditLog.id)).where(AuditLog.action == "login",
                                            AuditLog.user_id.in_(user_ids))
        .group_by(AuditLog.user_id))).all()
    if not latest:
        return {}
    rows = (await session.exec(select(AuditLog).where(AuditLog.id.in_(list(latest))))).all()
    return {r.user_id: {"at": r.timestamp, "method": (r.changes or {}).get("method")} for r in rows}


async def build_report(session, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """The live access report. `workspace_id=None` → instance-wide."""
    from app.models import (
        ApiKey, Project, Team, TeamProjectAccess, User, UserProjectAccess, UserTeam,
        UserWorkspace, Workspace,
    )
    roles = await _role_names(session)

    ws_q = select(Workspace)
    if workspace_id is not None:
        ws_q = ws_q.where(Workspace.id == workspace_id)
    workspaces = {w.id: w for w in (await session.exec(ws_q)).all()}
    ws_ids = list(workspaces)
    projects = {p.id: p for p in (await session.exec(
        select(Project).where(Project.workspace_id.in_(ws_ids)))).all()} if ws_ids else {}
    teams = {t.id: t for t in (await session.exec(
        select(Team).where(Team.workspace_id.in_(ws_ids)))).all()} if ws_ids else {}

    memberships = (await session.exec(select(UserWorkspace).where(
        UserWorkspace.workspace_id.in_(ws_ids)))).all() if ws_ids else []
    team_links = (await session.exec(select(UserTeam).where(
        UserTeam.team_id.in_(list(teams))))).all() if teams else []
    direct = (await session.exec(select(UserProjectAccess).where(
        UserProjectAccess.project_id.in_(list(projects))))).all() if projects else []
    team_access = (await session.exec(select(TeamProjectAccess).where(
        TeamProjectAccess.project_id.in_(list(projects))))).all() if projects else []

    user_ids = {m.user_id for m in memberships} | {t.user_id for t in team_links} \
        | {d.user_id for d in direct}
    if workspace_id is None:
        # Instance-wide also lists people with no workspace at all — an account
        # nobody can see from any workspace is exactly what a review should find.
        user_ids |= set((await session.exec(select(User.id))).all())
    users = {u.id: u for u in (await session.exec(
        select(User).where(User.id.in_(list(user_ids))))).all()} if user_ids else {}
    logins = await _last_logins(session, list(users))

    def level(row) -> str:
        if row.role_id and row.role_id in roles:
            return roles[row.role_id]
        return row.access_level if hasattr(row, "access_level") else (row.role or "member")

    per_user: Dict[int, Dict[int, Dict[str, Any]]] = {}

    def slot(uid: int, wid: int) -> Dict[str, Any]:
        return per_user.setdefault(uid, {}).setdefault(wid, {
            "workspace_id": wid, "workspace": workspaces[wid].name,
            "role": None, "teams": [], "projects": []})

    for m in memberships:
        slot(m.user_id, m.workspace_id)["role"] = (
            roles.get(m.role_id) if m.role_id else None) or m.role
    teams_of_user: Dict[int, List[int]] = {}
    for link in team_links:
        team = teams[link.team_id]
        slot(link.user_id, team.workspace_id)["teams"].append(team.name)
        teams_of_user.setdefault(link.user_id, []).append(team.id)
    for d in direct:
        p = projects[d.project_id]
        slot(d.user_id, p.workspace_id)["projects"].append(
            {"project_id": p.id, "project": p.name, "role": level(d), "via": "direct"})
    access_by_team: Dict[int, List[Any]] = {}
    for ta in team_access:
        access_by_team.setdefault(ta.team_id, []).append(ta)
    for uid, tids in teams_of_user.items():
        for tid in tids:
            for ta in access_by_team.get(tid, []):
                p = projects[ta.project_id]
                slot(uid, p.workspace_id)["projects"].append(
                    {"project_id": p.id, "project": p.name, "role": level(ta),
                     "via": f"team:{teams[tid].name}"})

    out_users = []
    for uid in sorted(users, key=lambda i: users[i].email.lower()):
        u = users[uid]
        login = logins.get(uid)
        scim = bool(u.scim_external_id)
        out_users.append({
            "user_id": u.id, "email": u.email, "full_name": u.full_name,
            "active": u.is_active, "instance_admin": bool(u.is_instance_admin),
            "mfa_enabled": bool(u.mfa_enabled), "scim_managed": scim,
            "last_login_at": (login or {}).get("at") or u.last_login_at,
            "auth_method": auth_method((login or {}).get("method"), scim_managed=scim,
                                       has_login=bool(login or u.last_login_at)),
            "workspaces": sorted(per_user.get(uid, {}).values(), key=lambda w: w["workspace"]),
        })

    key_q = select(ApiKey).where(ApiKey.revoked_at == None)  # noqa: E711
    if workspace_id is not None:
        key_q = key_q.where(ApiKey.workspace_id == workspace_id)
    keys = (await session.exec(key_q.order_by(ApiKey.created_at))).all()
    creators = {u.id: u.email for u in (await session.exec(select(User).where(
        User.id.in_([k.created_by_id for k in keys])))).all()} if keys else {}
    key_projects = projects if workspace_id is not None else {
        p.id: p for p in (await session.exec(select(Project).where(
            Project.id.in_([k.project_id for k in keys if k.project_id])))).all()}
    now = datetime.utcnow()
    out_keys = [{
        "api_key_id": k.id, "name": k.name, "prefix": k.prefix,
        "workspace_id": k.workspace_id,
        "workspace": workspaces[k.workspace_id].name if k.workspace_id in workspaces else None,
        "scope": f"project:{key_projects[k.project_id].name}" if k.project_id in key_projects
        else ("project" if k.project_id else "workspace"),
        "role": roles.get(k.role_id) if k.role_id else None,
        "created_by": creators.get(k.created_by_id), "created_by_id": k.created_by_id,
        "created_at": k.created_at, "last_used_at": k.last_used_at, "expires_at": k.expires_at,
        "expired": bool(k.expires_at and k.expires_at < now),
    } for k in keys]

    return {"scope": "workspace" if workspace_id is not None else "instance",
            "workspace_id": workspace_id,
            "workspace": workspaces[workspace_id].name if workspace_id in workspaces else None,
            "generated_at": now, "users": out_users, "api_keys": out_keys}


def jsonable(value: Any) -> Any:
    """Snapshots live in a JSON column; datetimes become ISO-8601 UTC strings."""
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds") + "Z"
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    return value


def items_from_report(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Campaign items: one per person, one per API key."""
    items = []
    for u in report["users"]:
        items.append({"subject_type": "user", "subject_id": u["user_id"],
                      "label": u["email"], "snapshot": jsonable(u)})
    for k in report["api_keys"]:
        items.append({"subject_type": "api_key", "subject_id": k["api_key_id"],
                      "label": f"{k['name']} ({k['prefix']}…)", "snapshot": jsonable(k)})
    return items


# ---------------------------------------------------------------------------
# Rendering (pure)
# ---------------------------------------------------------------------------

CSV_COLUMNS = (
    "subject_type", "subject_id", "label", "active", "instance_admin", "mfa_enabled",
    "auth_method", "last_login_at", "workspace", "workspace_role", "teams", "project_roles",
    "key_scope", "key_role", "key_created_by", "key_created_at", "key_last_used_at",
    "key_expires_at", "decision", "note", "decided_by", "decided_at",
)


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.isoformat(timespec="seconds") + "Z"
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


def _projects_text(projects: Iterable[Dict[str, Any]]) -> str:
    return "; ".join(f"{p['project']}={p['role']} ({p['via']})" for p in projects)


def rows_for_items(items: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Flat rows: a person with access in two workspaces is two rows (so a
    spreadsheet filter by workspace works), a key is one."""
    rows: List[Dict[str, str]] = []
    for it in items:
        snap = it.get("snapshot") or {}
        decision = {"decision": _fmt(it.get("decision")), "note": _fmt(it.get("note")),
                    "decided_by": _fmt(it.get("decided_by")), "decided_at": _fmt(it.get("decided_at"))}
        if it["subject_type"] == "api_key":
            rows.append({c: "" for c in CSV_COLUMNS} | {
                "subject_type": "api_key", "subject_id": _fmt(it["subject_id"]),
                "label": it["label"], "workspace": _fmt(snap.get("workspace")),
                "key_scope": _fmt(snap.get("scope")), "key_role": _fmt(snap.get("role")),
                "key_created_by": _fmt(snap.get("created_by")),
                "key_created_at": _fmt(snap.get("created_at")),
                "key_last_used_at": _fmt(snap.get("last_used_at")) or "never",
                "key_expires_at": _fmt(snap.get("expires_at")) or "never", **decision})
            continue
        base = {c: "" for c in CSV_COLUMNS} | {
            "subject_type": "user", "subject_id": _fmt(it["subject_id"]), "label": it["label"],
            "active": _fmt(snap.get("active")), "instance_admin": _fmt(snap.get("instance_admin")),
            "mfa_enabled": _fmt(snap.get("mfa_enabled")), "auth_method": _fmt(snap.get("auth_method")),
            "last_login_at": _fmt(snap.get("last_login_at")) or "never", **decision}
        spaces = snap.get("workspaces") or [{}]
        for w in spaces:
            rows.append(base | {
                "workspace": _fmt(w.get("workspace")), "workspace_role": _fmt(w.get("role")),
                "teams": "; ".join(w.get("teams") or []),
                "project_roles": _projects_text(w.get("projects") or [])})
    return rows


def _csv_safe(value: str) -> str:
    # Spreadsheet formula injection: a cell starting with = + - @ is executed
    # by Excel/Sheets. Names and notes are user-controlled.
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def render_csv(items: Iterable[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(CSV_COLUMNS))
    writer.writeheader()
    for row in rows_for_items(items):
        writer.writerow({k: _csv_safe(v) for k, v in row.items()})
    return buf.getvalue()


def render_html(title: str, items: Iterable[Dict[str, Any]], *,
                meta: Dict[str, Any]) -> str:
    """Self-contained, print-ready page (Save as PDF from the browser)."""
    items = list(items)
    rows = rows_for_items(items)
    e = html.escape
    users = [r for r in rows if r["subject_type"] == "user"]
    keys = [r for r in rows if r["subject_type"] == "api_key"]

    def table(cols, data):
        head = "".join(f"<th>{e(c.replace('_', ' '))}</th>" for c in cols)
        body = "".join("<tr>" + "".join(f"<td>{e(r.get(c, ''))}</td>" for c in cols) + "</tr>"
                       for r in data) or f"<tr><td colspan='{len(cols)}'>none</td></tr>"
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    meta_html = "".join(f"<div><b>{e(k.replace('_', ' ').capitalize())}:</b> {e(_fmt(v))}</div>"
                        for k, v in meta.items() if v not in (None, ""))
    user_cols = ["label", "active", "workspace", "workspace_role", "teams", "project_roles",
                 "auth_method", "mfa_enabled", "instance_admin", "last_login_at",
                 "decision", "note", "decided_by", "decided_at"]
    key_cols = ["label", "workspace", "key_scope", "key_role", "key_created_by",
                "key_created_at", "key_last_used_at", "key_expires_at",
                "decision", "note", "decided_by", "decided_at"]
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{e(title)}</title>
<style>
body{{font:12px/1.4 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#111;margin:24px}}
h1{{font-size:18px;margin:0 0 4px}} h2{{font-size:14px;margin:20px 0 6px}}
.meta{{color:#444;margin-bottom:12px}} table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ccc;padding:3px 5px;text-align:left;vertical-align:top}}
th{{background:#f3f4f6;text-transform:capitalize}} tr{{page-break-inside:avoid}}
@page{{size:A4 landscape;margin:12mm}}
</style></head><body>
<h1>{e(title)}</h1><div class="meta">{meta_html}</div>
<h2>People ({len({r['subject_id'] for r in users})})</h2>{table(user_cols, users)}
<h2>API keys ({len(keys)})</h2>{table(key_cols, keys)}
</body></html>"""
