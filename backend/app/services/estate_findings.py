"""SecurityFinding lifecycle + alert delivery for estate and page scans (WP9).

Findings are written through the same model and `finding_fingerprint` as
every other scanner, so triage and WP7's owner / SLA / due-date workflow apply
unchanged. Estate checks are posture, not per-run evidence, so there is ONE
row per (project, scan_type, fingerprint), refreshed in place:

* produced and new → `open`, with a due date from the workspace SLA;
* produced again → refreshed; `false_positive` and an unexpired
  `accepted_risk` stick; a fix (`fixed_pending_verification`/`verified_fixed`)
  that shows up again, or a lapsed acceptance, is re-opened (WP7's
  `carry_forward` decides, so the rules cannot drift);
* not produced by a scan whose *scope ran* → `verified_fixed`: the rescan is
  the evidence WP7 asks for, so no human "I fixed it" step is needed.

"Scope that ran" is (scan_type, target_url): a scan whose DNS lookups failed
must not close the previous scan's DNS findings for that host.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_CLOSABLE = ("open", "in_progress", "fixed_pending_verification")


def apply_findings(session, project_id: int, produced: Iterable[Dict[str, Any]],
                   scopes: Iterable[Tuple[str, str]], now: Optional[datetime] = None) -> Dict[str, int]:
    """Create / refresh / reopen / verify (sync session; caller commits)."""
    from sqlmodel import select

    from app.models import SecurityFinding
    from app.services.finding_lifecycle import (
        _audit, _workspace_for_project, carry_forward, due_date, normalize_status, resolve_sla,
    )

    now = now or datetime.utcnow()
    produced = list(produced)
    scopes = set(scopes) | {(f["scan_type"], f["target_url"]) for f in produced}
    stats = {"created": 0, "updated": 0, "reopened": 0, "resolved": 0}
    if not scopes:
        return stats
    scan_types = {s for s, _ in scopes}
    targets = {t for _, t in scopes}
    existing = session.exec(select(SecurityFinding).where(
        SecurityFinding.project_id == project_id,
        SecurityFinding.scan_type.in_(scan_types),
        SecurityFinding.target_url.in_(targets),
        SecurityFinding.superseded_by_id == None,  # noqa: E711
    ).order_by(SecurityFinding.id)).all()
    by_fp: Dict[Tuple[str, str], Any] = {}
    for f in existing:
        if f.fingerprint:
            by_fp[(f.scan_type, f.fingerprint)] = f   # newest wins
    workspace = _workspace_for_project(session, project_id)
    ws_id = workspace.id if workspace else None
    sla = resolve_sla(workspace.finding_sla_days if workspace else None)
    seen: Set[Tuple[str, str]] = set()
    for p in produced:
        key = (p["scan_type"], p["fingerprint"])
        if key in seen:
            continue
        seen.add(key)
        row = by_fp.get(key)
        if row is None:
            session.add(SecurityFinding(project_id=project_id, status="open",
                                        due_at=due_date(now, p["severity"], sla), **p))
            stats["created"] += 1
            continue
        row.severity, row.description, row.evidence = p["severity"], p["description"], p["evidence"]
        before = normalize_status(row.status)
        carry = carry_forward(row, now)
        if carry.reopened:
            row.status, row.resolved_at, row.verified_at = "open", None, None
            for name, value in carry.fields.items():
                setattr(row, name, value)
            stats["reopened"] += 1
            _audit(session, row, "reopen", {"from": before, "to": "open", "reason": carry.reopened,
                                            "fingerprint": row.fingerprint}, ws_id)
        session.add(row)
        stats["updated"] += 1
    for key, row in by_fp.items():
        if key in seen or (row.scan_type, row.target_url) not in scopes:
            continue
        before = normalize_status(row.status)
        if before in _CLOSABLE:
            row.status, row.resolved_at, row.verified_at = "verified_fixed", now, now
            session.add(row)
            stats["resolved"] += 1
            _audit(session, row, "verified_fixed", {"from": before, "to": "verified_fixed",
                                                    "reason": "estate rescan no longer reports it"}, ws_id)
    return stats


def send_alert(session, project, title: str, lines: List[str],
               emails: Optional[List[str]] = None) -> None:
    """Post to the project's Slack/Teams channels (when notifications are on)
    and email the explicit recipients — the same channels monitors use.
    Best-effort: an alerting failure never fails the scan."""
    import requests

    from app.tasks.notification_tasks import get_notification_settings

    from app.tasks.security_ops_tasks import _guard

    cfg = get_notification_settings(project, session) or {}
    if not cfg.get("enabled", False):
        cfg = {}
    text = "\n".join(lines)
    # Project webhook URLs are user-entered: vetted by net_guard on every
    # delivery, and a redirect is never followed to somewhere unvetted.
    if cfg.get("slack_enabled") and cfg.get("slack_webhook_url"):
        try:
            _guard(cfg["slack_webhook_url"])
            requests.post(cfg["slack_webhook_url"], timeout=10, allow_redirects=False, json={
                "text": f"*{title}*\n{text}"}).raise_for_status()
        except Exception as e:  # noqa: BLE001
            logger.error("[Estate] Slack alert failed: %s", e)
    if cfg.get("teams_enabled") and cfg.get("teams_webhook_url"):
        try:
            _guard(cfg["teams_webhook_url"])
            requests.post(cfg["teams_webhook_url"], timeout=10, allow_redirects=False, json={
                "@type": "MessageCard", "@context": "http://schema.org/extensions",
                "summary": title, "sections": [{"activityTitle": title, "text": text.replace("\n", "<br>")}],
            }).raise_for_status()
        except Exception as e:  # noqa: BLE001
            logger.error("[Estate] Teams alert failed: %s", e)
    recipients = [r for r in (emails or []) if r and "@" in r]
    if recipients:
        try:
            from html import escape

            from app.tasks.notification_tasks import send_account_email
            html = "<p>" + "<br>".join(escape(line) for line in lines) + "</p>"
            for r in recipients:
                send_account_email.delay(r, title, html, text)
        except Exception as e:  # noqa: BLE001
            logger.error("[Estate] email alert failed: %s", e)


def cert_alert_due(days_left: int, thresholds: Iterable[int], already_sent: Optional[int]) -> Optional[int]:
    """The threshold to alert for now, or None. Alerts fire once per threshold
    crossed (30 → 14 → 7), not on every scan inside the window."""
    crossed = [t for t in sorted(set(int(t) for t in thresholds if int(t) > 0)) if days_left <= t]
    if not crossed:
        return None
    t = crossed[0]
    if already_sent is not None and already_sent <= t:
        return None
    return t
