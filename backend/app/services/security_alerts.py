"""Security-alert rules over the audit stream (WP7).

Every audit row passes through the outbox; when SECURITY_ALERTS_ENABLED is on
the drain task evaluates these rules against it. An alert goes to the
instance's security channels (email / Slack / Teams — separate from run
notifications on purpose) and to the SIEM as a `security_alert` event.

Rules (each switchable, see the `security_alerts` instance-settings group):

  failed_login_burst  N failed logins for one account, or from one IP, within
                      M minutes. Password, MFA-code and LDAP failures all count.
  admin_granted       instance-admin grant; workspace/project admin role grant
  api_key_change      API key created or revoked
  mfa_weakened        a user disabled MFA; MFA_REQUIRED switched off
  sso_only_disabled   PASSWORD_LOGIN_DISABLED switched off
  scim_token_changed  SCIM bearer token set, replaced or cleared
  scim_deactivation   a user deactivated through SCIM

The burst rule fires when the count reaches EXACTLY N — the Nth failure in the
window — rather than at every failure past N. That needs no state of its own
(no "already alerted" table to get wrong): each audit row is evaluated once, and
the window sliding past the old failures re-arms the rule naturally.

`classify` and `burst_alerts` are pure; `evaluate` adds the one query the burst
rule needs.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

RULES = ("failed_login_burst", "admin_granted", "api_key_change", "mfa_weakened",
         "sso_only_disabled", "scim_token_changed", "scim_deactivation")

_RULE_SETTING = {
    "failed_login_burst": "ALERT_FAILED_LOGIN_BURST",
    "admin_granted": "ALERT_ADMIN_GRANTED",
    "api_key_change": "ALERT_API_KEY_CHANGES",
    "mfa_weakened": "ALERT_MFA_WEAKENED",
    "sso_only_disabled": "ALERT_SSO_ONLY_DISABLED",
    "scim_token_changed": "ALERT_SCIM_TOKEN_CHANGED",
    "scim_deactivation": "ALERT_SCIM_DEACTIVATION",
}

FAILED_LOGIN_ACTIONS = frozenset({"login_failed"})
ADMIN_ROLES = frozenset({"admin", "workspace admin", "project admin", "tenant admin"})


@dataclass(frozen=True)
class AlertConfig:
    enabled: bool = False
    rules: frozenset = frozenset(RULES)
    threshold: int = 5
    window_minutes: int = 10

    def on(self, rule: str) -> bool:
        return self.enabled and rule in self.rules


def load_config(get: Any = None) -> AlertConfig:
    if get is None:
        from app.services.instance_settings import effective as get
    rules = frozenset(r for r, key in _RULE_SETTING.items() if get(key) is not False)
    threshold = get("ALERT_FAILED_LOGIN_THRESHOLD") or 5
    window = get("ALERT_FAILED_LOGIN_WINDOW_MINUTES") or 10
    return AlertConfig(enabled=bool(get("SECURITY_ALERTS_ENABLED")), rules=rules,
                       threshold=max(int(threshold), 1), window_minutes=max(int(window), 1))


def _get(ev: Any, name: str) -> Any:
    return ev.get(name) if isinstance(ev, dict) else getattr(ev, name, None)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _ts(ev: Any) -> datetime:
    raw = _get(ev, "timestamp")
    if isinstance(raw, datetime):
        return raw
    return datetime.fromisoformat(str(raw).rstrip("Z"))


def _alert(rule: str, severity: str, title: str, ev: Any, **details: Any) -> Dict[str, Any]:
    row_hash = _get(ev, "row_hash") or (_get(ev, "chain") or {}).get("row_hash")
    alert_id = hashlib.sha256(f"{rule}|{row_hash}|{sorted(details.items())}".encode()).hexdigest()[:32]
    ts = _get(ev, "timestamp")
    return {
        "alert_id": alert_id,
        "rule": rule,
        "severity": severity,
        "title": title,
        "timestamp": ts.isoformat() + "Z" if isinstance(ts, datetime) else ts,
        "workspace_id": _get(ev, "workspace_id"),
        "actor": _get(ev, "actor_label") or (f"user:{_get(ev, 'user_id')}" if _get(ev, "user_id") else _get(ev, "actor_type")),
        "ip_address": _get(ev, "ip_address"),
        "audit_id": _get(ev, "id"),
        "audit_row_hash": row_hash,
        "audit_action": _get(ev, "action"),
        "details": details,
    }


def classify(ev: Any, cfg: AlertConfig) -> List[Dict[str, Any]]:
    """Single-event rules (everything except the burst rule)."""
    out: List[Dict[str, Any]] = []
    action = _get(ev, "action") or ""
    entity = _get(ev, "entity_type") or ""
    changes = _get(ev, "changes") or {}

    if cfg.on("admin_granted"):
        if action == "instance_admin_grant":
            out.append(_alert("admin_granted", "critical", "Instance admin granted", ev,
                              target_user_id=_get(ev, "entity_id"),
                              target=changes.get("email")))
        elif action == "role_grant" and str(changes.get("role", "")).lower() in ADMIN_ROLES:
            out.append(_alert("admin_granted", "high",
                              f"{changes.get('scope', 'workspace').title()} admin role granted", ev,
                              target_user_id=changes.get("user_id"), target=changes.get("email"),
                              scope=changes.get("scope"), scope_id=changes.get("scope_id")))

    if cfg.on("api_key_change") and entity == "api_key" and action in ("create", "revoke"):
        out.append(_alert("api_key_change", "medium",
                          "API key created" if action == "create" else "API key revoked", ev,
                          api_key_id=_get(ev, "entity_id"), name=changes.get("name"),
                          prefix=changes.get("prefix"), project_id=changes.get("project_id")))

    if entity == "instance_setting":
        key = changes.get("key")
        before, after = changes.get("from"), changes.get("to")
        if cfg.on("mfa_weakened") and key == "MFA_REQUIRED" and _truthy(before) and not _truthy(after):
            out.append(_alert("mfa_weakened", "critical", "MFA_REQUIRED was turned off", ev, setting=key))
        if cfg.on("sso_only_disabled") and key == "PASSWORD_LOGIN_DISABLED" \
                and _truthy(before) and not _truthy(after):
            out.append(_alert("sso_only_disabled", "high",
                              "SSO-only mode was turned off (password login re-enabled)", ev, setting=key))
        if cfg.on("scim_token_changed") and key == "SCIM_TOKEN":
            out.append(_alert("scim_token_changed", "high", "SCIM provisioning token changed", ev,
                              setting=key, cleared=action == "reset"))

    if cfg.on("mfa_weakened") and action == "mfa_disabled":
        out.append(_alert("mfa_weakened", "high", "A user disabled MFA", ev,
                          target_user_id=_get(ev, "entity_id"), target=changes.get("email")))

    if cfg.on("scim_deactivation") and action == "scim_deactivate":
        out.append(_alert("scim_deactivation", "medium", "User deactivated by SCIM", ev,
                          target_user_id=_get(ev, "entity_id"), target=changes.get("email")))
    return out


def _login_subject(ev: Any) -> Optional[str]:
    changes = _get(ev, "changes") or {}
    email = changes.get("email") or changes.get("username")
    if email:
        return str(email).strip().lower()
    uid = _get(ev, "user_id")
    return f"user:{uid}" if uid else None


def burst_alerts(ev: Any, window_events: Iterable[Any], cfg: AlertConfig) -> List[Dict[str, Any]]:
    """`window_events`: failed-login events in [ev.timestamp - M, ev.timestamp]
    with id <= ev.id, INCLUDING ev. Fires on the event that makes a count
    reach exactly N."""
    if not cfg.on("failed_login_burst") or (_get(ev, "action") not in FAILED_LOGIN_ACTIONS):
        return []
    events = list(window_events)
    subject, ip = _login_subject(ev), _get(ev, "ip_address")
    out: List[Dict[str, Any]] = []
    if subject:
        n = sum(1 for e in events if _login_subject(e) == subject)
        if n == cfg.threshold:
            out.append(_alert("failed_login_burst", "high",
                              f"{n} failed logins for one account in {cfg.window_minutes} min", ev,
                              account=subject, count=n, window_minutes=cfg.window_minutes))
    if ip:
        n = sum(1 for e in events if _get(e, "ip_address") == ip)
        if n == cfg.threshold:
            accounts = sorted({s for s in (_login_subject(e) for e in events
                                           if _get(e, "ip_address") == ip) if s})
            out.append(_alert("failed_login_burst", "high",
                              f"{n} failed logins from one IP address in {cfg.window_minutes} min", ev,
                              source_ip=ip, count=n, window_minutes=cfg.window_minutes,
                              accounts=accounts[:20]))
    return out


def evaluate(session: Any, row: Any, cfg: AlertConfig) -> List[Dict[str, Any]]:
    """All alerts one AuditLog row raises (sync session)."""
    if not cfg.enabled:
        return []
    alerts = classify(row, cfg)
    if cfg.on("failed_login_burst") and row.action in FAILED_LOGIN_ACTIONS:
        from sqlmodel import select
        from app.models import AuditLog
        since = row.timestamp - timedelta(minutes=cfg.window_minutes)
        window = session.exec(select(AuditLog).where(
            AuditLog.action.in_(tuple(FAILED_LOGIN_ACTIONS)),
            AuditLog.timestamp >= since,
            AuditLog.timestamp <= row.timestamp,
            AuditLog.id <= row.id,
        )).all()
        alerts += burst_alerts(row, window, cfg)
    return alerts


def render_text(alert: Dict[str, Any]) -> str:
    lines = [f"[{alert['severity'].upper()}] {alert['title']}",
             f"Rule: {alert['rule']}", f"When: {alert.get('timestamp')}"]
    if alert.get("actor"):
        lines.append(f"Actor: {alert['actor']}")
    if alert.get("ip_address"):
        lines.append(f"From IP: {alert['ip_address']}")
    if alert.get("workspace_id"):
        lines.append(f"Workspace: {alert['workspace_id']}")
    for k, v in (alert.get("details") or {}).items():
        if v not in (None, "", []):
            lines.append(f"{k.replace('_', ' ').capitalize()}: {v}")
    lines.append(f"Audit row: {alert.get('audit_id')} ({alert.get('audit_row_hash')})")
    return "\n".join(lines)
