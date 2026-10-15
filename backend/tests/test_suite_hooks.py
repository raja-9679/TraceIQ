"""Suite setup/teardown hooks, data seeds and run context (WP5).

The worker-side ordering rules are tested in
execution-engine/src/core/suite-hooks.test.ts; this covers what the backend
decides: which frames a case gets, validation, the seed, and how a worker
report becomes result_payload.
"""
from types import SimpleNamespace

import pytest

from app.services import suite_hooks
from app.services.suite_hooks import (
    SuiteHookError,
    hook_chain_sync,
    hook_payload,
    normalize_seed,
    run_short_token,
    validate_suite_settings,
)
from app.services.test_service import _merge_settings, INHERITED_EXECUTION_FLAGS


class FakeSession:
    def __init__(self, suites):
        self.suites = {s.id: s for s in suites}
        self.info = {}
        self.gets = 0

    def get(self, _model, ident):
        self.gets += 1
        return self.suites.get(ident)


def suite(id, parent_id=None, inherit=True, setup=None, teardown=None, **settings):
    blob = dict(settings)
    if setup is not None:
        blob["setup_steps"] = [{"id": f"s{id}{i}", "type": "http-request", "value": v} for i, v in enumerate(setup)]
    if teardown is not None:
        blob["teardown_steps"] = [{"id": f"t{id}{i}", "type": "http-request", "value": v} for i, v in enumerate(teardown)]
    project_id = blob.pop("project_id", 1)
    return SimpleNamespace(id=id, name=f"S{id}", parent_id=parent_id, inherit_settings=inherit,
                           settings=blob, project_id=project_id)


# ---------------------------------------------------------------------------
# Hook chain (settings inheritance)
# ---------------------------------------------------------------------------

def test_chain_is_outermost_first_and_skips_suites_without_hooks():
    session = FakeSession([
        suite(1, setup=["root-up"], teardown=["root-down"]),
        suite(2, parent_id=1),                      # no hooks: no frame
        suite(3, parent_id=2, setup=["leaf-up"]),
    ])
    chain = hook_chain_sync(3, session)
    assert [f["suite_id"] for f in chain] == [1, 3]
    assert chain[0]["setup_steps"][0]["value"] == "root-up"
    assert chain[0]["teardown_steps"][0]["value"] == "root-down"
    assert chain[1]["teardown_steps"] == []


def test_inherit_settings_false_cuts_the_chain_but_keeps_the_suites_own_hooks():
    session = FakeSession([
        suite(1, setup=["root-up"]),
        suite(2, parent_id=1, inherit=False, setup=["isolated-up"]),
        suite(3, parent_id=2, setup=["leaf-up"]),
    ])
    assert [f["suite_id"] for f in hook_chain_sync(3, session)] == [2, 3]
    assert [f["suite_id"] for f in hook_chain_sync(2, session)] == [2]


def test_chain_never_crosses_into_another_project():
    session = FakeSession([
        suite(1, setup=["foreign"], project_id=99),
        suite(2, parent_id=1, setup=["mine"]),
    ])
    assert [f["suite_id"] for f in hook_chain_sync(2, session)] == [2]


def test_chain_is_cached_per_session_and_survives_a_cycle():
    a = suite(1, parent_id=2, setup=["a"])
    b = suite(2, parent_id=1, setup=["b"])
    session = FakeSession([a, b])
    assert [f["suite_id"] for f in hook_chain_sync(1, session)] == [2, 1]
    before = session.gets
    hook_chain_sync(1, session)
    assert session.gets == before
    assert hook_chain_sync(None, session) == []


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_validate_assigns_step_ids_and_drops_empty_hooks():
    out = validate_suite_settings({
        "headers": {"A": "1"},
        "setup_steps": [{"type": "http-request", "value": "https://x"}],
        "teardown_steps": [],
        "data_seed": "  ",
    })
    assert out["headers"] == {"A": "1"}
    assert out["setup_steps"][0]["id"].startswith("step-")
    assert "teardown_steps" not in out and "data_seed" not in out


@pytest.mark.parametrize("bad, message", [
    ({"setup_steps": "nope"}, "must be a list"),
    ({"setup_steps": [{"value": "x"}]}, "with a type"),
    ({"teardown_steps": [{"type": "goto", "params": "x"}]}, "params must be an object"),
    ({"setup_steps": [{"type": "goto"}] * 51}, "more than 50"),
    ({"data_seed": "x" * 65}, "longer than 64"),
    ({"data_seed": {"a": 1}}, "string or a number"),
])
def test_validate_rejects_malformed_hooks(bad, message):
    with pytest.raises(SuiteHookError, match=message):
        validate_suite_settings(bad)


def test_seed_normalisation():
    assert normalize_seed(1234) == "1234"
    assert normalize_seed(" abc ") == "abc"
    assert normalize_seed("") is None
    assert normalize_seed(None) is None
    assert normalize_seed(True) is None


