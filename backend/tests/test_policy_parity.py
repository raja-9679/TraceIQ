"""Policy engine parity (WP14b, decision W4).

The engine must WRAP today's checks without changing a single outcome. Each
table below runs the same inputs through the legacy code (the function the
rule wraps, or — for checks that used to be inline — a verbatim copy of the
removed inline code) and through `evaluate` / `evaluate_sync`, and asserts the
decisions match.
"""
from __future__ import annotations

import itertools
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import policy
from app.services.policy import PolicyContext, evaluate, evaluate_sync


@pytest.fixture(autouse=True)
def captured_denies(monkeypatch):
    """No database: capture what would have been written to the deny log."""
    rows = []
    monkeypatch.setattr(policy, "_write_sync", lambda bind, action, ctx, d: rows.append((action, d.rule, d.reason)))
    policy.reset_dedupe()
    yield rows
    policy.reset_dedupe()


class FakeAsync:
    """AsyncSession.get over a dict keyed by (Model name, id)."""

    def __init__(self, objs):
        self.objs = objs

    async def get(self, model, ident):
        return self.objs.get((model.__name__, ident))


class FakeSync:
    def __init__(self, objs):
        self.objs = objs

    def get(self, model, ident):
        return self.objs.get((model.__name__, ident))

    def get_bind(self):
        return None


def _settings(monkeypatch, values):
    from app.services import instance_settings
    monkeypatch.setattr(instance_settings, "effective", lambda key: values.get(key))


# ---------------------------------------------------------------------------
# run.dispatch / plan.run_quota
# ---------------------------------------------------------------------------

def _legacy_create_run_quota(allowed, used, limit):
    """Verbatim shape of the inline check removed from create_run."""
    if not allowed:
        return HTTPException(status_code=402, detail=(
            f"Monthly run quota reached ({used}/{limit}). Upgrade the workspace plan to run more."))
    return None


def _legacy_benchmark_quota(allowed, used, limit, planned):
    if not allowed or (limit and used + planned > limit):
        return HTTPException(status_code=402, detail=(
            f"Monthly run quota would be exceeded ({used}+{planned}/{limit})."))
    return None


QUOTA_CASES = [(0, 0), (5, 0), (0, 10), (9, 10), (10, 10), (11, 10), (3, 4)]


@pytest.mark.parametrize("used,limit", QUOTA_CASES)
@pytest.mark.parametrize("planned", [None, 1, 2, 5])
async def test_run_quota_parity(monkeypatch, used, limit, planned):
    from app.services import billing

    async def check_quota(session, ws, metric):
        assert metric == "runs"
        return (limit == 0) or (used < limit), used, limit
    monkeypatch.setattr(billing, "check_quota", check_quota)

    facts = {} if planned is None else {"planned": planned}
    d = await evaluate("run.dispatch", PolicyContext(workspace_id=7, stage="create",
                                                      session=FakeAsync({}), facts=facts))
    allowed = (limit == 0) or (used < limit)
    legacy = (_legacy_create_run_quota(allowed, used, limit) if planned is None
              else _legacy_benchmark_quota(allowed, used, limit, planned))
    if legacy is None:
        assert d.allowed
    else:
        assert not d.allowed and d.rule == "plan.run_quota"
        exc = d.http_exception()
        assert (exc.status_code, exc.detail) == (legacy.status_code, legacy.detail)


async def test_run_quota_skipped_without_a_workspace(monkeypatch):
    from app.services import billing

    async def boom(*a):
        raise AssertionError("quota must not be read without a workspace")
    monkeypatch.setattr(billing, "check_quota", boom)
    d = await evaluate("run.dispatch", PolicyContext(stage="create", session=FakeAsync({})))
    assert d.allowed


# ---------------------------------------------------------------------------
# run.dispatch / admit + configure
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("at_capacity", [True, False])
def test_concurrency_parity(monkeypatch, at_capacity, captured_denies):
    from app import worker
    monkeypatch.setattr(worker, "_workspace_at_capacity", lambda run, session: at_capacity)
    d = evaluate_sync("run.dispatch", PolicyContext(stage="admit", session=FakeSync({}),
                                                     facts={"run": SimpleNamespace(id=1)}))
    assert d.allowed is (not at_capacity)
    if at_capacity:
        assert d.effect == policy.DEFER and d.data["retry_seconds"] == 30
    assert captured_denies == []        # a deferral is not a logged deny


