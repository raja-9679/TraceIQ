import asyncio

from traceiq_mcp.client import TraceIQClient


def test_create_run_sends_case_ids_in_body(monkeypatch):
    sent = {}

    async def fake_request(self, method, path, **kw):
        sent.update(method=method, path=path, params=kw.get("params"), json=kw.get("json"))
        return []

    monkeypatch.setattr(TraceIQClient, "_request", fake_request)
    client = TraceIQClient(base_url="http://x", api_key="tiq_test")
    asyncio.run(client.create_run(suite_id=4, case_ids=[7, 8]))
    assert sent["params"] == {"suite_id": 4}
    assert sent["json"]["case_ids"] == [7, 8]


def test_impact_analysis_sends_suite_id(monkeypatch):
    sent = {}

    async def fake_request(self, method, path, **kw):
        sent.update(json=kw.get("json"))
        return {"matched_cases": [], "cases_without_code_paths": 0, "unmatched_files": []}

    monkeypatch.setattr(TraceIQClient, "_request", fake_request)
    client = TraceIQClient(base_url="http://x", api_key="tiq_test")
    asyncio.run(client.impact_analysis(1, ["a.ts"], False, suite_id=4))
    assert sent["json"]["suite_id"] == 4


def test_create_run_sends_data_seed(monkeypatch):
    sent = {}

    async def fake_request(self, method, path, **kw):
        sent.update(json=kw.get("json"))
        return []

    monkeypatch.setattr(TraceIQClient, "_request", fake_request)
    client = TraceIQClient(base_url="http://x", api_key="tiq_test")
    asyncio.run(client.create_run(suite_id=4, data_seed="repro-1"))
    assert sent["json"]["data_seed"] == "repro-1"
    asyncio.run(client.create_run(suite_id=4))
    assert "data_seed" not in sent["json"]


def test_suite_settings_proposal_passes_hooks_through(monkeypatch):
    from traceiq_mcp.tools import authoring
    sent = {}

    async def fake_request(self, method, path, **kw):
        sent.update(path=path, json=kw.get("json"))
        return {"id": 1, "project_id": 2, "action": "update_suite_settings", "status": "pending"}

    monkeypatch.setattr(TraceIQClient, "_request", fake_request)
    monkeypatch.setenv("TRACEIQ_API_KEY", "tiq_test")
    monkeypatch.setenv("TRACEIQ_BASE_URL", "https://x")
    hooks = {"setup_steps": [{"type": "http-request", "value": "/seed"}],
             "teardown_steps": [{"type": "http-request", "value": "/reset"}], "data_seed": "s"}
    fn = getattr(authoring.propose_update_suite_settings, "fn", authoring.propose_update_suite_settings)
    asyncio.run(fn(project_id=2, test_suite_id=3, settings=hooks, rationale="seed data"))
    assert sent["json"]["action"] == "update_suite_settings"
    assert sent["json"]["payload"]["settings"] == hooks
