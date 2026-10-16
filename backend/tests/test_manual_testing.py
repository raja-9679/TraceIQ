"""Manual cases, manual runs and the sign-off rules (WP12d) — no database."""
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from app.models import ExecutorType, TestCase
from app.services import manual_testing as mt


def _step(instruction="Open the invoice", expected="VAT is shown", type_=mt.MANUAL_STEP):
    return {"id": "s", "type": type_, "value": instruction, "params": {"expected": expected}}


def _case(cid, executor="ui_playwright", steps=None):
    return TestCase(id=cid, name=f"c{cid}", executor=executor, steps=steps or [])


# --- dispatch exclusion ---------------------------------------------------------


def test_manual_cases_are_never_expanded_into_worker_jobs():
    from app.worker import _expand_cases
    cases = [_case(1), _case(2, "manual", [_step()]), _case(3, ExecutorType.MANUAL),
             _case(4, "load")]
    assert [c.id for c, _, _ in _expand_cases(cases)] == [1, 4]


def test_exclude_manual_accepts_enum_and_string_executors():
    cases = [_case(1, ExecutorType.MANUAL), _case(2, "manual"), _case(3)]
    assert [c.id for c in mt.exclude_manual(cases)] == [3]


def test_dispatch_skips_manual_cases_in_every_mode(monkeypatch):
    """dispatch_cases_to_queue / separate / parallel all expand through
    _expand_cases, so a mixed list yields jobs for the automated cases only."""
    from app import worker

    sent = []

    class Pipe:
        def hset(self, *a, **k): pass
        def execute(self): pass

    monkeypatch.setattr(worker, "_ensure_consumer_groups", lambda: None)
    monkeypatch.setattr(worker, "_local_queue_key", lambda run, s: None)
    monkeypatch.setattr(worker.redis_client, "pipeline", lambda: Pipe())
    monkeypatch.setattr(worker, "_enqueue_job", lambda pipe, stream, lk, rid, job, region=None: sent.append(job))
    monkeypatch.setattr(worker, "_case_payload", lambda case, *a, **k: {"id": case.id})
    run = NS(id=9, browser="chromium", device=None, region=None)
    cases = [_case(1), _case(2, "manual", [_step()])]
    worker.dispatch_cases_to_queue(run, cases, {}, None)
    worker.dispatch_parallel_jobs(run, cases, {}, None)
    worker.dispatch_separate_jobs(run, [{"type": "sub_suite", "id": 1, "name": "x", "test_cases": cases}], {}, None)
    assert [j.get("test_case_id") or j["test_cases"][0]["id"] for j in sent] == [1, 1, 1]


def test_a_manual_run_is_left_alone_by_the_celery_task(monkeypatch):
    from app import worker

    run = NS(id=5, executor=ExecutorType.MANUAL, status="pending")

    class Sess:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, model, rid): return run
        def add(self, obj): raise AssertionError("a manual run must not be modified")
        def commit(self): raise AssertionError("a manual run must not be modified")

    monkeypatch.setattr(worker, "Session", Sess)
    worker.run_test_suite.run(5)
    assert run.status == "pending"


# --- case validation ---------------------------------------------------------------


def test_manual_steps_need_an_instruction_and_stay_manual_only():
    mt.validate_manual_steps([_step(), _step(expected="")])
    with pytest.raises(mt.ManualTestingError, match="only 'manual-step'"):
        mt.validate_manual_steps([_step(), {"type": "goto", "value": "/"}])
    with pytest.raises(mt.ManualTestingError, match="instruction"):
        mt.validate_manual_steps([_step(instruction="  ")])
    with pytest.raises(mt.ManualTestingError, match="at most"):
        mt.validate_manual_steps([_step()] * (mt.MAX_STEPS + 1))


def test_case_endpoint_infers_manual_and_refuses_mixed_steps():
    from app.api.endpoints.test_cases import _check_manual
    case = _case(1, steps=[_step()])
    _check_manual(case, {mt.MANUAL_STEP})
    assert case.executor == ExecutorType.MANUAL
    mixed = _case(2, steps=[_step(), {"type": "click", "selector": "#x"}])
    with pytest.raises(HTTPException) as exc:
        _check_manual(mixed, {mt.MANUAL_STEP, "click"})
    assert exc.value.status_code == 400
    # An automated case is untouched.
    auto = _case(3, steps=[{"type": "goto", "value": "/"}])
    _check_manual(auto, {"goto"})
    assert auto.executor == "ui_playwright"