CAPTURE_POLICIES = [None, {}, {"capture_level": "full"}, {"capture_level": "minimal"},
                    {"capture_level": "bogus", "store_bodies": False},
                    {"capture_level": "none", "redact_patterns": []},
                    {"capture_level": "full", "mask_selectors": ["#card"], "retention_days": 3}]


@pytest.mark.parametrize("ceiling", [None, "minimal", "standard", "full", "typo"])
@pytest.mark.parametrize("stored", CAPTURE_POLICIES)
def test_capture_ceiling_parity(monkeypatch, ceiling, stored):
    from app import worker
    _settings(monkeypatch, {"MAX_CAPTURE_LEVEL": ceiling})
    monkeypatch.setattr(worker, "_load_egress_policy", lambda *a: None)
    monkeypatch.setattr("app.services.policy.ai_allowed_sync",
                        lambda *a, **k: policy.Decision(allowed=True))
    project = SimpleNamespace(id=3, data_policy=stored, network_policy=None)
    session = FakeSync({("Project", 3): project})
    d = evaluate_sync("run.dispatch", PolicyContext(project_id=3, stage="configure",
                                                     session=session, facts={"run": None}))
    assert d.data["data_policy"] == worker._load_data_policy(3, session)
    # And the legacy no-project fallback.
    d = evaluate_sync("run.dispatch", PolicyContext(project_id=None, stage="configure",
                                                     session=session, facts={"run": None}))
    assert d.data["data_policy"] == worker._load_data_policy(None, session)


EGRESS_POLICIES = [None, {"enforce": False}, {"enforce": True, "allowed_hosts": ["api.example.com"]},
                   {"enforce": True, "allow_private": True}, {"enforce": "broken", "allowed_hosts": 5}]


@pytest.mark.parametrize("instance_enforced", [False, True])
@pytest.mark.parametrize("stored", EGRESS_POLICIES)
@pytest.mark.parametrize("env", [None, {"name": "x", "base_url": "https://app.example.com"}])
def test_egress_parity(monkeypatch, instance_enforced, stored, env):
    from app import worker
    _settings(monkeypatch, {"EGRESS_ALLOWLIST_ENFORCED": instance_enforced,
                            "EGRESS_ALLOWED_HOSTS": ["cdn.example.net"],
                            "ALLOW_PRIVATE_NETWORK_TARGETS": False})
    monkeypatch.setattr("app.services.policy.ai_allowed_sync",
                        lambda *a, **k: policy.Decision(allowed=True))
    project = SimpleNamespace(id=4, data_policy=None, network_policy=stored)
    session = FakeSync({("Project", 4): project})
    d = evaluate_sync("run.dispatch", PolicyContext(project_id=4, stage="configure", session=session,
                                                     facts={"run": None, "environment": env}))
    assert d.data["egress_policy"] == worker._load_egress_policy(4, env, session)


def test_configure_carries_the_ai_flag(monkeypatch):
    seen = {}

    def fake(ws, **kw):
        seen.update(ws=ws, **kw)
        return policy.Decision.deny("ai.workspace_opt_out", "off")
    monkeypatch.setattr("app.services.policy.ai_allowed_sync", fake)
    session = FakeSync({("Project", 4): SimpleNamespace(id=4, data_policy=None, network_policy=None)})
    d = evaluate_sync("run.dispatch", PolicyContext(workspace_id=9, project_id=4, stage="configure",
                                                     agent_id="bot", agent_session_id="s1",
                                                     session=session, facts={"run": None}))
    assert d.allowed and d.data["ai_allowed"] is False and d.data["ai_blocked_by"] == "ai.workspace_opt_out"
    assert seen["ws"] == 9 and seen["agent_session_id"] == "s1" and seen["log"] is False


# ---------------------------------------------------------------------------
# ai.use / plan.llm_token_quota  (+ the new opt-out)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("over_quota,ai_enabled", list(itertools.product([False, True], [True, False])))
def test_ai_use_parity_and_opt_out(monkeypatch, over_quota, ai_enabled, captured_denies):
    from app.services import llm_usage
    monkeypatch.setattr(llm_usage, "llm_tokens_quota_exceeded", lambda ws=None: over_quota)
    session = FakeSync({("Workspace", 5): SimpleNamespace(id=5, ai_enabled=ai_enabled)})
    d = evaluate_sync("ai.use", PolicyContext(workspace_id=5, session=session))
    # Legacy: providers skipped the call iff llm_tokens_quota_exceeded().
    legacy_blocked = over_quota
    if ai_enabled:
        assert (not d.allowed) is legacy_blocked
        if legacy_blocked:
            assert d.rule == "plan.llm_token_quota"
    else:
        assert not d.allowed and d.rule == "ai.workspace_opt_out"
    assert len(captured_denies) == (0 if d.allowed else 1)


