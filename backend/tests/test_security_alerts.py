"""Security-alert rules (WP7)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from app.models import AuditLog, SecurityEventOutbox
from app.services import security_alerts as sa
from app.tasks import security_ops_tasks as ops

CFG = sa.AlertConfig(enabled=True, threshold=3, window_minutes=10)
T0 = datetime(2026, 10, 15, 9, 0, 0)


def ev(action, entity="auth", **kw):
    base = {"id": kw.pop("id", 1), "action": action, "entity_type": entity,
            "entity_id": kw.pop("entity_id", 1), "timestamp": kw.pop("timestamp", T0),
            "row_hash": kw.pop("row_hash", "r"), "changes": kw.pop("changes", {}),
            "ip_address": kw.pop("ip", None), "user_id": kw.pop("user_id", None),
            "workspace_id": kw.pop("workspace_id", None), "actor_label": None}
    return base


def rules(alerts):
    return [a["rule"] for a in alerts]


# --- single-event rules ------------------------------------------------------------

def test_instance_admin_grant_is_critical():
    out = sa.classify(ev("instance_admin_grant", "user", changes={"email": "x@y"}), CFG)
    assert rules(out) == ["admin_granted"] and out[0]["severity"] == "critical"


@pytest.mark.parametrize("role,fires", [("Workspace Admin", True), ("Project Admin", True),
                                        ("Workspace Member", False), ("Project Editor", False)])
def test_role_grants_alert_only_for_admin_roles(role, fires):
    out = sa.classify(ev("role_grant", "workspace", changes={"role": role, "scope": "workspace"}), CFG)
    assert bool(out) is fires


@pytest.mark.parametrize("action", ["create", "revoke"])
def test_api_key_changes(action):
    out = sa.classify(ev(action, "api_key", changes={"name": "ci", "prefix": "tiq_ab"}), CFG)
    assert rules(out) == ["api_key_change"]


def test_api_key_update_or_use_is_not_an_alert():
    assert sa.classify(ev("update", "api_key"), CFG) == []


def test_mfa_required_turned_off_alerts_but_turned_on_does_not():
    off = ev("update", "instance_setting", changes={"key": "MFA_REQUIRED", "from": True, "to": False})
    on = ev("update", "instance_setting", changes={"key": "MFA_REQUIRED", "from": False, "to": True})
    assert rules(sa.classify(off, CFG)) == ["mfa_weakened"]
    assert sa.classify(on, CFG) == []


def test_reset_to_environment_counts_as_turning_it_off():
    reset = ev("reset", "instance_setting",
               changes={"key": "PASSWORD_LOGIN_DISABLED", "from": "true", "to": False})
    assert rules(sa.classify(reset, CFG)) == ["sso_only_disabled"]


def test_user_disabling_mfa():
    assert rules(sa.classify(ev("mfa_disabled", changes={"email": "a@b"}), CFG)) == ["mfa_weakened"]


def test_scim_token_change_and_clear():
    assert rules(sa.classify(ev("update", "instance_setting",
                                changes={"key": "SCIM_TOKEN", "changed": True}), CFG)) == ["scim_token_changed"]
    cleared = sa.classify(ev("reset", "instance_setting", changes={"key": "SCIM_TOKEN"}), CFG)
    assert cleared[0]["details"]["cleared"] is True


def test_scim_deactivation():
    assert rules(sa.classify(ev("scim_deactivate", "user"), CFG)) == ["scim_deactivation"]


def test_unrelated_events_and_disabled_rules_are_quiet():
    assert sa.classify(ev("login"), CFG) == []
    assert sa.classify(ev("update", "instance_setting", changes={"key": "SMTP_HOST"}), CFG) == []
    off = sa.AlertConfig(enabled=True, rules=frozenset({"api_key_change"}))
    assert sa.classify(ev("instance_admin_grant", "user"), off) == []
    assert sa.classify(ev("create", "api_key"), sa.AlertConfig(enabled=False)) == []


def test_load_config_defaults_every_rule_on_and_honours_switches():
    values = {"SECURITY_ALERTS_ENABLED": True, "ALERT_API_KEY_CHANGES": False,
              "ALERT_FAILED_LOGIN_THRESHOLD": 7, "ALERT_FAILED_LOGIN_WINDOW_MINUTES": 3}
    cfg = sa.load_config(lambda k: values.get(k, True if k.startswith("ALERT_") else None))
    assert cfg.enabled and cfg.threshold == 7 and cfg.window_minutes == 3
    assert "api_key_change" not in cfg.rules and "admin_granted" in cfg.rules


# --- burst rule ------------------------------------------------------------------------

def failures(n, email="victim@corp", ip="203.0.113.5", start=T0, step=timedelta(seconds=30)):
    return [ev("login_failed", id=i + 1, timestamp=start + step * i, ip=ip,
               changes={"email": email}) for i in range(n)]


def test_burst_fires_exactly_at_the_threshold():
    evs = failures(4)
    assert sa.burst_alerts(evs[1], evs[:2], CFG) == []
    third = sa.burst_alerts(evs[2], evs[:3], CFG)
    assert sorted(a["details"].get("account") or a["details"].get("source_ip") for a in third) \
        == ["203.0.113.5", "victim@corp"]
    # The 4th failure in the same window does not re-alert.
    assert sa.burst_alerts(evs[3], evs[:4], CFG) == []


def test_burst_per_ip_across_many_accounts():
    evs = [ev("login_failed", id=i + 1, ip="198.51.100.7", timestamp=T0,
              changes={"email": f"user{i}@corp"}) for i in range(3)]
    out = sa.burst_alerts(evs[2], evs, CFG)
    assert len(out) == 1
    assert out[0]["details"]["source_ip"] == "198.51.100.7"
    assert out[0]["details"]["accounts"] == ["user0@corp", "user1@corp", "user2@corp"]


def test_account_matching_is_case_insensitive():
    evs = [ev("login_failed", id=i, changes={"email": e}) for i, e in
           enumerate(["A@corp", "a@CORP", "a@corp"], 1)]
    assert rules(sa.burst_alerts(evs[2], evs, CFG)) == ["failed_login_burst"]


def test_burst_rule_switch():
    evs = failures(3)
    off = sa.AlertConfig(enabled=True, rules=frozenset(), threshold=3)
    assert sa.burst_alerts(evs[2], evs, off) == []


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[AuditLog.__table__, SecurityEventOutbox.__table__])
    with Session(engine) as s:
        yield s


def _fail(db, minutes, email="victim@corp", ip="203.0.113.5"):
    row = AuditLog(entity_type="auth", entity_id=0, action="login_failed",
                   timestamp=T0 + timedelta(minutes=minutes), changes={"email": email},
                   ip_address=ip)
    db.add(row)
    db.commit()
    return row


def test_evaluate_counts_only_the_window(db, monkeypatch):
    from app.services import instance_settings
    monkeypatch.setattr(instance_settings, "effective", lambda k: False)
    _fail(db, 0)
    _fail(db, 1)
    third_late = _fail(db, 30)        # outside a 10-minute window of the first two
    assert sa.evaluate(db, third_late, CFG) == []
    _fail(db, 31)
    fourth = _fail(db, 32)
    assert rules(sa.evaluate(db, fourth, CFG)) == ["failed_login_burst", "failed_login_burst"]


def test_drain_evaluates_notifies_and_streams_the_alert(db, monkeypatch):
    from app.services import instance_settings, siem
    settings = {"SECURITY_ALERTS_ENABLED": True, "SIEM_ENABLED": True,
                "SIEM_SINK_TYPE": "webhook", "SIEM_URL": "https://siem.example.com/in",
                "SIEM_TOKEN": "t", "ALERT_FAILED_LOGIN_THRESHOLD": 5}
    monkeypatch.setattr(instance_settings, "effective",
                        lambda k: settings.get(k, True if k.startswith("ALERT_") else None))

    async def ok(cfg):
        return None
    monkeypatch.setattr(siem, "check_destination", ok)

    grant = AuditLog(entity_type="user", entity_id=9, action="instance_admin_grant",
                     timestamp=T0, changes={"email": "new-admin@corp"})
    db.add(grant)
    db.commit()
    queued = db.exec(select(SecurityEventOutbox)).all()
    assert len(queued) == 1 and queued[0].needs_alert_eval and queued[0].needs_delivery

    notified, delivered = [], []
    out = ops.drain_once(db, sender=lambda cfg, events: delivered.extend(events),
                         notify=notified.append)
    assert out["alerts"] == 1 and out["sent"] == 2
    assert notified[0]["rule"] == "admin_granted"
    assert [e["event_type"] for e in delivered] == ["audit", "security_alert"]
    assert delivered[1]["audit_row_hash"] == grant.row_hash
    # Both jobs done → the outbox is empty again.
    assert db.exec(select(SecurityEventOutbox)).all() == []


def test_render_text_mentions_rule_actor_and_audit_row():
    alert = sa.classify(ev("instance_admin_grant", "user", changes={"email": "x@y"},
                           row_hash="abc"), CFG)[0]
    text = sa.render_text(alert)
    assert "Instance admin granted" in text and "admin_granted" in text and "abc" in text


def test_alert_webhook_to_cloud_metadata_is_refused():
    # Slack/Teams alert URLs are POSTed to by the server: net_guard applies.
    import pytest
    from app.tasks.security_ops_tasks import _guard
    with pytest.raises(Exception):
        _guard("http://169.254.169.254/latest/meta-data/")
