"""WP8 fleet & infrastructure: pure logic (no DB, no Redis).

    ./run-tests.sh tests/test_fleet_wp8.py -q
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime

import pytest
from fastapi import HTTPException

from app.core import client_ip as cip
from app.core.worker_auth import as_list_lines, mtls_verdict, require_worker, worker_secret_ok
from app.services import worker_fleet as wf
from app.services.network_policy import NetworkPolicy, normalize_host, resolve_egress_policy


# --- client IP / CIDR allowlists -------------------------------------------

TRUSTED = cip.parse_cidrs(["172.18.0.0/16"])


def test_peer_address_is_used_when_no_proxy_is_trusted():
    assert cip.resolve_client_ip("203.0.113.9", "1.2.3.4", []) == "203.0.113.9"


def test_forwarded_for_is_ignored_from_an_untrusted_peer():
    # An attacker connecting directly cannot choose the address the allowlist sees.
    assert cip.resolve_client_ip("203.0.113.9", "10.0.0.1", TRUSTED) == "203.0.113.9"


def test_forwarded_for_is_walked_right_to_left_past_trusted_hops():
    # client-supplied junk, real client, then our nginx
    xff = "10.0.0.1, 198.51.100.7, 172.18.0.3"
    assert cip.resolve_client_ip("172.18.0.2", xff, TRUSTED) == "198.51.100.7"


def test_garbage_hop_fails_every_match():
    ip = cip.resolve_client_ip("172.18.0.2", "not-an-ip", TRUSTED)
    assert ip == "not-an-ip"
    assert not cip.allowed(ip, ["0.0.0.0/0"])


def test_allowlist_semantics():
    assert cip.allowed("1.2.3.4", None)
    assert cip.allowed("1.2.3.4", [])
    assert cip.allowed("10.1.2.3", ["10.0.0.0/8"])
    assert not cip.allowed("11.1.2.3", ["10.0.0.0/8"])
    assert cip.allowed("::ffff:10.1.2.3", ["10.0.0.0/8"])      # dual-stack peer
    assert cip.allowed("2001:db8::5", ["2001:db8::/32"])
    # A list that is non-empty but entirely unparseable denies (fail closed).
    assert not cip.allowed("10.1.2.3", ["nonsense"])


def test_normalize_cidrs_validates_and_canonicalises():
    assert cip.normalize_cidrs(["10.1.2.3/8", "192.0.2.1"]) == ["10.0.0.0/8", "192.0.2.1/32"]
    assert cip.normalize_cidrs([]) is None
    with pytest.raises(ValueError):
        cip.normalize_cidrs(["10.0.0.0/33"])


def test_list_settings_accept_json_or_commas():
    assert cip.as_list('["a", "b"]') == ["a", "b"]
    assert cip.as_list("a, b,,c") == ["a", "b", "c"]
    assert cip.as_list(None) == []
    assert as_list_lines("CN=w1,O=Corp\nCN=w2,O=Corp") == ["CN=w1,O=Corp", "CN=w2,O=Corp"]


# --- worker secret + mTLS ----------------------------------------------------

def test_worker_secret(monkeypatch):
    from app.core import worker_auth
    monkeypatch.setattr(worker_auth.settings, "WEBHOOK_SECRET", "s3cret")
    assert worker_secret_ok("s3cret")
    assert not worker_secret_ok("")
    assert not worker_secret_ok("nope")
    with pytest.raises(HTTPException) as exc:
        require_worker("nope")
    assert exc.value.status_code == 403


def test_mtls_off_allows_everything():
    assert mtls_verdict("1.2.3.4", {}, required=False, trusted=[], allowed_subjects=[]) == ""


def test_mtls_header_only_believed_from_a_trusted_proxy():
    headers = {"x-client-verify": "SUCCESS"}
    assert "trusted proxy" in mtls_verdict("203.0.113.9", headers, required=True,
                                            trusted=TRUSTED, allowed_subjects=[])
    assert mtls_verdict("172.18.0.2", headers, required=True,
                        trusted=TRUSTED, allowed_subjects=[]) == ""


def test_mtls_requires_success_and_an_allowed_subject():
    assert "no verified" in mtls_verdict("172.18.0.2", {"x-client-verify": "NONE"},
                                         required=True, trusted=TRUSTED, allowed_subjects=[])
    h = {"x-client-verify": "SUCCESS", "x-client-cert-subject": "CN=rogue"}
    assert "subject" in mtls_verdict("172.18.0.2", h, required=True, trusted=TRUSTED,
                                     allowed_subjects=["CN=worker,O=Corp"])
    h["x-client-cert-subject"] = "CN=worker,O=Corp"
    assert mtls_verdict("172.18.0.2", h, required=True, trusted=TRUSTED,
                        allowed_subjects=["CN=worker,O=Corp"]) == ""


def test_mtls_middleware_guards_only_internal_paths(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.core.worker_auth import WorkerMTLSMiddleware
    from app.services import instance_settings

    values = {"REQUIRE_WORKER_MTLS": True, "TRUSTED_PROXY_CIDRS": ["127.0.0.0/8", "testclient/32"],
              "WORKER_MTLS_ALLOWED_SUBJECTS": []}
    monkeypatch.setattr(instance_settings, "effective", lambda k: values.get(k))
    app = FastAPI()
    app.add_middleware(WorkerMTLSMiddleware)

    @app.get("/api/internal/ping")
    def ping():
        return {"ok": True}

    @app.get("/api/public")
    def public():
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/api/public").status_code == 200
    # TestClient's peer is "testclient" — not an IP, so never a trusted proxy.
    r = client.get("/api/internal/ping", headers={"X-Client-Verify": "SUCCESS"})
    assert r.status_code == 403
    values["REQUIRE_WORKER_MTLS"] = False
    assert client.get("/api/internal/ping").status_code == 200


# --- regions / fleet -----------------------------------------------------------

def test_region_labels():
    assert wf.normalize_region(None) is None
    assert wf.normalize_region("  ") is None
    assert wf.normalize_region("EU-West") == "eu-west"
    for bad in ("eu west", "a:b", "-eu", "x" * 40):
        with pytest.raises(wf.InvalidRegion):
            wf.normalize_region(bad)
    assert wf.stream_for("jobs:pending", None) == "jobs:pending"
    assert wf.stream_for("jobs:pending", "eu") == "jobs:pending:eu"


def test_version_skew_is_judged_per_worker_kind():
    catalog = ["click", "goto", "load-test", "mobile-tap", "totp"]
    assert wf.missing_step_types("web", ["click", "goto"], catalog) == ["totp"]
    assert wf.missing_step_types("web", ["click", "goto", "totp"], catalog) == []
    assert wf.missing_step_types("mobile", ["mobile-tap"], catalog) == []
    assert wf.missing_step_types("mobile", ["x"], catalog) == ["mobile-tap"]
    # An image older than WP8 reports nothing: unknown, not "everything missing".
    assert wf.missing_step_types("web", [], catalog) == []


def test_the_real_catalog_matches_the_current_web_worker():
    import re
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "execution-engine/src/core/test-executor.ts"
    if not src.exists():
        pytest.skip("engine source not mounted")
    m = re.search(r"SUPPORTED_STEP_TYPES: readonly string\[\] = \[(.*?)\];", src.read_text(), re.S)
    worker_types = re.findall(r"'([a-z0-9-]+)'", m.group(1))
    assert wf.missing_step_types("web", worker_types, wf.catalog_step_types()) == []


class FakeRedis:
    """Just enough async Redis for fleet_snapshot."""

    def __init__(self):
        self.kv, self.z, self.streams = {}, {}, {}

    async def zrange(self, key, a, b, withscores=False):
        return sorted(self.z.get(key, {}).items(), key=lambda kv: kv[1])

    async def mget(self, keys):
        return [self.kv.get(k) for k in keys]

    async def zrem(self, key, *members):
        for m in members:
            self.z.get(key, {}).pop(m, None)

    async def scan_iter(self, match=None, count=None):
        prefix = match.rstrip("*")
        for name in list(self.streams):
            if name.startswith(prefix):
                yield name

    async def xlen(self, stream):
        return self.streams.get(stream, {}).get("length", 0)

    async def xinfo_groups(self, stream):
        s = self.streams.get(stream)
        if not s:
            raise Exception("no such key")
        return [{"name": s["group"], "pending": s["pending"], "lag": s["lag"], "consumers": 1}]

    async def xpending(self, stream, group):
        s = self.streams.get(stream) or {}
        return {"pending": s.get("pending", 0),
                "consumers": [{"name": n, "pending": c} for n, c in s.get("consumers", {}).items()]}

    def pipeline(self):
        outer = self

        class P:
            def __init__(self):
                self.ops = []

            def set(self, k, v, ex=None):
                self.ops.append(lambda: outer.kv.__setitem__(k, v))

            def zadd(self, k, mapping):
                self.ops.append(lambda: outer.z.setdefault(k, {}).update(mapping))

            async def execute(self):
                for op in self.ops:
                    op()
        return P()


def _hb(worker_id, **kw):
    return json.dumps({"worker_id": worker_id, "kind": "web", "step_types": [],
                       "streams": ["jobs:pending"], **kw})


def test_fleet_snapshot_joins_heartbeats_streams_and_warnings():
    r = FakeRedis()
    now = time.time()
    r.kv["workers:hb:w1"] = _hb("w1", current_job={"job_id": "j", "run_id": 1},
                                step_types=["goto"], region="eu", streams=["jobs:pending:eu", "jobs:pending"])
    r.kv["workers:hb:w2"] = _hb("w2", region='bad"label', kind="evil")
    r.z["workers:index"] = {"w1": now, "w2": now, "lost": now - 60, "ancient": now - 3600}
    r.streams = {
        "jobs:pending": {"group": "execution-workers", "length": 10, "pending": 1, "lag": 0,
                         "consumers": {"w1": 1}},
        "jobs:pending:ap": {"group": "execution-workers", "length": 2, "pending": 0, "lag": 2},
        "jobs:mobile:pending": {"group": "mobile-workers", "length": 0, "pending": 0, "lag": 0},
        "jobs:dead-letter": {"group": "x", "length": 3, "pending": 0, "lag": 0},
    }
    snap = asyncio.run(wf.fleet_snapshot(r))
    by_id = {w["worker_id"]: w for w in snap["workers"]}
    assert by_id["w1"]["alive"] and by_id["w1"]["current_job"]["job_id"] == "j"
    assert by_id["w1"]["version_skew"]                       # reports only "goto"
    assert by_id["w2"]["region"] is None and by_id["w2"]["kind"] == "web"   # sanitised
    assert not by_id["lost"]["alive"]
    assert "ancient" not in by_id and "ancient" not in r.z["workers:index"]  # pruned
    assert snap["summary"] == {"alive": 2, "lost": 1, "busy": 1, "skewed": 1}
    streams = {s["stream"]: s for s in snap["streams"]}
    assert streams["jobs:pending:ap"]["region"] == "ap"
    assert streams["jobs:pending"]["consumers"] == [{"name": "w1", "pending": 1}]
    assert snap["dead_letter_depth"] == 3
    text = " ".join(snap["warnings"])
    assert "jobs:pending:ap" in text and "no live worker in region 'ap'" in text
    assert "older than the backend" in text


def test_local_heartbeat_round_trip():
    r = FakeRedis()
    asyncio.run(wf.record_heartbeat(r, {"worker_id": "local:3:laptop", "kind": "local",
                                        "workspace_id": 3}))
    snap = asyncio.run(wf.fleet_snapshot(r))
    assert snap["workers"][0]["worker_id"] == "local:3:laptop"
    assert snap["workers"][0]["workspace_id"] == 3


def test_dead_letter_replay_goes_back_to_the_original_stream():
    from app.api.dead_letter import replay_stream
    web = {"test_case": {"executor": "ui_playwright"}}
    assert replay_stream(json.dumps(web)) == "jobs:pending"
    assert replay_stream(json.dumps({**web, "region": "eu"})) == "jobs:pending:eu"
    mobile = {"test_cases": [{"executor": "mobile_appium"}], "region": "ap"}
    assert replay_stream(json.dumps(mobile)) == "jobs:mobile:pending:ap"
    assert replay_stream(json.dumps({**web, "region": "x:y"})) == "jobs:pending"
    assert replay_stream("not json") == "jobs:pending"


# --- egress policy -------------------------------------------------------------

def test_hosts_are_normalised():
    assert normalize_host("https://App.Example.com:8443/x") == "app.example.com"
    assert normalize_host("*.cdn.example.com") == "cdn.example.com"
    assert normalize_host("10.0.0.1") == "10.0.0.1"
    assert normalize_host("example.com:80") == "example.com"
    with pytest.raises(ValueError):
        normalize_host("not a host!")


def test_egress_not_enforced_unless_opted_in():
    assert resolve_egress_policy(None, instance_enforced=False, instance_hosts=[],
                                 instance_allows_private=True) is None
    assert resolve_egress_policy({"enforce": False, "allowed_hosts": ["a.com"]},
                                 instance_enforced=False, instance_hosts=[],
                                 instance_allows_private=True) is None


def test_egress_policy_merges_instance_hosts_and_base_url():
    p = resolve_egress_policy({"enforce": True, "allowed_hosts": ["a.com"], "allow_private": True},
                              instance_enforced=False, instance_hosts=["sso.corp.com"],
                              instance_allows_private=True,
                              base_url="https://shop.example.com/app")
    assert p == {"enforce": True, "allowed_hosts": ["a.com", "sso.corp.com", "shop.example.com"],
                 "allow_private": True}


def test_instance_is_the_ceiling_for_private_targets_and_can_force_enforcement():
    p = resolve_egress_policy({"allow_private": True}, instance_enforced=True,
                              instance_hosts=[], instance_allows_private=False)
    assert p["enforce"] is True and p["allow_private"] is False


def test_a_corrupt_stored_policy_fails_closed():
    p = resolve_egress_policy({"allowed_hosts": ["not a host!"]}, instance_enforced=False,
                              instance_hosts=[], instance_allows_private=True)
    assert p == {"enforce": True, "allowed_hosts": [], "allow_private": False}


def test_network_policy_model_rejects_bad_hosts():
    with pytest.raises(Exception):
        NetworkPolicy(allowed_hosts=["bad host"])
    assert NetworkPolicy(allowed_hosts=["A.com", "a.com"]).allowed_hosts == ["a.com"]


# --- worker config -------------------------------------------------------------

def test_worker_config_sends_only_what_is_set(monkeypatch):
    from app.services import worker_config
    values = {"WORKER_DEFAULT_TIMEOUT_MS": 45000, "WORKER_RUNTIME_HEAL_ENABLED": False,
              "WORKER_MAX_JOB_DURATION_MS": 0, "WORKER_HAR_CAPTURE_ENABLED": None}
    monkeypatch.setattr(worker_config, "effective", lambda k: values.get(k))
    cfg = worker_config.effective_worker_config()
    assert cfg["env"] == {"DEFAULT_TIMEOUT": "45000", "MAX_JOB_DURATION_MS": None,
                          "RUNTIME_HEAL_ENABLED": "false", "HAR_CAPTURE_ENABLED": None}
    assert cfg["llm"] is None                       # worker keeps its own env


def test_worker_llm_explicit_and_inherited(monkeypatch):
    from app.services import worker_config
    values = {"WORKER_LLM_PROVIDER": "Anthropic", "WORKER_LLM_MODEL": "m",
              "WORKER_LLM_API_KEY": "k"}
    monkeypatch.setattr(worker_config, "effective", lambda k: values.get(k))
    assert worker_config.worker_llm() == {"provider": "anthropic", "model": "m",
                                          "api_key": "k", "base_url": None}
    values["WORKER_LLM_PROVIDER"] = "inherit"
    import app.ai.providers as providers
    monkeypatch.setattr(providers, "_provider_configs", lambda: [
        {"is_active": True, "is_default": True, "provider_type": "ollama",
         "model": "qwen", "api_key": "", "base_url": "http://o:11434/v1"}])
    assert worker_config.worker_llm() == {"provider": "ollama", "model": "qwen",
                                          "api_key": None, "base_url": "http://o:11434/v1"}


# --- monitor transitions + run paging decision ----------------------------------

def test_monitor_transition_matches_the_old_inline_logic():
    from app.tasks.monitor_tasks import transition
    t = lambda **kw: transition(**{"threshold": 2, "alert_on_recovery": True, **kw})  # noqa: E731
    assert t(is_up=True, streak=0, prev_state=None, prev_alert=None) == ("up", None, "up")
    assert t(is_up=False, streak=1, prev_state="up", prev_alert="up") == ("up", None, "up")
    assert t(is_up=False, streak=2, prev_state="up", prev_alert="up") == ("down", "down", "down")
    assert t(is_up=False, streak=3, prev_state="down", prev_alert="down") == ("down", None, "down")
    assert t(is_up=True, streak=0, prev_state="down", prev_alert="down") == ("up", "up", "up")
    assert transition(is_up=True, streak=0, threshold=1, prev_state="down", prev_alert="down",
                      alert_on_recovery=False) == ("up", None, "up")


def test_only_unattended_runs_page():
    from app.models import RunTrigger, TestRun, TestStatus
    from app.tasks.paging_tasks import page_decision

    def run(**kw):
        base = dict(test_suite_id=1, project_id=1, status=TestStatus.FAILED,
                    triggered_by=RunTrigger.SCHEDULE)
        return TestRun(**{**base, **kw})
    assert page_decision(run(), False) == "trigger"
    assert page_decision(run(status=TestStatus.PASSED), False) == "resolve"
    assert page_decision(run(status=TestStatus.RUNNING), False) is None
    assert page_decision(run(triggered_by=RunTrigger.CI), False) == "trigger"
    assert page_decision(run(triggered_by=RunTrigger.HUMAN), False) is None
    assert page_decision(run(triggered_by=RunTrigger.API_AGENT), False) is None
    assert page_decision(run(local_worker_id="laptop"), False) is None
    assert page_decision(run(), True) is None          # monitors page via their own path


# --- server metrics next to load results ------------------------------------------

def test_prometheus_queries_come_from_the_load_step():
    from app.services.server_metrics import MAX_QUERIES, queries_from_steps
    steps = [{"type": "goto"}, {"type": "load-test", "params": {"prometheus_queries": [
        {"name": "CPU", "query": "rate(cpu[1m])"}, "up", {"query": ""}, {"query": "x" * 2000},
    ] + [{"query": f"q{i}"} for i in range(20)]}}]
    qs = queries_from_steps(steps)
    assert qs[0] == {"name": "CPU", "query": "rate(cpu[1m])"}
    assert qs[1] == {"name": "query 2", "query": "up"}
    assert len(qs) == MAX_QUERIES
    assert queries_from_steps([{"type": "goto"}]) == []


def test_window_and_step():
    from app.services.server_metrics import step_seconds, window_for
    fin = datetime(2026, 10, 15, 12, 0, 0)
    start, end = window_for(datetime(2026, 10, 15, 11, 58), fin, 60_000)
    assert end == datetime(2026, 10, 15, 12, 0, 30)
    assert start == datetime(2026, 10, 15, 11, 58, 30)
    assert step_seconds(start, end) == 5


def test_evaluate_parses_matrices_and_isolates_errors(monkeypatch):
    from app.services import server_metrics, instance_settings
    monkeypatch.setattr(instance_settings, "effective",
                        lambda k: {"PROMETHEUS_URL": "http://prometheus:9090",
                                   "PROMETHEUS_ALLOW_ADHOC_QUERIES": True}.get(k))
    calls = []

    async def fake_fetch(base, params, token):
        calls.append(params)
        if params["query"] == "bad(":
            raise ValueError("parse error")
        return {"status": "success", "data": {"resultType": "matrix", "result": [
            {"metric": {"pod": "api-1"}, "values": [[1, "0.5"], [2, "NaN"], [3, "oops"]]}]}}
    monkeypatch.setattr(server_metrics, "_fetch", fake_fetch)
    out = asyncio.run(server_metrics.evaluate(
        [{"name": "cpu", "query": "up"}, {"name": "broken", "query": "bad("}],
        datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 1, 0, 5)))
    assert out["configured"] is True
    cpu, broken = out["series"]
    assert cpu["result"][0]["labels"] == {"pod": "api-1"}
    assert cpu["result"][0]["points"][0] == [1.0, 0.5]
    assert len(cpu["result"][0]["points"]) == 2          # NaN parses; "oops" is dropped
    assert broken["error"] == "parse error"
    assert calls[0]["start"] == 1767225600.0


def test_evaluate_without_prometheus_is_a_no_op(monkeypatch):
    from app.services import server_metrics, instance_settings
    monkeypatch.setattr(instance_settings, "effective", lambda k: None)
    out = asyncio.run(server_metrics.evaluate([{"name": "a", "query": "up"}],
                                              datetime(2026, 1, 1), datetime(2026, 1, 1, 0, 1)))
    assert out["configured"] is False and out["series"] == []