def test_ai_use_without_workspace_is_unrestricted(monkeypatch):
    from app.services import llm_usage
    monkeypatch.setattr(llm_usage, "llm_tokens_quota_exceeded", lambda ws=None: True)
    # Legacy: no workspace context -> llm_tokens_quota_exceeded() returned False.
    assert evaluate_sync("ai.use", PolicyContext()).allowed


def test_provider_guard_uses_the_ambient_context(monkeypatch):
    from app.ai import providers
    from app.services.llm_usage import llm_call_context
    seen = []

    def fake_eval(action, ctx, **kw):
        seen.append((action, ctx.workspace_id, ctx.agent_id, ctx.agent_session_id))
        return policy.Decision.deny("ai.workspace_opt_out", "off")
    monkeypatch.setattr(policy, "evaluate_sync", fake_eval)

    class Inner:
        name = "fake"
        calls = 0

        def complete(self, prompt, **kw):
            Inner.calls += 1
            return "x"
    guarded = providers._PolicyGuarded(Inner())
    with llm_call_context(workspace_id=11, agent_id="claude", agent_session_id="t-1"):
        assert guarded.complete("hi") == ""
    assert Inner.calls == 0
    assert seen == [("ai.use", 11, "claude", "t-1")]


def test_get_provider_wraps_everything_but_null(monkeypatch):
    from app.ai import providers
    monkeypatch.setattr(providers, "_resolve_provider", lambda pid=None: providers.NullProvider())
    assert isinstance(providers.get_provider(), providers.NullProvider)
    inner = SimpleNamespace(name="anthropic", complete=lambda *a, **k: "ok")
    monkeypatch.setattr(providers, "_resolve_provider", lambda pid=None: inner)
    wrapped = providers.get_provider()
    assert isinstance(wrapped, providers._PolicyGuarded) and wrapped.name == "anthropic"


# ---------------------------------------------------------------------------
# proposal.approve / proposal.auto_apply
# ---------------------------------------------------------------------------

def _legacy_separation(instance_flag, workspace_flag, created_by, approver):
    from app.services.proposal_policy import approver_conflict
    required = bool(instance_flag) or bool(workspace_flag)
    return approver_conflict(created_by_id=created_by, approver_id=approver, required=required)


@pytest.mark.parametrize("instance_flag,workspace_flag,created_by,approver", list(itertools.product(
    [False, True], [False, True], [None, 1, 2], [1, 2])))
async def test_separation_parity(monkeypatch, instance_flag, workspace_flag, created_by, approver):
    from app.services import proposal_policy
    monkeypatch.setattr(proposal_policy, "effective",
                        lambda key: instance_flag if key == "REQUIRE_SEPARATE_APPROVER" else None)
    proposal = SimpleNamespace(id=1, project_id=2, created_by_id=created_by)
    session = FakeAsync({("Project", 2): SimpleNamespace(id=2, workspace_id=3),
                         ("Workspace", 3): SimpleNamespace(id=3, require_separate_approver=workspace_flag)})
    d = await evaluate("proposal.approve", PolicyContext(
        workspace_id=3, session=session, facts={"proposal": proposal, "approver_id": approver}))
    conflict = _legacy_separation(instance_flag, workspace_flag, created_by, approver)
    assert (not d.allowed) is conflict
    if conflict:
        exc = d.http_exception()
        assert exc.status_code == 403 and exc.detail.startswith("You filed this proposal")


@pytest.mark.parametrize("disabled", [False, True, None])
def test_auto_apply_kill_switch_parity(monkeypatch, disabled):
    from app.services import proposal_policy
    monkeypatch.setattr(proposal_policy, "effective",
                        lambda key: disabled if key == "AUTO_APPLY_DISABLED" else None)
    d = evaluate_sync("proposal.auto_apply", PolicyContext(facts={"proposal": SimpleNamespace(id=1)}))
    assert (not d.allowed) is proposal_policy.auto_apply_disabled()