def test_data_seed_inherits_down_the_suite_chain_like_other_flags():
    assert "data_seed" in INHERITED_EXECUTION_FLAGS
    assert _merge_settings({"data_seed": "parent"}, {})["data_seed"] == "parent"
    assert _merge_settings({"data_seed": "parent"}, {"data_seed": "child"})["data_seed"] == "child"
    # Hooks are chained per case, never merged into effective settings.
    assert "setup_steps" not in _merge_settings({"setup_steps": [{"type": "goto"}]}, {})


def test_accepting_a_settings_proposal_keeps_hook_keys():
    from app.api.agent_ownership import _proposed_suite_settings
    existing = {"headers": {"A": "1"}, "teardown_steps": [{"id": "t", "type": "goto"}]}
    merged = _proposed_suite_settings(existing, {
        "settings": {"headers": {"B": "2"}, "setup_steps": [{"type": "http-request"}], "data_seed": 7},
    })
    assert merged["headers"] == {"A": "1", "B": "2"}
    assert merged["teardown_steps"] == [{"id": "t", "type": "goto"}]
    assert merged["setup_steps"][0]["type"] == "http-request"
    assert merged["data_seed"] == "7"
    with pytest.raises(SuiteHookError):
        _proposed_suite_settings({}, {"settings": {"setup_steps": "x"}})


# ---------------------------------------------------------------------------
# Run context
# ---------------------------------------------------------------------------

def test_run_short_token_is_stable_short_and_not_the_id():
    a = run_short_token(42)
    assert a == run_short_token(42)
    assert len(a) == 8 and a.isalnum() and a == a.lower()
    assert a != run_short_token(43)


def test_settings_payload_carries_the_run_context():
    from datetime import datetime
    from app.worker import _settings_payload
    ctx = suite_hooks.run_context(SimpleNamespace(id=5), "seed-1", datetime(2026, 10, 15, 9, 30, 1, 999))
    assert ctx == {"id": 5, "short": run_short_token(5), "started_at": "2026-10-15T09:30:01Z",
                   "data_seed": "seed-1"}
    assert _settings_payload({"run_context": ctx})["run"] == ctx
    assert "run" not in _settings_payload({})


def test_case_payload_ships_the_cases_own_hook_chain():
    from app.worker import _case_payload
    session = FakeSession([suite(1, setup=["up"]), suite(2, parent_id=1, teardown=["down"])])
    case = SimpleNamespace(id=9, name="c", steps=[], test_suite_id=2, executor=None)
    payload = _case_payload(case, session=session)
    assert [f["suite_id"] for f in payload["suite_hooks"]] == [1, 2]
    assert "suite_hooks" not in _case_payload(case)
    bare = SimpleNamespace(id=9, name="c", steps=[], test_suite_id=3, executor=None)
    assert "suite_hooks" not in _case_payload(bare, session=FakeSession([suite(3)]))


# ---------------------------------------------------------------------------
# Worker report -> result_payload
# ---------------------------------------------------------------------------

def test_hook_payload_normalises_and_derives_warnings():
    out = hook_payload({
        "setup": [{"suite_id": 1, "suite_name": "Root", "status": "passed", "duration_ms": 12,
                   "steps": [{"index": 0, "type": "http-request", "status": "passed"}, "junk"]}],
        "teardown": [{"suite_id": 1, "suite_name": "Root", "status": "failed", "error": "step 1: 500",
                      "steps": [{"index": 0, "type": "http-request", "status": "failed", "error": "500"}]},
                     {"suite_id": "x"}],
        "cleanup": [{"method": "delete", "url": "/items/{{id}}", "status": "failed", "http_status": 500,
                     "error": "unexpected status 500", "origin": "case", "evil": "<script>"},
                    {"method": "DELETE", "url": "/p", "status": "weird", "origin": "setup", "suite_id": 1}],
    })
    assert out["hooks"]["setup"][0]["steps"] == [{"index": 0, "type": "http-request", "status": "passed"}]
    assert len(out["hooks"]["teardown"]) == 1
    assert out["cleanup"][0] == {"method": "DELETE", "url": "/items/{{id}}", "status": "failed",
                                 "origin": "case", "http_status": 500, "error": "unexpected status 500"}
    assert out["cleanup"][1]["status"] == "failed" and out["cleanup"][1]["suite_id"] == 1
    assert out["hook_warnings"][0] == "Teardown failed (Root): step 1: 500"
    assert out["hook_warnings"][1].startswith("Cleanup DELETE /items/{{id}} failed")
    assert len(out["hook_warnings"]) == 3


def test_hook_payload_ignores_absent_or_malformed_reports():
    assert hook_payload(None) == {}
    assert hook_payload("x") == {}
    assert hook_payload({"setup": [], "teardown": [], "cleanup": []}) == {}


def test_aggregator_evidence_payload_includes_hooks():
    from app.tasks.result_aggregator import _evidence_payload
    out = _evidence_payload({"hook_report": {"cleanup": [{"method": "DELETE", "url": "/x", "status": "ok"}]}}, 1)
    assert out["cleanup"][0]["status"] == "ok"
    assert "hook_warnings" not in out