def test_manual_step_is_in_the_catalog_and_exempt_from_worker_skew():
    from app.api.agent_reference import _STEP_TYPES
    from app.services import worker_fleet as wf
    assert mt.MANUAL_STEP in {s["type"] for s in _STEP_TYPES}
    assert mt.MANUAL_STEP not in wf.missing_step_types("web", ["goto"], [mt.MANUAL_STEP, "goto"])


# --- recording and totals ---------------------------------------------------------


def test_outcomes_map_onto_the_existing_statuses():
    assert mt.outcome_status("passed") == "passed"
    assert mt.outcome_status("failed") == "failed"
    assert mt.outcome_status("blocked") == "error"
    assert mt.outcome_status("skipped") == "pending"
    assert mt.outcome_status(None) == "pending"


def test_run_summary_blocked_fails_and_all_skipped_is_not_a_pass():
    s = mt.summarize(["passed", "blocked", "skipped", None])
    assert (s["passed"], s["failed"], s["unrecorded"], s["status"]) == (1, 1, 1, "failed")
    assert mt.summarize(["passed", "skipped"])["status"] == "passed"
    assert mt.summarize(["skipped", "skipped"])["status"] == "failed"


def test_record_validation():
    steps = mt.validate_record("failed", "broke", [{"outcome": "passed"}, {"outcome": "failed", "note": "x"}], 2, 1000)
    assert steps[1] == {"index": 1, "outcome": "failed", "note": "x"}
    for bad in (("maybe", None, None, 0, None),
                ("passed", None, [{"outcome": "passed"}], 0, None),
                ("passed", None, [{"outcome": "nope"}], 1, None),
                ("passed", "x" * (mt.MAX_NOTES + 1), None, 0, None),
                ("passed", None, None, 0, -1)):
        with pytest.raises(mt.ManualTestingError):
            mt.validate_record(*bad)


def test_attachments_are_sniffed_not_trusted():
    assert mt.sniff_image(b"\x89PNG\r\n\x1a\n....") == ("image/png", "png")
    assert mt.sniff_image(b"\xff\xd8\xff\xe0") == ("image/jpeg", "jpg")
    assert mt.sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == ("image/webp", "webp")
    assert mt.sniff_image(b"<html><script>") is None
    assert mt.sniff_image(b"%PDF-1.7") is None


# --- separation of duties ---------------------------------------------------------


def _run(rid, user_id, executor="manual"):
    return NS(id=rid, user_id=user_id, executor=executor)


def _res(run_id, case_id, tester):
    return NS(test_run_id=run_id, test_case_id=case_id, result_payload={"tester_id": tester})


def test_executors_are_run_starters_and_testers_of_manual_runs_only():
    runs = [_run(1, 10), _run(2, 20, executor="ui_playwright")]
    results = [_res(1, 100, 11), _res(1, 101, 12), _res(2, 102, 99)]
    assert mt.executor_ids(runs, results) == {10, 11, 12}
    # Scoped to cases: only testers of those cases count (the starter always does).
    assert mt.executor_ids(runs, results, {100}) == {10, 11}


def test_four_eyes_rule():
    assert mt.signoff_conflict(11, {10, 11}, required=True)
    assert not mt.signoff_conflict(30, {10, 11}, required=True)
    assert not mt.signoff_conflict(11, {10, 11}, required=False)


def test_instance_policy_is_a_floor_for_separation(monkeypatch):
    from app.services import proposal_policy
    monkeypatch.setattr(proposal_policy, "effective", lambda k: k == "REQUIRE_SEPARATE_APPROVER")
    assert proposal_policy.separation_required(workspace_flag=False)


def test_signoff_gate_check():
    from app.services.release_readiness import signoff_check
    missing = signoff_check(None, "for this project")
    assert not missing.passed and missing.missing_data
    assert signoff_check(("approve", "v2.4", "Ana"), "x").passed
    rejected = signoff_check(("reject", "v2.4", "Ana"), "x")
    assert not rejected.passed and not rejected.missing_data


def test_signoff_check_is_off_by_default():
    from app.models import DEFAULT_QUALITY_GATE
    assert DEFAULT_QUALITY_GATE.require_manual_signoff is False