# ---------------------------------------------------------------------------
# security.scan
# ---------------------------------------------------------------------------

SEC = [None, {"enabled": False, "allowed_domains": ["example.com"]},
       {"enabled": True, "allowed_domains": ["example.com"]},
       {"enabled": True, "allowed_domains": ["example.com"], "allow_active_scan": True},
       {"enabled": True, "allowed_domains": []}]


@pytest.mark.parametrize("sec,url,scan_type,ws_active,env_active", list(itertools.product(
    SEC, ["https://example.com/a", "https://api.example.com", "https://evil.test", "not a url"],
    ["baseline", "active"], [False, True], [False, True])))
async def test_scan_gate_parity(monkeypatch, sec, url, scan_type, ws_active, env_active):
    from app.api import security
    monkeypatch.setattr(security.cfg, "SECURITY_ACTIVE_SCAN_ENABLED", env_active)
    project = SimpleNamespace(id=1, workspace_id=2, security_settings=sec)
    session = FakeAsync({("Workspace", 2): SimpleNamespace(id=2, active_scan_enabled=ws_active)})
    legacy = await security._scan_gate_reason(session, project, url, scan_type)
    routed = await security._scan_gate(session, project, url, scan_type)
    assert routed == legacy
    d = await evaluate("security.scan", PolicyContext(
        workspace_id=2, project_id=1, session=session,
        facts={"project": project, "target_url": url, "scan_type": scan_type}))
    assert d.allowed is (legacy is None)
    if legacy:
        assert d.rule == "security.scan_gate" and d.data["gate"].startswith("security.")


# ---------------------------------------------------------------------------
# Engine mechanics
# ---------------------------------------------------------------------------

def test_catalog_lists_every_action_and_the_documented_net_guard():
    cat = policy.catalog()
    names = {(r["action"], r["name"]) for r in cat}
    for expected in [("run.dispatch", "plan.run_quota"), ("run.dispatch", "workspace.max_concurrent_runs"),
                     ("run.dispatch", "data.capture_ceiling"), ("run.dispatch", "network.egress_allowlist"),
                     ("ai.use", "ai.workspace_opt_out"), ("ai.use", "plan.llm_token_quota"),
                     ("ai.use", "agent.budget"), ("agent.write", "agent.kill_switch"),
                     ("proposal.create", "agent.budget"), ("proposal.approve", "proposal.separation_of_duties"),
                     ("proposal.auto_apply", "proposal.auto_apply_disabled"),
                     ("security.scan", "security.scan_gate"), ("backend.fetch", "network.ssrf_guard")]:
        assert expected in names
    ssrf = next(r for r in cat if r["name"] == "network.ssrf_guard")
    assert ssrf["enforced_by_engine"] is False and "net_guard" in ssrf["enforced_in"]
    assert all(r["description"] and r["configured"] for r in cat)


def test_documented_rules_never_run():
    d = evaluate_sync("backend.fetch", PolicyContext())
    assert d.allowed


def test_identical_denies_are_logged_once_per_window(monkeypatch, captured_denies):
    from app.services import llm_usage
    monkeypatch.setattr(llm_usage, "llm_tokens_quota_exceeded", lambda ws=None: True)
    session = FakeSync({("Workspace", 5): SimpleNamespace(id=5, ai_enabled=True)})
    for _ in range(5):
        assert not evaluate_sync("ai.use", PolicyContext(workspace_id=5, session=session)).allowed
    assert captured_denies == [("ai.use", "plan.llm_token_quota", "workspace over monthly token quota")]
    # A probe (log=False) never writes.
    policy.reset_dedupe()
    captured_denies.clear()
    evaluate_sync("ai.use", PolicyContext(workspace_id=5, session=session, log=False))
    assert captured_denies == []


def test_sync_evaluation_refuses_an_async_only_rule():
    with pytest.raises(RuntimeError):
        evaluate_sync("security.scan", PolicyContext(facts={}))


def test_job_payload_carries_the_ai_flag():
    from app.worker import _settings_payload
    assert _settings_payload({"ai_allowed": False})["ai_allowed"] is False
    assert _settings_payload({"ai_allowed": True})["ai_allowed"] is True
    assert "ai_allowed" not in _settings_payload({})
