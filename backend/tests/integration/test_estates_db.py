"""Estate scanning + monitored pages (WP9) against a real Postgres.

    TRACEIQ_SCRATCH_DB=traceiq_wp9 ./run-tests-live.sh tests/integration/test_estates_db.py -q
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlmodel import Session, create_engine, select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import estates as api                              # noqa: E402
from app.core.auth import AuthPrincipal                         # noqa: E402
from app.core.config import db_url_for, settings                # noqa: E402
from app.models import (                                        # noqa: E402
    AuditLog, EstateDomain, EstateDomainResult, EstateScan, MonitoredPage, PageChangeEvent,
    PageScript, Project, SecurityFinding, User, UserProjectAccess,
)
from app.services import estate_checks as ec                    # noqa: E402
from app.services.script_inventory import sri_sha384            # noqa: E402
from app.tasks import estate_tasks as tasks                     # noqa: E402

from tests.integration.test_targeted_runs_db import (           # noqa: E402,F401
    no_queue, scene, session,
)

REQ = SimpleNamespace(headers={}, client=None)
NOW = datetime(2026, 10, 15, 12, 0)


@pytest.fixture(autouse=True)
def no_estate_queue(monkeypatch):
    monkeypatch.setattr(tasks.run_estate_scan, "delay", lambda *a, **kw: None)
    monkeypatch.setattr(tasks.check_monitored_page, "delay", lambda *a, **kw: None)
    from app.services import estate_findings
    sent = []
    monkeypatch.setattr(estate_findings, "send_alert",
                        lambda session, project, title, lines, emails=None: sent.append((title, lines)))
    return sent


def _me(scene):
    return AuthPrincipal(user=scene.user, api_key=None)


def _sync():
    return create_engine(db_url_for(settings.DATABASE_URL, sync=True))


async def _enable_security(session, scene, domains=("example.com",)):
    project = await session.get(Project, scene.project_id)
    project.security_settings = {"enabled": True, "allowed_domains": list(domains)}
    session.add(project)
    await session.commit()


async def _viewer(session, scene):
    v = User(email="viewer@corp.example.com", full_name="V", hashed_password="x")
    session.add(v)
    await session.flush()
    session.add(UserProjectAccess(user_id=v.id, project_id=scene.project_id, access_level="viewer"))
    await session.commit()
    return AuthPrincipal(user=v, api_key=None)


async def _estate(session, scene, **kw):
    return await api.create_estate(scene.project_id, api.EstateIn(name="Corp", **kw), REQ,
                                   session=session, principal=_me(scene))


# --- validation ----------------------------------------------------------------------

def test_domain_import_parsing():
    names, rejected = api.parse_domain_import(
        "domain,owner\nexample.com,ops\nhttps://Shop.Example.com/cart\n*.api.example.com; www.example.com\n"
        "not a host\n# comment\nexample.com\n")
    assert names == ["example.com", "shop.example.com", "api.example.com", "www.example.com"]
    assert [r["value"] for r in rejected] == ["not a host"]


def test_input_validation():
    with pytest.raises(ValidationError):
        api.EstateIn(name="x", cron_expression="* * * * *")          # more often than hourly
    with pytest.raises(ValidationError):
        api.EstateIn(name="x", dkim_selectors=["bad selector!"])
    with pytest.raises(ValidationError):
        api.MonitoredPageIn(name="p", url="ftp://example.com/")
    with pytest.raises(ValidationError):
        api.ApproveIn(justification="ok")
    assert api.MonitoredPageIn(name="p", url="https://example.com/pay").cron_expression == "0 6 * * 1"


# --- access control ------------------------------------------------------------------

async def test_import_needs_allowlist_and_rejects_off_list_hosts(session, scene):
    e = await _estate(session, scene)
    with pytest.raises(HTTPException) as exc:
        await api.import_domains(e["id"], api.DomainImport(text="example.com"), REQ,
                                 session=session, principal=_me(scene))
    assert exc.value.status_code == 403
    await _enable_security(session, scene)
    out = await api.import_domains(e["id"], api.DomainImport(text="example.com\nwww.example.com\nevil.net"),
                                   REQ, session=session, principal=_me(scene))
    assert out["added"] == ["example.com", "www.example.com"]
    assert out["rejected"] == [{"value": "evil.net", "reason": "not on the project's security allowlist"}]
    again = await api.import_domains(e["id"], api.DomainImport(text="example.com"), REQ,
                                     session=session, principal=_me(scene))
    assert again["added"] == []


async def test_viewer_reads_but_cannot_change(session, scene):
    await _enable_security(session, scene)
    e = await _estate(session, scene)
    page = await api.create_page(scene.project_id, api.MonitoredPageIn(name="pay", url="https://example.com/pay"),
                                 REQ, session=session, principal=_me(scene))
    viewer = await _viewer(session, scene)
    assert [x["id"] for x in await api.list_estates(scene.project_id, session=session, principal=viewer)] == [e["id"]]
    await api.get_estate(e["id"], session=session, principal=viewer)
    await api.list_scripts(page["id"], session=session, principal=viewer)
    for call in (
        api.create_estate(scene.project_id, api.EstateIn(name="x"), REQ, session=session, principal=viewer),
        api.import_domains(e["id"], api.DomainImport(text="example.com"), REQ, session=session, principal=viewer),
        api.scan_now(e["id"], REQ, session=session, principal=viewer),
        api.delete_estate(e["id"], REQ, session=session, principal=viewer),
        api.check_now(page["id"], REQ, session=session, principal=viewer),
        api.reset_baseline(page["id"], REQ, session=session, principal=viewer),
        api.delete_page(page["id"], REQ, session=session, principal=viewer),
    ):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 403


async def test_outsider_and_wrong_scope_key_are_refused(session, scene):
    await _enable_security(session, scene)
    e = await _estate(session, scene)
    page = await api.create_page(scene.project_id, api.MonitoredPageIn(name="pay", url="https://example.com/pay"),
                                 REQ, session=session, principal=_me(scene))
    outsider = User(email="x@else.example.com", full_name="X", hashed_password="x")
    session.add(outsider)
    await session.commit()
    them = AuthPrincipal(user=outsider, api_key=None)
    wrong_key = AuthPrincipal(user=scene.user, api_key=SimpleNamespace(id=1, project_id=scene.other_project_id))
    for who in (them, wrong_key):
        for call in (api.get_estate(e["id"], session=session, principal=who),
                     api.get_page(page["id"], session=session, principal=who),
                     api.list_changes(page["id"], limit=100, session=session, principal=who)):
            with pytest.raises(HTTPException) as exc:
                await call
            assert exc.value.status_code == 403
    ws = (await session.get(Project, scene.project_id)).workspace_id
    with pytest.raises(HTTPException) as exc:
        await api.certificate_calendar(ws, session=session, principal=them)
    assert exc.value.status_code == 403


async def test_page_host_must_be_allowlisted(session, scene):
    await _enable_security(session, scene)
    with pytest.raises(HTTPException) as exc:
        await api.create_page(scene.project_id, api.MonitoredPageIn(name="p", url="https://evil.net/"),
                              REQ, session=session, principal=_me(scene))
    assert exc.value.status_code == 403


# --- scan storage --------------------------------------------------------------------

class FakeDns:
    def __init__(self, records):
        self.records = records

    def query(self, name, rdtype):
        return list(self.records.get((name, rdtype), []))


def fake_net(spf="v=spf1 -all", cert_days=20):
    dns = FakeDns({
        ("example.com", "SOA"): ["ns. host. 1 2 3 4 5"],
        ("example.com", "TXT"): [spf] if spf else [],
        ("_dmarc.example.com", "TXT"): ["v=DMARC1; p=reject; rua=mailto:r@example.com"],
        ("example.com", "CAA"): ['0 issue "pki.goog"'],
        ("example.com", "DNSKEY"): ["257 3 13 abc"],
    })

    def fetch(url):
        if url.startswith("https://"):
            return 200, {"strict-transport-security": "max-age=31536000"}, ""
        return 301, {"location": url.replace("http://", "https://")}, ""

    def cert(host):
        exp = datetime(2026, 10, 15, 12, tzinfo=timezone.utc) + timedelta(days=cert_days, hours=1)
        return {"not_after": exp.isoformat(), "issuer": "CN=R3"}

    ct = [{"name_value": "example.com\nshop.example.com\n*.cdn.example.com"},
          {"name_value": "elsewhere.org"}]
    return ec.Net(dns=dns, fetch=fetch, cert=cert, ct=lambda apex: ct)


async def _scan(session, scene, estate_id, net, now=NOW):
    out = await api.scan_now(estate_id, REQ, session=session, principal=_me(scene))
    engine = _sync()
    with Session(engine) as s:
        result = tasks.run_scan(s, out["id"], net, now=now)
    engine.dispose()
    return out["id"], result


async def test_scan_stores_history_discovers_and_scores(session, scene, no_estate_queue):
    await _enable_security(session, scene)
    e = await _estate(session, scene)
    await api.import_domains(e["id"], api.DomainImport(text="example.com"), REQ,
                             session=session, principal=_me(scene))
    scan_id, result = await _scan(session, scene, e["id"], fake_net(spf=None))
    assert result["status"] == "completed"
    assert sorted(result["discovered"]) == ["cdn.example.com", "shop.example.com"]

    engine = _sync()
    with Session(engine) as s:
        scan = s.get(EstateScan, scan_id)
        assert scan.status == "completed" and scan.summary["domains_scanned"] == 3
        apex = s.exec(select(EstateDomain).where(EstateDomain.hostname == "example.com")).one()
        assert apex.is_apex is True and apex.cert_alert_sent_days == 30
        res = s.exec(select(EstateDomainResult).where(EstateDomainResult.domain_id == apex.id)).one()
        # 100 - spf_missing(10) - cert_expiring_30(2)
        assert res.score == 88 == apex.score
        assert sorted(d["check"] for d in res.deductions) == ["certificate", "spf"]
        sources = {d.hostname: d.source for d in s.exec(select(EstateDomain)).all()}
        assert sources["shop.example.com"] == "ct"
        f = s.exec(select(SecurityFinding).where(SecurityFinding.title == "SPF record missing")).one()
        assert f.project_id == scene.project_id and f.status == "open"
    engine.dispose()
    assert no_estate_queue and "Certificate expiry" in no_estate_queue[0][0]

    # Fixed SPF + still inside 30 days: the finding verifies, no second alert.
    no_estate_queue.clear()
    await _scan(session, scene, e["id"], fake_net(), now=NOW + timedelta(days=1))
    engine = _sync()
    with Session(engine) as s:
        f = s.exec(select(SecurityFinding).where(SecurityFinding.title == "SPF record missing")).one()
        assert f.status == "verified_fixed"
    engine.dispose()
    assert no_estate_queue == []

    detail = await api.domain_detail(apex.id, session=session, principal=_me(scene))
    assert detail["latest"]["arithmetic"] == {"start": 100, "deducted": 2, "score": 98}
    assert [h["score"] for h in detail["history"]] == [98, 88]
    est = await api.get_estate(e["id"], session=session, principal=_me(scene))
    scores = [d["score"] for d in est["domains"]]
    assert scores == sorted(scores)                      # worst first
    apex_out = next(d for d in est["domains"] if d["hostname"] == "example.com")
    assert apex_out["trend"] == [88, 98]

    ws = (await session.get(Project, scene.project_id)).workspace_id
    cal = await api.certificate_calendar(ws, session=session, principal=_me(scene))
    assert {c["hostname"] for c in cal} >= {"example.com"}


async def test_scan_skips_hosts_dropped_from_the_allowlist(session, scene):
    await _enable_security(session, scene, domains=("example.com", "other.org"))
    e = await _estate(session, scene, discover_subdomains=False)
    await api.import_domains(e["id"], api.DomainImport(text="example.com\nother.org"), REQ,
                             session=session, principal=_me(scene))
    await _enable_security(session, scene, domains=("example.com",))
    _, result = await _scan(session, scene, e["id"], fake_net())
    assert result["skipped_not_allowlisted"] == ["other.org"]
    assert result["domains_scanned"] == 1


async def test_concurrent_scan_is_refused(session, scene):
    await _enable_security(session, scene)
    e = await _estate(session, scene)
    await api.scan_now(e["id"], REQ, session=session, principal=_me(scene))
    with pytest.raises(HTTPException) as exc:
        await api.scan_now(e["id"], REQ, session=session, principal=_me(scene))
    assert exc.value.status_code == 409


async def test_due_estates_schedule(session, scene):
    await _enable_security(session, scene)
    e = await _estate(session, scene)
    engine = _sync()
    with Session(engine) as s:
        from app.models import Estate
        est = s.get(Estate, e["id"])
        est.next_scan_at = NOW - timedelta(minutes=1)
        s.add(est)
        s.commit()
        assert tasks.due_estates(s, NOW) == [e["id"]]
        assert tasks.due_estates(s, NOW) == []          # next_scan_at advanced
        assert s.get(Estate, e["id"]).next_scan_at > NOW
    engine.dispose()


# --- monitored pages ------------------------------------------------------------------

class FakeRedis:
    def __init__(self):
        self.kv = {}

    def get(self, k):
        return self.kv.get(k)

    def delete(self, k):
        self.kv.pop(k, None)


def obs(scripts, csp="default-src 'self'", text="Checkout. Pay securely with card or UPI here today.",
        elements=None):
    return {"url": "https://example.com/pay", "final_url": "https://example.com/pay", "status": 200,
            "headers": {"content-security-policy": csp, "set-cookie": "sid=leak"},
            "scripts": scripts, "external_hashes": {s["src"]: s.pop("hash") for s in scripts if s.get("hash")},
            "text": text, "elements": elements or {"h1": "Checkout"}}


async def _collect(page_id, observation, now):
    rc = FakeRedis()
    rc.kv[f"page_check:result:{page_id}"] = json.dumps(observation)
    engine = _sync()
    with Session(engine) as s:
        assert tasks.collect_page_once(s, rc, page_id, now - timedelta(seconds=10), now=now)
    engine.dispose()


async def test_page_inventory_approval_and_change_detection(session, scene, no_estate_queue):
    await _enable_security(session, scene)
    body = api.MonitoredPageIn(name="pay", url="https://example.com/pay", track_content=True,
                               forbidden_keywords=["hacked by"],
                               element_checks=[api.ElementCheck(selector="h1", must_contain="Checkout")])
    page = await api.create_page(scene.project_id, body, REQ, session=session, principal=_me(scene))
    pid = page["id"]

    await _collect(pid, obs([{"src": "https://js.stripe.com/v3/", "hash": "sha384-s1"},
                             {"inline_sha384": sri_sha384(b"cfg")}]), NOW)
    scripts = await api.list_scripts(pid, session=session, principal=_me(scene))
    assert len(scripts) == 2 and not any(s["authorized"] for s in scripts)
    stripe = next(s for s in scripts if s["src"])
    assert stripe["third_party"] is True and stripe["sha384"] == "sha384-s1"
    p = await api.get_page(pid, session=session, principal=_me(scene))
    assert p["last_status"] == "ok" and p["has_content_baseline"]
    assert "set-cookie" not in p["headers_baseline"]
    no_estate_queue.clear()

    with pytest.raises(ValidationError):
        api.ApproveIn(justification="")
    out = await api.approve_script(pid, stripe["id"], api.ApproveIn(justification="Card tokenisation (Stripe Elements)"),
                                   REQ, session=session, principal=_me(scene))
    assert out["authorized"] and out["authorized_by_id"] == scene.user.id
    audit = (await session.exec(select(AuditLog).where(
        AuditLog.entity_type == "page_script", AuditLog.action == "approve"))).one()
    assert audit.changes["justification"].startswith("Card tokenisation")
    assert audit.changes["sha384"] == "sha384-s1"

    # Stripe's body changes, a skimmer appears, CSP is loosened, page defaced.
    later = NOW + timedelta(days=7)
    await _collect(pid, obs([{"src": "https://js.stripe.com/v3/", "hash": "sha384-s2"},
                             {"inline_sha384": sri_sha384(b"cfg")},
                             {"src": "https://cdn.skim.example.net/x.js", "hash": "sha384-k"}],
                            csp="default-src *", text="HACKED BY crew",
                            elements={"h1": "Owned"}), later)
    p = await api.get_page(pid, session=session, principal=_me(scene))
    assert p["last_status"] == "alert" and p["last_similarity"] == 0.0
    scripts = {s["src"]: s for s in await api.list_scripts(pid, session=session, principal=_me(scene))}
    assert scripts["https://js.stripe.com/v3/"]["authorized"] is False     # approval revoked by the change
    kinds = [c["kind"] for c in await api.list_changes(pid, limit=100, session=session, principal=_me(scene))]
    for k in ("script_changed", "script_added", "headers_changed", "defacement", "script_approved", "baseline"):
        assert k in kinds
    assert no_estate_queue and any("skim" in line for line in no_estate_queue[0][1])
    findings = (await session.exec(select(SecurityFinding).where(
        SecurityFinding.project_id == scene.project_id))).all()
    assert {f.scan_type for f in findings} == {"page-scripts", "page-defacement"}

    # Reset: next check re-baselines, and a clean check resolves the defacement finding.
    await api.reset_baseline(pid, REQ, session=session, principal=_me(scene))
    await _collect(pid, obs([], text="Checkout. Pay securely."), later + timedelta(days=7))
    p = await api.get_page(pid, session=session, principal=_me(scene))
    assert p["last_status"] == "changed"     # scripts removed
    engine = _sync()
    with Session(engine) as s:
        deface = s.exec(select(SecurityFinding).where(SecurityFinding.scan_type == "page-defacement")).one()
        assert deface.status == "verified_fixed"
    engine.dispose()
    gone = await api.list_scripts(pid, include_removed=True, session=session, principal=_me(scene))
    assert all(not s["present"] for s in gone)


async def test_missing_worker_result_times_out_to_an_error(session, scene):
    await _enable_security(session, scene)
    page = await api.create_page(scene.project_id, api.MonitoredPageIn(name="p", url="https://example.com/"),
                                 REQ, session=session, principal=_me(scene))
    engine = _sync()
    with Session(engine) as s:
        assert tasks.collect_page_once(s, FakeRedis(), page["id"], NOW, now=NOW + timedelta(minutes=1)) is False
        assert tasks.collect_page_once(s, FakeRedis(), page["id"], NOW, now=NOW + timedelta(hours=1)) is True
        assert s.get(MonitoredPage, page["id"]).last_status == "error"
    engine.dispose()


async def test_deletes_cascade(session, scene):
    await _enable_security(session, scene)
    e = await _estate(session, scene)
    await api.import_domains(e["id"], api.DomainImport(text="example.com"), REQ,
                             session=session, principal=_me(scene))
    await _scan(session, scene, e["id"], fake_net())
    page = await api.create_page(scene.project_id, api.MonitoredPageIn(name="p", url="https://example.com/"),
                                 REQ, session=session, principal=_me(scene))
    await _collect(page["id"], obs([{"src": "https://example.com/a.js"}]), NOW)
    await api.delete_estate(e["id"], REQ, session=session, principal=_me(scene))
    await api.delete_page(page["id"], REQ, session=session, principal=_me(scene))
    for model in (EstateDomain, EstateScan, EstateDomainResult, PageScript, PageChangeEvent):
        assert (await session.exec(select(model))).all() == []
