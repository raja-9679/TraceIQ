"""The "fix first" list from info/FEATURE_ROADMAP_2026-10.md.

Seven features looked shipped but were broken underneath; each test here pins
the repaired behaviour:

  1. Personas reach the worker (dispatch injection + refresh job).
  2. Proactive selector heal gets a per-step DOM (and only at capture `full`).
  3. AI generation no longer pre-stamps a human review; saving does.
  4. Failure analysis receives console errors and failed network requests.
  5. TestCaseResult.is_flaky / .confidence are actually written.
  6. Visual baselines are versioned, decisions are audited, keys are confined
     to the case's own folder (and purge can't be aimed at other tenants).
  7. Heal accept refuses assertion steps and stale proposals; reject is
     access-checked.

Run with:
    cd backend && ./run-tests.sh tests/test_fix_first.py -q
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.core.auth import AuthPrincipal
from app.core.secrets import encrypt_json
from app.models import TestStatus


# ── 1. Personas ──────────────────────────────────────────────────────────────

class _GetSession:
    def __init__(self, obj):
        self.obj = obj

    def get(self, model, pk):
        return self.obj


def test_load_persona_returns_decrypted_state_and_headers():
    from app.worker import _load_persona

    state = {"cookies": [{"name": "sid", "value": "abc"}], "origins": []}
    persona = SimpleNamespace(session_state=encrypt_json(state),
                              auth_headers={"X-Tenant": "acme"})
    run = SimpleNamespace(persona_id=5)
    assert _load_persona(run, _GetSession(persona)) == (state, {"X-Tenant": "acme"})


def test_load_persona_none_when_unset_or_deleted():
    from app.worker import _load_persona

    assert _load_persona(SimpleNamespace(persona_id=None), _GetSession(None)) is None
    assert _load_persona(SimpleNamespace(persona_id=9), _GetSession(None)) is None


def test_refresh_job_rides_the_worker_queue_with_secrets():
    from app.tasks.persona_tasks import build_refresh_job

    persona = SimpleNamespace(id=3, auth_headers={"X-A": "1"},
                              login_steps=[{"type": "fill", "value": "{{secret.PW}}"}])
    job = build_refresh_job(persona, {"PW": "hunter2"})
    assert job["job_type"] == "persona_refresh"
    assert job["persona_id"] == 3
    assert job["login_steps"] == persona.login_steps
    assert job["settings"] == {"headers": {"X-A": "1"}, "secrets": {"PW": "hunter2"}}
    assert job["refresh_id"] == job["job_id"]


def test_refresh_job_omits_secrets_for_workspace_scoped_persona():
    from app.tasks.persona_tasks import build_refresh_job

    persona = SimpleNamespace(id=3, auth_headers=None, login_steps=[])
    assert "secrets" not in build_refresh_job(persona, None)["settings"]


# ── 2. Per-step DOM for proactive heal ───────────────────────────────────────

@pytest.mark.parametrize("flag,level,expected", [
    ("true", "full", True),
    ("true", "standard", False),
    ("false", "full", False),
    (None, "full", False),
])
def test_step_dom_needs_heal_flag_and_full_capture(monkeypatch, flag, level, expected):
    from app.worker import wants_step_dom, _settings_payload

    if flag is None:
        monkeypatch.delenv("PROACTIVE_HEAL_ENABLED", raising=False)
    else:
        monkeypatch.setenv("PROACTIVE_HEAL_ENABLED", flag)
    policy = {"capture_level": level}
    assert wants_step_dom(policy) is expected
    payload = _settings_payload({"data_policy": policy})
    assert payload.get("capture_step_dom", False) is expected


def test_step_doms_grouped_by_case_with_single_case_fallback():
    from app.tasks.heal_tasks import step_doms_by_case

    results = [
        SimpleNamespace(test_case_id=1, result_payload={"step_dom": {"s1": "<a>", "s2": ""}}),
        SimpleNamespace(test_case_id=None, result_payload={"step_dom": {"s9": "<b>"}}),
        SimpleNamespace(test_case_id=2, result_payload={"web_vitals": {}}),
        SimpleNamespace(test_case_id=3, result_payload=None),
    ]
    assert step_doms_by_case(results, run_case_id=7) == {1: {"s1": "<a>"}, 7: {"s9": "<b>"}}


def test_aggregator_keeps_evidence_in_result_payload():
    from app.tasks.result_aggregator import _case_payload, _evidence_payload

    res = {"console_errors": [{"type": "error", "text": "x"}] * 30,
           "step_dom": {"s1": "<div/>"}, "web_vitals": {"lcp": 1}}
    ev = _evidence_payload(res)
    assert len(ev["console_errors"]) == 20
    assert ev["step_dom"] == {"s1": "<div/>"}
    assert _case_payload(res)["web_vitals"] == {"lcp": 1}
    assert _evidence_payload({}) == {}


def test_ingestion_redacts_console_errors_and_step_dom():
    from app.services.redaction import redact_worker_result

    # Assembled at runtime: a JWT-shaped literal in source trips the gitleaks
    # history scan in CI. Header/payload are base64 of {"alg":"HS256"} and
    # {"sub":"1234567890"}; the signature is filler.
    jwt = ".".join(["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiIxMjM0NTY3ODkwIn0",
                    "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"])
    out = redact_worker_result({
        "console_errors": [{"type": "error", "text": f"bad token {jwt}"}],
        "test_results": [{"step_dom": {"s1": f"<p>{jwt}</p>"}}],
    })
    assert jwt not in out["console_errors"][0]["text"]
    assert jwt not in out["test_results"][0]["step_dom"]["s1"]


# ── 3. AI generation review stamp ────────────────────────────────────────────

class _CaseSession:
    def __init__(self, case):
        self.case, self.added, self.commits = case, [], 0

    async def get(self, model, pk):
        return self.case

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        pass


def _ai_case():
    return SimpleNamespace(id=1, project_id=1, is_ai_authored=True, steps=[],
                           name="gen", last_human_reviewed_at=None,
                           last_human_reviewed_by_id=None, executor=None)


async def _put_case(monkeypatch, case, principal):
    from app.api.endpoints import test_cases
    from app.models import TestCaseUpdate

    async def allow(*a, **kw):
        return True
    monkeypatch.setattr(test_cases.rbac_service, "has_permission", allow)
    session = _CaseSession(case)
    await test_cases.update_test_case(
        case_id=1, case_update=TestCaseUpdate(), session=session, principal=principal)
    return session


async def test_human_save_stamps_review_on_ai_case(monkeypatch):
    case = _ai_case()
    session = await _put_case(monkeypatch, case,
                              AuthPrincipal(user=MagicMock(id=42), api_key=None))
    assert case.last_human_reviewed_by_id == 42
    assert case.last_human_reviewed_at is not None
    assert session.commits == 1


async def test_api_key_save_is_not_a_review(monkeypatch):
    case = _ai_case()
    await _put_case(monkeypatch, case,
                    AuthPrincipal(user=MagicMock(id=42), api_key=MagicMock()))
    assert case.last_human_reviewed_at is None


def test_generation_does_not_prestamp_review():
    import inspect
    from app.api import case_generation
    src = inspect.getsource(case_generation)
    assert "last_human_reviewed_at=datetime.utcnow()" not in src


# ── 4. Failure-analysis evidence ─────────────────────────────────────────────

def test_network_failures_filtered_per_test():
    from app.tasks.analysis_tasks import network_failures_for

    events = [
        {"testCaseName": "a", "status": 500, "url": "/x", "method": "GET", "resourceType": "xhr",
         "requestHeaders": {"cookie": "secret"}},
        {"testCaseName": "a", "status": 200, "url": "/ok"},
        {"testCaseName": "b", "status": 404, "url": "/y"},
        "garbage",
    ]
    assert network_failures_for(events, "a") == [
        {"method": "GET", "url": "/x", "status": 500, "resourceType": "xhr"}]
    assert network_failures_for(None, "a") == []


def test_analysis_evidence_carries_console_errors(monkeypatch):
    from app.services import failure_analysis

    monkeypatch.setattr(failure_analysis, "get_provider",
                        lambda pid=None: SimpleNamespace(name="null"))
    report = failure_analysis.analyze_case_failure(
        test_name="t", error_message="boom", steps=[],
        network_failures=[{"status": 500}],
        console_errors=[{"type": "error", "text": "TypeError: x is undefined"}])
    assert report.evidence.console_errors[0]["text"].startswith("TypeError")
    assert report.evidence.network_failures == [{"status": 500}]


# ── 5. Flake fields ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,retries,score,flaky,conf", [
    (TestStatus.PASSED, 0, 0.0, False, None),
    (TestStatus.PASSED, 1, 0.0, True, None),      # pass-after-retry is never hidden
    (TestStatus.FAILED, 0, 0.0, False, 1.0),      # stable case: failure is real
    (TestStatus.FAILED, 0, 0.5, True, 0.5),       # coin-flip case
    (TestStatus.ERROR, 2, None, False, 1.0),      # no history yet
])
def test_flake_verdict(status, retries, score, flaky, conf):
    from app.tasks.result_aggregator import flake_verdict
    assert flake_verdict(status, retries, score) == (flaky, conf)


# ── 6. Visual baselines ──────────────────────────────────────────────────────

def test_baseline_purge_prefix_comes_from_the_case_not_the_key():
    """Stored keys used to be caller-supplied (`runs/`, another tenant's
    case folder…), so the purge prefix is built from the row's case id."""
    from app.services.purge import baseline_purge_prefix
    assert baseline_purge_prefix(12) == "baselines/12/"
    assert baseline_purge_prefix("12") == "baselines/12/"


