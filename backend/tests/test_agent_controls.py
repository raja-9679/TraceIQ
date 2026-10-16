"""Agent budgets + kill switch: the pure decisions (WP14b, decision W3)."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from app.services import agent_controls as ac


def _row(**kw):
    base = {f: None for f in ac.LIMIT_FIELDS}
    base.update(blocked=False, blocked_reason=None, agent_id="bot")
    base.update(kw)
    return SimpleNamespace(**base)


NONE = {f: 0 for f in ac.LIMIT_FIELDS}


def test_no_budget_by_default():
    assert ac.resolve_limits(None, None, NONE) == NONE
    usage = {"runs": 10_000, "proposals": 10_000, "llm_tokens": 10**9,
             "first_seen_at": datetime.utcnow() - timedelta(days=30)}
    for kind in ("run", "proposal", "llm"):
        assert ac.budget_exceeded(NONE, usage, kind) is None


def test_resolution_agent_row_then_wildcard_then_instance():
    instance = {"max_runs": 50, "max_llm_tokens": 1000, "max_proposals": 5, "max_minutes": 60}
    wildcard = _row(agent_id="*", max_runs=20, max_proposals=None)
    agent = _row(max_runs=None, max_llm_tokens=0, max_minutes=10)
    limits = ac.resolve_limits(agent, wildcard, instance)
    assert limits == {"max_runs": 20,          # agent inherits -> workspace "*"
                      "max_llm_tokens": 0,     # explicit 0 on the agent = no limit
                      "max_proposals": 5,      # both inherit -> instance
                      "max_minutes": 10}


def test_counters_refuse_once_the_cap_is_reached():
    limits = {**NONE, "max_runs": 3, "max_proposals": 2, "max_llm_tokens": 100}
    now = datetime.utcnow()
    u = {"runs": 2, "proposals": 1, "llm_tokens": 99, "first_seen_at": now}
    assert ac.budget_exceeded(limits, u, "run", now) is None
    assert ac.budget_exceeded(limits, u, "proposal", now) is None
    assert ac.budget_exceeded(limits, u, "llm", now) is None
    u = {"runs": 3, "proposals": 2, "llm_tokens": 150, "first_seen_at": now}
    assert ac.budget_exceeded(limits, u, "run", now) == {"limit": "max_runs", "used": 3, "max": 3}
    assert ac.budget_exceeded(limits, u, "proposal", now)["limit"] == "max_proposals"
    assert ac.budget_exceeded(limits, u, "llm", now) == {"limit": "max_llm_tokens", "used": 150, "max": 100}


def test_kinds_are_independent():
    limits = {**NONE, "max_runs": 1}
    u = {"runs": 5, "proposals": 0, "llm_tokens": 0, "first_seen_at": datetime.utcnow()}
    assert ac.budget_exceeded(limits, u, "run") is not None
    assert ac.budget_exceeded(limits, u, "proposal") is None
    assert ac.budget_exceeded(limits, u, "llm") is None


def test_wall_clock_applies_to_every_kind():
    limits = {**NONE, "max_minutes": 30}
    now = datetime.utcnow()
    fresh = {"runs": 0, "proposals": 0, "llm_tokens": 0, "first_seen_at": now - timedelta(minutes=29)}
    stale = {**fresh, "first_seen_at": now - timedelta(minutes=31)}
    for kind in ("run", "proposal", "llm"):
        assert ac.budget_exceeded(limits, fresh, kind, now) is None
        verdict = ac.budget_exceeded(limits, stale, kind, now)
        assert verdict == {"limit": "max_minutes", "used": 31, "max": 30}


def test_429_payload_is_machine_readable():
    detail = ac.budget_detail({"limit": "max_runs", "used": 3, "max": 3}, "claude-code", "task-42")
    assert detail["error"] == "agent_budget_exceeded"
    assert detail["rule"] == "agent.budget"
    assert (detail["limit"], detail["used"], detail["max"]) == ("max_runs", 3, 3)
    assert (detail["agent_id"], detail["agent_session_id"]) == ("claude-code", "task-42")
    assert "3/3 runs started" in detail["message"]


def test_budget_decision_maps_to_429_with_the_payload():
    from app.services.policy import PolicyContext
    from app.services.policy.rules import _budget_decision
    ctx = PolicyContext(workspace_id=1, agent_id="bot", agent_session_id="s")
    d = _budget_decision(ctx, {**NONE, "max_proposals": 1},
                         {"runs": 0, "proposals": 1, "llm_tokens": 0, "first_seen_at": None}, "proposal")
    exc = d.http_exception()
    assert exc.status_code == 429
    assert exc.detail["limit"] == "max_proposals" and exc.detail["agent_session_id"] == "s"
    assert d.rule == "agent.budget" and d.data["kind"] == "proposal"


def test_blocked_payload_and_ids_are_trimmed():
    detail = ac.blocked_detail("bot", "looping")
    assert detail["error"] == "agent_blocked" and detail["rule"] == "agent.kill_switch"
    assert "looping" in detail["message"] and "Reads still work" in detail["message"]
    assert ac.clean_id("  x " * 200).startswith("x") and len(ac.clean_id("y" * 999)) == ac.MAX_ID_LEN
    assert ac.clean_id("   ") is None


def test_instance_defaults_read_the_registry(monkeypatch):
    from app.services import instance_settings
    assert all(k in instance_settings.REGISTRY for k in ac.INSTANCE_KEYS.values())
    monkeypatch.setattr(instance_settings, "effective",
                        lambda key: {"AGENT_SESSION_MAX_RUNS": 7, "AGENT_SESSION_MAX_MINUTES": "x"}.get(key))
    assert ac.instance_limits() == {"max_runs": 7, "max_llm_tokens": 0, "max_proposals": 0,
                                    "max_minutes": 0}
