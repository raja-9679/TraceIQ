"""Regression tests for the WP8 security review findings.

    ./run-tests.sh tests/test_wp8_security_review.py -q

Region grants, Prometheus query templates, paging vendor pinning, net_guard's
embedded-IPv4 handling, and static checks on the restore drill, compose files
and Helm chart (the last three self-skip inside run-tests.sh, which mounts
backend/ only; run them from the host with plain pytest).
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from app.services import worker_fleet as wf

REPO = Path(__file__).resolve().parents[2]


# --- 1. region grants -----------------------------------------------------------

def test_region_grants_parse():
    g = wf.parse_region_grants(["eu", "acme-onprem=12, 15", "", "bad label", "us=x"])
    assert g == {"eu": None, "acme-onprem": {12, 15}, "us": set()}
    # A restriction on any line wins over an open line for the same region,
    # whichever order they come in.
    assert wf.parse_region_grants(["eu", "eu=3"]) == {"eu": {3}}
    assert wf.parse_region_grants(["eu=3", "eu"]) == {"eu": {3}}
    assert wf.parse_region_grants(["eu=3", "eu=4"]) == {"eu": {3, 4}}


def test_region_access():
    entries = ["eu", "acme=7"]
    wf.check_region_access(None, 1, entries)                 # no region = global pool
    wf.check_region_access("eu", 1, entries)
    wf.check_region_access("acme", 7, entries)
    for region, ws in (("acme", 8), ("acme", None), ("ap", 1), ("eu", 1)):
        with pytest.raises(wf.RegionNotPermitted):
            wf.check_region_access(region, ws, entries if region != "eu" else [])


def test_region_access_reads_the_instance_setting(monkeypatch):
    from app.services import instance_settings
    monkeypatch.setattr(instance_settings, "effective",
                        lambda k: ["eu"] if k == "WORKER_REGIONS" else None)
    wf.check_region_access("eu", 1)
    with pytest.raises(wf.RegionNotPermitted):
        wf.check_region_access("us", 1)


# --- Prometheus templates ----------------------------------------------------------

def _prom(monkeypatch, **values):
    from app.services import instance_settings, server_metrics
    base = {"PROMETHEUS_URL": "http://prometheus:9090"}
    base.update(values)
    monkeypatch.setattr(instance_settings, "effective", lambda k: base.get(k))
    sent = []

    async def fake_fetch(url, params, token):
        sent.append(params["query"])
        return {"status": "success", "data": {"resultType": "matrix", "result": []}}
    monkeypatch.setattr(server_metrics, "_fetch", fake_fetch)
    return server_metrics, sent


def test_adhoc_promql_is_refused_by_default(monkeypatch):
    sm, sent = _prom(monkeypatch, PROMETHEUS_QUERY_TEMPLATES=[
        'cpu=sum(rate(cpu{project="$project_id"}[1m]))'])
    out = asyncio.run(sm.evaluate(
        [{"name": "steal", "query": 'up{namespace="other-tenant"}'},
         {"name": "CPU", "query": "cpu"},
         {"name": "CPU2", "query": "", "template": "cpu"}],
        datetime(2026, 1, 1), datetime(2026, 1, 1, 0, 5),
        scope={"project_id": 42, "workspace_id": 3, "run_id": 9}))
    steal, cpu, cpu2 = out["series"]
    assert "free-form PromQL is disabled" in steal["error"]
    assert cpu["error"] is None and cpu2["error"] is None
    assert sent == ['sum(rate(cpu{project="42"}[1m]))'] * 2
    # The template body is not echoed to the reader.
    assert cpu2["query"] == "cpu"


def test_adhoc_promql_needs_the_opt_in(monkeypatch):
    sm, sent = _prom(monkeypatch, PROMETHEUS_ALLOW_ADHOC_QUERIES=True)
    out = asyncio.run(sm.evaluate([{"name": "a", "query": "up"}],
                                  datetime(2026, 1, 1), datetime(2026, 1, 1, 0, 5)))
    assert out["series"][0]["error"] is None and sent == ["up"]


def test_template_scope_is_never_left_unsubstituted():
    from app.services.server_metrics import resolve_query
    t = {"x": 'up{ws="$workspace_id"}'}
    with pytest.raises(ValueError):
        resolve_query({"template": "x", "query": ""}, t, False, {"workspace_id": None})
    with pytest.raises(ValueError):
        resolve_query({"template": "nope", "query": ""}, t, True, {})
    # Scope values are rendered as integers: nothing a caller sends is spliced in.
    with pytest.raises((TypeError, ValueError)):
        resolve_query({"template": "x", "query": ""}, t, False, {"workspace_id": '1"} or up{a="'})


def test_queries_from_steps_keep_template_names():
    from app.services.server_metrics import queries_from_steps
    qs = queries_from_steps([{"type": "load-test", "params": {"prometheus_queries": [
        {"template": "cpu"}, {"name": "p99", "template": "p99"}]}}])
    assert qs == [{"name": "cpu", "query": "", "template": "cpu"},
                  {"name": "p99", "query": "", "template": "p99"}]


# --- paging goes through net_guard and is pinned to the vendors -------------------------

def test_paging_refuses_non_vendor_urls(monkeypatch):
    from app.services import paging
    for url in ("http://events.pagerduty.com/v2/enqueue",
                "https://events.pagerduty.com.evil.io/v2/enqueue",
                "https://169.254.169.254/latest",
                "https://user:pw@api.opsgenie.com/v2/alerts",
                "https://api.opsgenie.com:8443/v2/alerts"):
        with pytest.raises(ValueError):
            paging.guard_vendor_url(url)


def test_paging_runs_net_guard(monkeypatch):
    from app.core import net_guard
    from app.services import paging
    seen = []

    async def fake_validate(url, **kw):
        seen.append((url, kw))
        raise net_guard.UnsafeUrlError("resolves to 10.0.0.1")
    monkeypatch.setattr(net_guard, "validate_outbound_url", fake_validate)
    with pytest.raises(net_guard.UnsafeUrlError):
        paging._post(paging.PAGERDUTY_EVENTS_URL, json={})
    assert seen == [(paging.PAGERDUTY_EVENTS_URL, {"allow_private": False})]


def test_validate_outbound_url_sync_works_inside_a_loop(monkeypatch):
    from app.core import net_guard

    async def ok(url, **kw):
        return url
    monkeypatch.setattr(net_guard, "validate_outbound_url", ok)
    assert net_guard.validate_outbound_url_sync("https://x") == "https://x"

    async def inside():
        return net_guard.validate_outbound_url_sync("https://y")
    assert asyncio.run(inside()) == "https://y"


# --- net_guard: IPv6 forms that reach an IPv4 address ------------------------------------

@pytest.mark.parametrize("addr", ["64:ff9b::a9fe:a9fe", "2002:a9fe:a9fe::1",
                                  "::ffff:169.254.169.254"])
def test_net_guard_never_allows_embedded_metadata(addr):
    from app.core.net_guard import _is_never_allowed
    assert _is_never_allowed(ipaddress.ip_address(addr))


@pytest.mark.parametrize("addr", ["64:ff9b::a00:1", "2002:0a00:0001::1"])
def test_net_guard_treats_embedded_private_as_private(addr):
    from app.core.net_guard import _is_private
    assert _is_private(ipaddress.ip_address(addr))


def test_net_guard_public_v6_unchanged():
    from app.core.net_guard import _is_never_allowed, _is_private
    ip = ipaddress.ip_address("2606:4700::1111")
    assert not _is_never_allowed(ip) and not _is_private(ip)


# --- deployment files (host-only) -----------------------------------------------------

needs_repo = pytest.mark.skipif(not (REPO / "scripts/restore_drill.sh").exists(),
                                reason="repo root not visible (run-tests.sh mounts backend/ only)")


@needs_repo
def test_restore_drill_keeps_the_password_off_command_lines():
    text = (REPO / "scripts/restore_drill.sh").read_text()
    assert "umask 077" in text
    assert not re.search(r"^\s*set\s+-[a-z]*x", text, re.M)
    # `-e DATABASE_URL=...` would put the password in argv (visible in `ps`).
    assert not re.search(r"-e\s+DATABASE_URL=", text)
    assert re.search(r"-e DATABASE_URL\s*\\", text)
    assert "PGPASSWORD=" not in text
    assert not re.search(r"echo[^\n]*PGPASS_", text)


@needs_repo
def test_restore_drill_redact_is_literal():
    text = (REPO / "scripts/restore_drill.sh").read_text()
    fn = text[text.index("redact() {"):]
    fn = fn[:fn.index("\n}\n") + 3]
    pw = "p.ss*w[0]rd/$x"
    script = f"PGPASS_='{pw}'\n{fn}\nprintf 'url postgres://u:{pw}@h/db\\nclean\\n' | redact\n"
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout
    assert pw not in out and "postgres://u:***@h/db" in out and "clean" in out


@needs_repo
def test_restore_drill_rejects_sql_in_db_name():
    out = subprocess.run(["bash", str(REPO / "scripts/restore_drill.sh"), "--dump", "/dev/null"],
                         env={"DRILL_DB": "x; DROP DATABASE traceiq", "PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True)
    assert out.returncode == 2 and "lowercase identifier" in out.stderr


COMPOSE_FILES = ["docker-compose.yml", "docker-compose.community.yml", "docker-compose.prod.yml",
                 "docker-compose.prod-1.yml", "docker-compose.distributed.yml"]


@needs_repo
@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_region_worker_is_exclusive_and_no_more_privileged(name):
    import yaml
    doc = yaml.safe_load((REPO / "infrastructure" / name).read_text())
    svc = doc["services"]["execution-worker-region"]
    assert svc["extends"]["service"] == "execution-worker"
    assert svc["environment"]["WORKER_REGION_EXCLUSIVE"] == "${WORKER_REGION_EXCLUSIVE:-true}"
    # Nothing that would widen it beyond the base worker.
    for key in ("networks", "ports", "volumes", "privileged", "cap_add", "network_mode",
                "user", "security_opt"):
        assert key not in svc, key


@needs_repo
def test_helm_region_workers_default_to_exclusive():
    import yaml
    chart = REPO / "deploy/helm/traceiq"
    values = yaml.safe_load((chart / "values.yaml").read_text())
    assert values["regionWorkers"] == []
    tpl = (chart / "templates/execution-worker.yaml").read_text()
    assert '(ternary .exclusive true (hasKey . "exclusive"))' in tpl
    assert "default false .exclusive" not in tpl
    assert "regexMatch" in tpl