class _BaselineSession:
    def __init__(self, objs):
        self.objs, self.added = objs, []

    async def get(self, model, pk):
        return self.objs.get(model.__name__)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass


def _allow_project(monkeypatch, module):
    async def allow(*a, **kw):
        return True
    monkeypatch.setattr(module.access_service, "has_project_access", allow)


async def test_create_baseline_refuses_other_tenants_keys(monkeypatch):
    from app.api import visual_baselines as vb

    _allow_project(monkeypatch, vb)
    session = _BaselineSession({"TestCase": SimpleNamespace(id=5, project_id=1)})
    principal = AuthPrincipal(user=MagicMock(id=1), api_key=None)
    for key in ("runs/77/screenshots/secret.png", "baselines/6/s/x.png", "runs/"):
        with pytest.raises(HTTPException) as exc:
            await vb.create_baseline(
                body=vb.VisualBaselineCreate(test_case_id=5, step_id="s", image_url=key),
                principal=principal, session=session)
        assert exc.value.status_code == 400


async def test_rollback_only_to_this_baselines_own_history(monkeypatch):
    from app.api import visual_baselines as vb

    _allow_project(monkeypatch, vb)
    baseline = SimpleNamespace(id=3, test_case_id=5, image_url="baselines/5/s/new.png")
    session = _BaselineSession({"VisualBaseline": baseline,
                                "TestCase": SimpleNamespace(id=5, project_id=1)})

    async def history(sess, bid):
        return [SimpleNamespace(changes={"image": "baselines/5/s/new.png",
                                         "previous_image": "baselines/5/s/old.png"})]
    monkeypatch.setattr(vb, "_history_rows", history)
    principal = AuthPrincipal(user=MagicMock(id=1), api_key=None)

    with pytest.raises(HTTPException) as exc:
        await vb.rollback_baseline(
            baseline_id=3, body=vb.BaselineRollback(image="runs/9/x.png"),
            principal=principal, session=session)
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await vb.rollback_baseline(
            baseline_id=3, body=vb.BaselineRollback(image="baselines/5/s/new.png"),
            principal=principal, session=session)
    assert exc.value.status_code == 409


