import asyncio

from traceiq_mcp.client import TraceIQClient
from traceiq_mcp.schemas.quality import QualityGateResult, ReleaseReadiness

GATE = {
    "project_id": 3, "passed": False, "git_commit": "abc", "evaluated_run_ids": [9],
    "checks": [
        {"name": "pass_rate", "passed": True, "actual": "100.0%", "threshold": ">= 100.0%"},
        # A check name this client has never heard of must pass through.
        {"name": "critical_passed", "passed": False, "actual": "1 of 2 passed",
         "threshold": "all critical passed", "detail": "no result for commit abc: pay",
         "missing_data": True},
        {"name": "some_future_check", "passed": True, "actual": "x", "threshold": "y",
         "new_field": 1},
    ],
}


def test_gate_accepts_new_and_unknown_checks():
    g = QualityGateResult.model_validate(GATE)
    assert [c.name for c in g.checks][1:] == ["critical_passed", "some_future_check"]
    assert g.checks[1].missing_data is True and g.checks[0].missing_data is False


def test_release_readiness_validates_full_payload():
    r = ReleaseReadiness.model_validate({
        "project_id": 3, "verdict": "insufficient_data", "scope": "commit",
        "git_commit": "abc", "reasons": ["critical_passed: 1 of 2 passed"],
        "gate": GATE,
        "runs": [{"id": 9, "status": "passed", "created_at": "2026-10-15T10:00:00",
                  "browser": "chromium", "total_tests": 2, "passed_tests": 2}],
        "critical_cases": [
            {"case_id": 1, "name": "login", "status": "passed", "run_id": 9},
            {"case_id": 2, "name": "pay", "status": "not_run", "detail": "no run for this commit",
             "last_status": "passed", "last_at": "2026-10-10T10:00:00", "last_commit": "old"}],
        "quarantines": [{"flake_record_id": 4, "case_id": 5, "case_name": "card",
                         "flake_score": 0.5, "expires_at": "2026-10-20T00:00:00",
                         "owner": {"id": 1, "email": "a@b.c", "full_name": None}}],
        "requirements": [{"ref": "REQ-1", "status": "partial",
                          "cases": [{"case_id": 1, "name": "login", "status": "passed"},
                                    {"case_id": 2, "name": "pay", "status": "not_run"}]}],
        "recent_commits": [{"git_commit": "abc", "last_run_at": "2026-10-15T10:00:00", "runs": 1}],
    })
    assert r.verdict == "insufficient_data"
    assert r.critical_cases[1].status == "not_run"
    assert r.quarantines[0].owner.email == "a@b.c"


def test_client_calls_readiness_endpoint(monkeypatch):
    sent = {}

    async def fake_request(self, method, path, **kw):
        sent.update(method=method, path=path, params=kw.get("params"))
        return {}

    monkeypatch.setattr(TraceIQClient, "_request", fake_request)
    client = TraceIQClient(base_url="http://x", api_key="tiq_test")
    asyncio.run(client.release_readiness(3, git_branch="main"))
    assert sent == {"method": "GET", "path": "/api/projects/3/release-readiness",
                    "params": {"git_branch": "main"}}
