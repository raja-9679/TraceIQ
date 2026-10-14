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