# ── 7. Heal accept / reject ──────────────────────────────────────────────────

@pytest.mark.parametrize("step,old,refused", [
    ({"id": "a", "type": "click", "selector": "#go"}, "#go", False),
    ({"id": "a", "type": "expect-visible", "selector": "#err"}, "#err", True),
    ({"id": "a", "type": "mobile-expect-text", "selector": "~x"}, "~x", True),
    ({"id": "a", "type": "count-children", "selector": "ul"}, "ul", True),
    ({"id": "a", "type": "click", "selector": "#changed"}, "#go", True),   # stale
    (None, "#go", True),                                                    # step gone
])
def test_heal_refusal(step, old, refused):
    from app.api.heal_proposals import heal_refusal
    assert (heal_refusal(step, old) is not None) is refused


async def test_heal_accept_applies_to_action_step_and_persists(monkeypatch):
    from app.api import heal_proposals as hp

    proposal = SimpleNamespace(test_case_id=1, step_id="a", old_selector="#go",
                               new_selector="#go-v2", status="pending")
    original = [{"id": "a", "type": "click", "selector": "#go"}]
    case = MagicMock()
    case.steps = original

    async def editor(*a, **kw):
        return case
    monkeypatch.setattr(hp, "_check_editor_on_case", editor)
    flagged = []
    monkeypatch.setattr("sqlalchemy.orm.attributes.flag_modified",
                        lambda obj, attr: flagged.append(attr))

    async def no_revision(*a, **kw):
        return None
    monkeypatch.setattr("app.services.case_revisions.record_revision", no_revision)

    class S:
        async def get(self, model, pk):
            return proposal

        def add(self, obj):
            pass

        async def commit(self):
            pass

    out = await hp.accept_proposal(
        proposal_id=1, principal=AuthPrincipal(user=MagicMock(id=1), api_key=None), session=S())
    assert out == {"status": "accepted", "applied": True}
    assert case.steps[0]["selector"] == "#go-v2"
    assert case.steps is not original          # a new list, not an in-place edit
    assert flagged == ["steps"]


