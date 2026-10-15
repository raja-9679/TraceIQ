"""SecurityFinding lifecycle + alert delivery for estate and page scans (WP9).

Findings are written through the same model and `finding_fingerprint` as
every other scanner, so triage (and WP7's owner / due-date workflow) applies
unchanged. The lifecycle mirrors the access-matrix one:

* identity is (project, scan_type, fingerprint);
* a false_positive verdict sticks — the row is refreshed but keeps its status;
* a resolved finding that comes back is reopened;
* a finding in a *scope that ran* but was not produced this time is resolved.

"Scope that ran" is (scan_type, target_url): a scan that skipped port probing
(no attestation) must not resolve the port findings of the previous one.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def apply_findings(session, project_id: int, produced: Iterable[Dict[str, Any]],
                   scopes: Iterable[Tuple[str, str]]) -> Dict[str, int]:
    """Create / refresh / reopen / resolve (sync session; caller commits)."""
    from sqlmodel import select

    from app.models import SecurityFinding

    produced = list(produced)
    scopes = set(scopes) | {(f["scan_type"], f["target_url"]) for f in produced}
    if not scopes:
        return {"created": 0, "updated": 0, "resolved": 0}
    scan_types = {s for s, _ in scopes}
    targets = {t for _, t in scopes}
    existing = session.exec(select(SecurityFinding).where(
        SecurityFinding.project_id == project_id,
        SecurityFinding.scan_type.in_(scan_types),
        SecurityFinding.target_url.in_(targets))).all()
    by_fp: Dict[Tuple[str, str], Any] = {}
    for f in existing:
        if f.fingerprint:
            by_fp[(f.scan_type, f.fingerprint)] = f
    now = datetime.utcnow()
    created = updated = resolved = 0
    seen: Set[Tuple[str, str]] = set()
    for p in produced:
        key = (p["scan_type"], p["fingerprint"])
        if key in seen:
            continue
        seen.add(key)
        row = by_fp.get(key)
        if row is None:
            session.add(SecurityFinding(project_id=project_id, status="open", **p))
            created += 1
            continue
        row.severity, row.description, row.evidence = p["severity"], p["description"], p["evidence"]
        if row.status == "resolved":
            row.status, row.resolved_at = "open", None
        session.add(row)
        updated += 1
    for key, row in by_fp.items():
        if key in seen or (row.scan_type, row.target_url) not in scopes:
            continue
        if row.status in ("open", "acknowledged"):
            row.status, row.resolved_at = "resolved", now
            session.add(row)
            resolved += 1
    return {"created": created, "updated": updated, "resolved": resolved}


def send_alert(session, project, title: str, lines: List[str],
               emails: Optional[List[str]] = None) -> None:
    """Post to the project's Slack/Teams channels (when notifications are on)
    and email the explicit recipients — the same channels monitors use.
    Best-effort: an alerting failure never fails the scan."""
    import requests

    from app.tasks.notification_tasks import get_notification_settings

    cfg = get_notification_settings(project, session) or {}
    if not cfg.get("enabled", False):
        cfg = {}
    text = "\n".join(lines)
    if cfg.get("slack_enabled") and cfg.get("slack_webhook_url"):
        try:
            requests.post(cfg["slack_webhook_url"], timeout=10, json={
                "text": f"*{title}*\n{text}"}).raise_for_status()
        except Exception as e:  # noqa: BLE001
            logger.error("[Estate] Slack alert failed: %s", e)
    if cfg.get("teams_enabled") and cfg.get("teams_webhook_url"):
        try:
            requests.post(cfg["teams_webhook_url"], timeout=10, json={
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