async def test_heal_reject_checks_access(monkeypatch):
    from app.api import heal_proposals as hp

    proposal = SimpleNamespace(test_case_id=1, status="pending")

    async def deny(*a, **kw):
        raise HTTPException(status_code=403, detail="nope")
    monkeypatch.setattr(hp, "_check_editor_on_case", deny)

    class S:
        async def get(self, model, pk):
            return proposal

    with pytest.raises(HTTPException) as exc:
        await hp.reject_proposal(
            proposal_id=1, principal=AuthPrincipal(user=MagicMock(id=1), api_key=None),
            session=S())
    assert exc.value.status_code == 403
    assert proposal.status == "pending"


def test_failure_analysis_is_queued_only_after_commit(monkeypatch):
    """Queued inside the aggregator's transaction, the task raced the commit
    that writes the run's network events and saw none of them."""
    from sqlalchemy import create_engine
    from sqlmodel import Session

    from app.tasks import analysis_tasks, result_aggregator

    queued = []
    monkeypatch.setattr(analysis_tasks.analyze_run_failures, "delay", queued.append)
    run = SimpleNamespace(total_tests=1, passed_tests=0, failed_tests=0,
                          status=None, error_message=None, completed_at=None,
                          duration_ms=None, started_at=None, id=7)
    with Session(create_engine("sqlite://")) as session:
        try:
            result_aggregator.update_run_from_progress(
                run, 7, {"total": "1", "completed": "1", "passed": "0", "failed": "1"}, session)
        except Exception:
            pass  # finalisation side effects beyond the queueing aren't under test
        assert queued == []
        session.commit()
        assert queued == [7]
        session.commit()
        assert queued == [7]  # once, not on every later commit


class _PersonaRunSession:
    def __init__(self, objs):
        self.objs = objs

    async def get(self, model, pk):
        return self.objs.get(model.__name__)


async def test_workspace_persona_needs_workspace_editor_to_run(monkeypatch):
    """A project editor (e.g. via a team) must not borrow a workspace-wide
    persona's live session."""
    from app.api import personas
    from app.api.endpoints import test_runs

    async def deny(*a, **kw):
        raise HTTPException(status_code=403, detail="Editor access required")
    monkeypatch.setattr(personas, "_ensure_editor", deny)

    async def allow(*a, **kw):
        return True
    monkeypatch.setattr(test_runs.access_service, "has_project_access", allow)

    async def ws(*a, **kw):
        return 5
    async def quota(*a, **kw):
        return True, 0, 0
    from app.services import billing
    monkeypatch.setattr(billing, "workspace_id_for_project", ws)
    monkeypatch.setattr(billing, "check_quota", quota)

    async def settings(*a, **kw):
        return {}
    monkeypatch.setattr(test_runs.test_service, "get_effective_settings", settings)

    async def no_cases(*a, **kw):
        return []
    # create_run now sizes the suite scope (targeted runs) before the persona
    # check; this fake session has no exec().
    monkeypatch.setattr(test_runs.test_service, "subtree_cases", no_cases)

    session = _PersonaRunSession({
        "TestSuite": SimpleNamespace(id=1, project_id=3),
        "Persona": SimpleNamespace(id=9, workspace_id=5, project_id=None),
    })
    with pytest.raises(HTTPException) as exc:
        await test_runs.create_run(
            suite_id=1, case_id=None, browser=None, device=None, tags=None,
            context=test_runs.RunCreateContext(persona_id=9), session=session,
            principal=AuthPrincipal(user=MagicMock(id=1), api_key=None))
    assert exc.value.status_code == 403


async def test_project_persona_refresh_needs_project_access(monkeypatch):
    """Refresh runs the login steps with the project's decrypted secrets."""
    from app.api import personas

    async def ws_editor(*a, **kw):
        return None
    monkeypatch.setattr(personas, "_ensure_editor", ws_editor)

    async def no_project(*a, **kw):
        return False
    from app.services.access_service import access_service
    monkeypatch.setattr(access_service, "has_project_access", no_project)

    persona = SimpleNamespace(id=9, workspace_id=5, project_id=3,
                              login_steps=[{"type": "goto", "value": "/"}])
    queued = []
    from app.tasks import persona_tasks
    monkeypatch.setattr(persona_tasks.refresh_persona_session, "delay", queued.append)

    with pytest.raises(HTTPException) as exc:
        await personas.refresh_persona(
            workspace_id=5, persona_id=9,
            principal=AuthPrincipal(user=MagicMock(id=1), api_key=None),
            session=_PersonaRunSession({"Persona": persona}))
    assert exc.value.status_code == 403
    assert queued == []
