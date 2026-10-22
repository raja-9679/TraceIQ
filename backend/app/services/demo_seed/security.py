"""Demo datasets: security pages (see package docstring).

Security register (two ZAP scans whose findings went through the WP7 workflow),
access matrix + a finished run, access reviews, an estate with two scans, a
monitored page with a script inventory, SOC 2 controls with evidence and an
exception, and a disabled trust center with a status page and a draft
incident post.

Everything is written through the same functions the scanners and workers use
(`finding_lifecycle.ingest/verify_fixes`, `zap_client.map_alerts`,
`estate_checks.scan_host` over a canned `Net`, `estate_findings.apply_findings`,
`estate_tasks.apply_page_observation`, `access_matrix.judge_observation /
apply_findings`, `access_review.build_report`) so the rows are shaped exactly as
production writes them. Nothing touches the network: the estate scan's DNS,
certificate and HTTPS answers are canned, and every schedule is seeded off.

Skipped on purpose: TrustDocument and EvidencePack rows — both are only useful
with a stored object (the document file / the pack zip) and the demo writes no
objects, so a seeded row would be a download that 404s.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List

WEB_ORIGIN = "https://example.com"
TODOLITE = "http://localhost:8080"
ALLOWED_DOMAINS = ["example.com", "localhost"]


def _members(ctx) -> List[Any]:
    """Fake members made by the setup module, if it ran (User rows)."""
    return [m for m in ctx.members.values() if getattr(m, "id", None) and getattr(m, "email", None)]


async def _audit(ctx, **kw: Any) -> None:
    from app.services.audit import record
    await record(ctx.session, workspace_id=ctx.ws.id, user_id=ctx.user.id, **kw)


def _sha(text: str) -> str:
    return hashlib.sha384(text.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Security settings
# ---------------------------------------------------------------------------

def _enable_security(ctx) -> None:
    web = ctx.projects["web"]
    web.security_settings = {"enabled": True, "allowed_domains": list(ALLOWED_DOMAINS),
                             "allow_active_scan": False}
    ctx.session.add(web)


# ---------------------------------------------------------------------------
# ZAP scans + findings workflow
# ---------------------------------------------------------------------------

_ZAP_ALERTS = {
    "csp": {"alert": "Content Security Policy (CSP) Header Not Set", "risk": "Medium",
            "confidence": "High", "cweid": "693", "wascid": "15", "url": f"{WEB_ORIGIN}/",
            "description": "Content Security Policy is an added layer of security that helps detect and "
                           "mitigate certain types of attacks, including XSS and data injection.",
            "solution": "Configure the web server to return the Content-Security-Policy header."},
    "xfo": {"alert": "Missing Anti-clickjacking Header", "risk": "Medium", "confidence": "Medium",
            "cweid": "1021", "wascid": "15", "url": f"{WEB_ORIGIN}/login", "param": "x-frame-options",
            "description": "The response does not protect against 'ClickJacking' attacks.",
            "solution": "Send Content-Security-Policy frame-ancestors or X-Frame-Options."},
    "cookie": {"alert": "Cookie without SameSite Attribute", "risk": "Low", "confidence": "Medium",
               "cweid": "1275", "wascid": "13", "url": f"{WEB_ORIGIN}/login", "param": "prefs",
               "evidence": "Set-Cookie: prefs",
               "description": "A cookie has been set without the SameSite attribute.",
               "solution": "Set SameSite to 'Lax' or 'Strict' for all cookies."},
    "server": {"alert": "Server Leaks Version Information via \"Server\" HTTP Response Header Field",
               "risk": "Low", "confidence": "High", "cweid": "497", "wascid": "13",
               "url": f"{WEB_ORIGIN}/", "evidence": "ECS (dcb/7F84)",
               "description": "The web/application server is leaking version information via the "
                              "\"Server\" HTTP response header.",
               "solution": "Suppress the Server header or remove version details from it."},
    "xss": {"alert": "Cross Site Scripting (Reflected)", "risk": "High", "confidence": "Medium",
            "cweid": "79", "wascid": "8", "url": f"{WEB_ORIGIN}/search?q=demo", "param": "q",
            "attack": "\"><scrIpt>alert(1);</scRipt>",
            "description": "Cross-site scripting: the q parameter is reflected without encoding.",
            "solution": "Encode output for the HTML context it is written into."},
    "hsts": {"alert": "Strict-Transport-Security Header Not Set", "risk": "Low", "confidence": "High",
             "cweid": "319", "wascid": "15", "url": f"{WEB_ORIGIN}/",
             "description": "HTTP Strict Transport Security (HSTS) is not enforced.",
             "solution": "Send Strict-Transport-Security with a max-age of at least six months."},
}


async def _zap_scans(ctx) -> None:
    from app.models import SecurityFinding, SecurityScan
    from app.services import finding_lifecycle as lifecycle
    from app.services.passive_security import finding_fingerprint
    from app.services.zap_client import map_alerts

    web = ctx.projects["web"]
    session = ctx.session

    async def run_scan(started: datetime, keys: List[str]) -> tuple:
        finished = started + timedelta(minutes=14)
        scan = SecurityScan(project_id=web.id, target_url=f"{WEB_ORIGIN}/", scan_type="baseline",
                            status="completed", requested_by_id=ctx.user.id, created_at=started,
                            started_at=started + timedelta(seconds=20), finished_at=finished)
        session.add(scan)
        await session.flush()
        counts = {"high": 0, "medium": 0, "low": 0, "info": 0}
        rows = []
        for f in map_alerts([_ZAP_ALERTS[k] for k in keys]):
            counts[f["severity"]] = counts.get(f["severity"], 0) + 1
            rows.append(SecurityFinding(
                scan_id=scan.id, project_id=web.id, scan_type="zap", category=f["category"],
                severity=f["severity"], title=f["title"], description=f["description"],
                evidence=f["evidence"], target_url=f["target_url"], created_at=finished,
                fingerprint=finding_fingerprint(f["category"], f["title"], f["target_url"]),
                status="open"))
        for r in rows:
            session.add(r)

        def fold(s):
            lifecycle.ingest(s, rows, project_id=web.id, scan_type="zap", now=finished)
            lifecycle.verify_fixes(
                s, project_id=web.id, scan_type="zap", produced=[r.fingerprint for r in rows],
                in_scope=lambda finding: lifecycle.origin_of(finding.target_url)
                == lifecycle.origin_of(scan.target_url),
                evidence={"verified_by_scan_id": scan.id}, now=finished)
        await session.run_sync(fold)
        scan.counts = counts
        session.add(scan)
        await session.flush()
        return scan, {r.title: r for r in rows}

    first, found = await run_scan(ctx.ago(days=16, hours=3), ["xss", "csp", "xfo", "cookie", "server"])
    # People work the first scan's findings the way the Security page lets them.
    triaged_at = ctx.ago(days=14)
    xss = found[_ZAP_ALERTS["xss"]["alert"]]
    csp = found[_ZAP_ALERTS["csp"]["alert"]]
    cookie = found[_ZAP_ALERTS["cookie"]["alert"]]
    server = found[_ZAP_ALERTS["server"]["alert"]]
    xss.assignee_id = ctx.user.id
    xss.status, xss.resolved_at = "fixed_pending_verification", triaged_at
    csp.assignee_id, csp.status = ctx.user.id, "in_progress"
    cookie.status = "accepted_risk"
    cookie.risk_justification = ("The prefs cookie only stores the UI theme; it carries no session "
                                 "or personal data. Revisit when the cookie banner is rebuilt.")
    cookie.risk_expires_at = ctx.now + timedelta(days=75)
    cookie.risk_approved_by_id, cookie.risk_approved_at = ctx.user.id, triaged_at
    server.status = "false_positive"
    for f, target in ((xss, "fixed_pending_verification"), (csp, "in_progress"),
                      (cookie, "accepted_risk"), (server, "false_positive")):
        ctx.session.add(f)
        await _audit(ctx, entity_type="security_finding", entity_id=f.id, action="status_change",
                     changes={"status": {"from": "open", "to": target}, "fingerprint": f.fingerprint})

    # The retest of the XSS fix: a later scan of the same target that no longer
    # reports it verifies the fix; the other verdicts carry forward and HSTS is new.
    second, _ = await run_scan(ctx.ago(days=3, hours=5), ["csp", "xfo", "cookie", "server", "hsts"])
    xss.retest_scan_id = second.id
    ctx.session.add(xss)
    ctx.extra["zap_scans"] = [first.id, second.id]


# ---------------------------------------------------------------------------
# Access matrix
# ---------------------------------------------------------------------------

async def _access_matrix(ctx) -> None:
    from sqlmodel import select

    from app.models import AccessMatrix, AccessMatrixCell, AccessMatrixRun, Persona, SecurityFinding
    from app.services import access_matrix as am

    web = ctx.projects["web"]
    personas = (await ctx.session.exec(select(Persona).where(Persona.project_id == web.id)
                                       .order_by(Persona.id))).all()
    if len(personas) < 2:
        return
    alice, bob = personas[0], personas[1]
    a, b, anon = str(alice.id), str(bob.id), am.ANONYMOUS
    rows = [
        {"key": "todos_api", "label": "List my todos (API)", "kind": "request", "method": "GET",
         "url": "/api/todos",
         "cells": {a: {"expect": "allow", "status": 200}, b: {"expect": "allow", "status": 200},
                   anon: {"expect": "deny"}}},
        {"key": "admin_page", "label": "Admin panel", "kind": "page", "url": "/admin",
         "selector": "#admin-panel",
         "cells": {a: {"expect": "allow"}, b: {"expect": "deny"}, anon: {"expect": "deny"}}},
        {"key": "other_todo", "label": "Read alice's todo by id", "kind": "request", "method": "GET",
         "url": "/api/todos/{id}",
         "swap": {"owner_persona_id": alice.id, "method": "GET", "url": "/api/todos",
                  "extractor": {"source": "json", "expr": "0.id"}},
         "cells": {a: {"expect": "allow", "status": 200}, b: {"expect": "deny"},
                   anon: {"expect": "deny"}}},
    ]
    cols = am.column_keys([alice.id, bob.id], True)
    parsed = am.parse_rows(rows, cols)
    created = ctx.ago(days=9)
    matrix = AccessMatrix(project_id=web.id, name="Todo app roles (demo)",
                          description="Who may see todos and the admin panel. bob is a plain user; "
                                      "the admin panel should be alice-only.",
                          base_url=TODOLITE, persona_ids=[alice.id, bob.id], include_anonymous=True,
                          deny_statuses=None, rows=rows, created_by_id=ctx.user.id,
                          created_at=created, updated_at=created)
    ctx.session.add(matrix)
    await ctx.session.flush()

    started = ctx.ago(days=2, hours=6)
    finished = started + timedelta(minutes=3)
    mrun = AccessMatrixRun(
        matrix_id=matrix.id, project_id=web.id, status="completed", matrix_group=uuid.uuid4().hex,
        authorized=False, created_by_id=ctx.user.id, created_at=started, finished_at=finished,
        snapshot={"name": matrix.name, "base_url": matrix.base_url, "columns": cols,
                  "persona_names": {a: alice.name, b: bob.name},
                  "deny_statuses": am.deny_set(None),
                  "rows": [r.model_dump(exclude_none=True) for r in parsed]})
    ctx.session.add(mrun)
    await ctx.session.flush()

    def url(path: str) -> str:
        return am.resolve_url(path, TODOLITE)

    jh = {"content-type": "application/json; charset=utf-8"}

    def resp(path: str, status: int, body: str, **extra: Any) -> Dict[str, Any]:
        return {"url": url(path), **extra, "status": status, "headers": jh,
                "content_type": jh["content-type"], "body_excerpt": body}

    swap = {"owner_status": 200, "swapped_id": "17"}
    observed = {
        ("todos_api", a): resp("/api/todos", 200, '[{"id":17,"title":"Buy milk","done":false}]'),
        ("todos_api", b): resp("/api/todos", 200, "[]"),
        ("todos_api", anon): resp("/api/todos", 401, '{"detail":"Not authenticated"}'),
        ("admin_page", a): {"url": url("/admin"), "status": 200, "final_url": url("/admin"),
                            "element_present": True},
        # The finding: bob is a plain user but the admin panel renders for him.
        ("admin_page", b): {"url": url("/admin"), "status": 200, "final_url": url("/admin"),
                            "element_present": True},
        # Anonymous timed out: not proof of denial, so inconclusive.
        ("admin_page", anon): {"url": url("/admin"), "error_kind": "timeout",
                               "error": "page.goto: Timeout 15000ms exceeded."},
        ("other_todo", a): resp("/api/todos/17", 200, '{"id":17,"title":"Buy milk","done":false}', **swap),
        ("other_todo", b): resp("/api/todos/17", 404, '{"detail":"Not found"}', **swap),
        ("other_todo", anon): resp("/api/todos/17", 401, '{"detail":"Not authenticated"}', **swap),
    }
    deny = am.deny_set(None)
    cells = []
    for row, col, spec in am.plan_cells(parsed, cols):
        obs = observed[(row.key, col)]
        verdict, reason = am.judge_observation(row.kind, spec.expect, obs, deny, spec.status,
                                               has_selector=bool(row.selector))
        cell = AccessMatrixCell(matrix_run_id=mrun.id, row_key=row.key, persona_key=col,
                                expect=spec.expect, expected_status=spec.status, verdict=verdict,
                                reason=reason, observed=obs, created_at=started,
                                completed_at=finished)
        ctx.session.add(cell)
        cells.append(cell)
    await ctx.session.flush()

    names = {a: alice.name, b: bob.name, anon: "anonymous"}
    found = await ctx.session.run_sync(lambda s: am.apply_findings(s, matrix, mrun, cells, names))
    mrun.summary = {**am.summarize(cells), "findings": found}
    ctx.session.add(mrun)
    await ctx.session.flush()
    # apply_findings stamps the finding with the wall clock; the run happened at `finished`.
    for f in (await ctx.session.exec(select(SecurityFinding).where(
            SecurityFinding.project_id == web.id,
            SecurityFinding.scan_type == am.FINDING_SCAN_TYPE))).all():
        f.created_at = finished
        ctx.session.add(f)


# ---------------------------------------------------------------------------
# Access reviews
# ---------------------------------------------------------------------------

async def _access_reviews(ctx) -> None:
    from app.models import AccessReviewCampaign, AccessReviewItem
    from app.services import access_review

    report = await access_review.build_report(ctx.session, ctx.ws.id)
    items = access_review.items_from_report(report)
    members = {m.id for m in _members(ctx)}

    # Last quarter's review: every item decided, signed off.
    started = ctx.ago(days=47)
    done = AccessReviewCampaign(workspace_id=ctx.ws.id, name="Q3 demo workspace access review",
                                status="signed_off", created_by_id=ctx.user.id, created_at=started,
                                signed_off_by_id=ctx.user.id, signed_off_at=ctx.ago(days=44),
                                sign_off_note="Reviewed with the team leads; one stale key revoked.")
    ctx.session.add(done)
    await ctx.session.flush()
    revoked = 0
    for i, it in enumerate(items):
        decision, note = "keep", None
        if it["subject_type"] == "api_key" and not revoked:
            decision, note, revoked = "revoke", "CI key from the old pipeline; replaced in August.", 1
        elif it["subject_type"] == "user" and it["subject_id"] in members:
            note = "Still on the release rota."
        ctx.session.add(AccessReviewItem(campaign_id=done.id, decision=decision, note=note,
                                         decided_by_id=ctx.user.id,
                                         decided_at=started + timedelta(days=1, minutes=5 * i), **it))
    await _audit(ctx, entity_type="access_review", entity_id=done.id, action="sign_off",
                 changes={"name": done.name, "keep": len(items) - revoked, "revoke": revoked})

    # This quarter's review: started, partly decided.
    current = AccessReviewCampaign(workspace_id=ctx.ws.id, name="Q4 demo workspace access review",
                                   status="open", created_by_id=ctx.user.id, created_at=ctx.ago(days=2))
    ctx.session.add(current)
    await ctx.session.flush()
    for i, it in enumerate(items):
        decided = i == 0          # the reviewer has started with themselves
        ctx.session.add(AccessReviewItem(
            campaign_id=current.id, decision="keep" if decided else None,
            decided_by_id=ctx.user.id if decided else None,
            decided_at=ctx.ago(days=1) if decided else None, **it))
    await _audit(ctx, entity_type="access_review", entity_id=current.id, action="start",
                 changes={"name": current.name, "scope": report["scope"],
                          "users": len(report["users"]), "api_keys": len(report["api_keys"])})


# ---------------------------------------------------------------------------
# Estate
# ---------------------------------------------------------------------------

class _Dns:
    """Canned DNS answers: SPF softfail, DMARC p=none, no CAA, no DNSKEY."""

    def __init__(self, records: Dict[tuple, List[str]]):
        self.records = records

    def query(self, name: str, rdtype: str) -> List[str]:
        return list(self.records.get((name.rstrip(".").lower(), rdtype.upper()), []))


def _estate_net(hsts_on_www: bool, now: datetime):
    from app.services import estate_checks as ec
    records = {
        ("example.com", "SOA"): ["ns.icann.org. noc.dns.icann.org. 2025082245 7200 3600 1209600 3600"],
        ("example.com", "TXT"): ["v=spf1 include:_spf.example.com ~all"],
        ("_dmarc.example.com", "TXT"): ["v=DMARC1; p=none; rua=mailto:dmarc@example.com"],
        ("example.com", "MX"): ["10 mail.example.com."],
        ("default._domainkey.example.com", "TXT"): ["v=DKIM1; k=rsa; p=DEMO"],
        ("example.com", "A"): ["192.0.2.10"],
        ("www.example.com", "A"): ["192.0.2.10"],
    }

    def cert(host: str) -> Dict[str, Any]:
        return {"not_after": (now + timedelta(days=84)).replace(microsecond=0).isoformat(),
                "issuer": "Demo CA (example)", "subject": host,
                "san": ["example.com", "www.example.com"]}

    def fetch(url: str, *a, **kw):
        if url.startswith("http://"):
            return 301, {"Location": url.replace("http://", "https://", 1)}, ""
        headers = {"content-type": "text/html"}
        if url.startswith("https://example.com") or hsts_on_www:
            headers["strict-transport-security"] = "max-age=31536000; includeSubDomains"
        return 200, headers, "<html>Example Domain</html>"

    return ec.Net(dns=_Dns(records), fetch=fetch, cert=cert, ct=None)


async def _estate(ctx) -> None:
    from app.models import Estate, EstateDomain, EstateDomainResult, EstateScan
    from app.services import estate_checks as ec
    from app.services.estate_findings import apply_findings

    web = ctx.projects["web"]
    created = ctx.ago(days=20)
    # schedule off: beat would otherwise scan example.com for real.
    estate = Estate(project_id=web.id, name="example.com estate (demo)", dkim_selectors=["default"],
                    discover_subdomains=False, schedule_enabled=False, next_scan_at=None,
                    cert_alert_days=[30, 14, 7], created_by_id=ctx.user.id, created_at=created,
                    updated_at=created)
    ctx.session.add(estate)
    await ctx.session.flush()
    domains = [EstateDomain(estate_id=estate.id, hostname="example.com", source="user", is_apex=True,
                            created_at=created),
               EstateDomain(estate_id=estate.id, hostname="www.example.com", source="user",
                            is_apex=False, created_at=created)]
    ctx.session.add_all(domains)
    await ctx.session.flush()

    # Two scans: www had no HSTS a week ago and has it now, so its finding is
    # verified fixed by the rescan and the score trend goes up.
    for when, hsts_on_www in ((ctx.ago(days=8, hours=2), False), (ctx.ago(days=1, hours=2), True)):
        scan = EstateScan(estate_id=estate.id, status="completed", trigger="schedule",
                          created_at=when, started_at=when, finished_at=when + timedelta(seconds=40))
        ctx.session.add(scan)
        await ctx.session.flush()
        net = _estate_net(hsts_on_www, when)
        scores, produced, scopes = {}, [], set()
        for dom in domains:
            rep = ec.scan_host(net, dom.hostname, is_apex=dom.is_apex,
                               dkim_selectors=estate.dkim_selectors, now=when)
            score, deductions = rep.score()
            ctx.session.add(EstateDomainResult(scan_id=scan.id, domain_id=dom.id, score=score,
                                               checks=[c.as_dict() for c in rep.checks],
                                               deductions=deductions, created_at=when))
            dom.score, dom.last_scanned_at = score, when
            dom.cert_expires_at = rep.cert_expires_at
            ctx.session.add(dom)
            scores[dom.hostname] = score
            produced += ec.findings_from_checks(dom.hostname, rep.checks)
            scopes |= {(ec.FAMILY_SCAN_TYPE[f], ec.host_url(dom.hostname)) for f in rep.families
                       if f in ec.FAMILY_SCAN_TYPE}
        await ctx.session.flush()
        stats = await ctx.session.run_sync(
            lambda s: apply_findings(s, web.id, produced, scopes, now=when))
        worst = sorted(scores.items(), key=lambda kv: kv[1])[:5]
        scan.summary = {"domains_scanned": len(scores), "skipped_not_allowlisted": [],
                        "discovered": [], "average_score": round(sum(scores.values()) / len(scores)),
                        "worst": [{"hostname": h, "score": s} for h, s in worst],
                        "findings": stats, "notes": [], "cert_alerts": 0}
        estate.last_scan_at = when
        ctx.session.add_all([scan, estate])
        await ctx.session.flush()


# ---------------------------------------------------------------------------
# Monitored page (script inventory)
# ---------------------------------------------------------------------------

async def _monitored_page(ctx) -> None:
    from sqlmodel import select

    from app.models import MonitoredPage, PageScript
    from app.tasks.estate_tasks import apply_page_observation

    web = ctx.projects["web"]
    url = f"{WEB_ORIGIN}/checkout"
    # schedule off: beat would otherwise dispatch a real page check.
    page = MonitoredPage(project_id=web.id, name="Checkout page (demo)", url=url, track_scripts=True,
                         track_content=False, schedule_enabled=False, next_check_at=None,
                         created_by_id=ctx.user.id, created_at=ctx.ago(days=15))
    ctx.session.add(page)
    await ctx.session.flush()

    app_js = f"{WEB_ORIGIN}/static/app.js"
    pay_js = "https://payments.example.net/v3/checkout.js"
    tag_js = "https://tags.example.org/gtm.js"
    headers = {"content-security-policy": "default-src 'self'; script-src 'self' payments.example.net "
                                          "tags.example.org",
               "x-content-type-options": "nosniff"}

    def obs(tag_body: str) -> Dict[str, Any]:
        return {"final_url": url, "headers": headers,
                "scripts": [{"src": "/static/app.js"}, {"src": pay_js},
                            {"src": tag_js}, {"inline_sha384": _sha("window.dataLayer=[];")}],
                "external_hashes": {app_js: _sha("app.js v41"), pay_js: _sha("checkout.js v3"),
                                    tag_js: _sha(tag_body)}}

    first_at = ctx.ago(days=14)
    await ctx.session.run_sync(lambda s: apply_page_observation(s, page, obs("gtm 2026-09"), now=first_at))
    await ctx.session.flush()
    scripts = (await ctx.session.exec(select(PageScript).where(PageScript.page_id == page.id))).all()
    reasons = {app_js: "First-party bundle, built and reviewed in CI.",
               pay_js: "Payment provider's hosted checkout; required for card entry (PCI DSS 6.4.3).",
               tag_js: "Marketing tag manager, approved by the web team for conversion tracking."}
    for sc in scripts:
        if sc.src in reasons:
            sc.authorized, sc.justification = True, reasons[sc.src]
            sc.authorized_by_id, sc.authorized_at = ctx.user.id, ctx.ago(days=13)
            ctx.session.add(sc)
            await _audit(ctx, entity_type="page_script", entity_id=sc.id, action="approve",
                         changes={"src": sc.src, "justification": sc.justification})
    await ctx.session.flush()
    # Later the tag manager's body changed: its approval is revoked and the
    # change is in the page's history. The inline config stays unapproved.
    second_at = ctx.ago(days=2, hours=4)
    await ctx.session.run_sync(lambda s: apply_page_observation(s, page, obs("gtm 2026-10"), now=second_at))
    await ctx.session.flush()


# ---------------------------------------------------------------------------
# SOC 2 controls
# ---------------------------------------------------------------------------

async def _compliance(ctx) -> None:
    from app.models import ComplianceControl, ControlEvidence, ControlException
    from app.services import compliance as svc

    created = ctx.ago(days=40)
    controls: Dict[str, Any] = {}
    for item in svc.SOC2_STARTER:
        c = ComplianceControl(workspace_id=ctx.ws.id, framework="SOC 2", reference=item["reference"],
                              title=item["title"], description=item["description"],
                              evidence_sources=svc.validate_sources(item["sources"]),
                              freshness_days=svc.DEFAULT_FRESHNESS_DAYS, seed_key=f"soc2:{item['key']}",
                              owner_id=ctx.user.id, created_by_id=ctx.user.id, created_at=created,
                              updated_at=created)
        ctx.session.add(c)
        controls[item["key"]] = c
    await ctx.session.flush()
    await _audit(ctx, entity_type="workspace", entity_id=ctx.ws.id, action="compliance_load_soc2",
                 changes={"created": len(controls)})

    notes = [
        ("cc6.1-secrets", "pass", 30, "Rotation run with scripts/rotate_secrets.py; old key retired "
                                      "after the overlap window."),
        ("cc6.6-network", "pass", 21, "Reviewed net_guard configuration: private targets off."),
        ("cc8.1-change", "pass", 12, "Branch protection on main: 1 review + green CI required."),
        ("cc8.1-agent-changes", "pass", 9, "Proposal queue reviewed; auto-apply threshold 0.9."),
        ("cc7.2-monitoring", "fail", 6, "Alert rules not yet routed to the on-call rota."),
        ("p4.3-deletion", "pass", 120, "Erasure request handled end to end (evidence now stale)."),
    ]
    for key, outcome, days, note in notes:
        ctx.session.add(ControlEvidence(control_id=controls[key].id, workspace_id=ctx.ws.id,
                                        outcome=outcome, note=note, created_by_id=ctx.user.id,
                                        created_at=ctx.ago(days=days)))
    # An exception: approved when a second person exists (four-eyes), else pending.
    requester = next(iter(_members(ctx)), None)
    exc = ControlException(
        control_id=controls["cc6.1-sso"].id, workspace_id=ctx.ws.id,
        reason="The demo workspace uses password login with MFA; SSO arrives with the IdP project.",
        compensating_control="MFA required for every admin; access review every quarter.",
        requested_by_id=(requester or ctx.user).id, requested_at=ctx.ago(days=10),
        expires_at=ctx.now + timedelta(days=80))
    if requester is not None:
        exc.status, exc.approved_by_id, exc.approved_at = "approved", ctx.user.id, ctx.ago(days=9)
        exc.decided_note = "Approved until the IdP rollout; revisit at the Q1 review."
    ctx.session.add(exc)


# ---------------------------------------------------------------------------
# Trust center
# ---------------------------------------------------------------------------

async def _trust_center(ctx) -> None:
    import secrets as pysecrets

    from app.models import StatusPage, TrustCenter, TrustIncidentPost

    api = ctx.projects["api"]
    page = StatusPage(project_id=api.id, slug=pysecrets.token_urlsafe(9), title="Public API status",
                      enabled=True, created_at=ctx.ago(days=25))
    ctx.session.add(page)
    await ctx.session.flush()
    center = TrustCenter(
        workspace_id=ctx.ws.id, slug=pysecrets.token_urlsafe(12), enabled=False,
        title="Demo Co trust center",
        intro="How we keep your data safe: our security posture, live service status and "
              "incident history. **Draft** — not published yet.",
        contact_email="security@example.com", status_page_ids=[page.id],
        posture_items=["mfa_admins", "audit_chain"], allow_access_requests=False,
        updated_by_id=ctx.user.id, created_at=ctx.ago(days=6), updated_at=ctx.ago(days=6))
    ctx.session.add(center)
    at = ctx.ago(days=1, hours=3)
    ctx.session.add(TrustIncidentPost(
        workspace_id=ctx.ws.id, title="Elevated API latency", status="identified",
        components=["Public API"], published=False, created_by_id=ctx.user.id,
        created_at=at, updated_at=at + timedelta(minutes=40),
        updates=[{"at": at.isoformat(), "status": "investigating",
                  "body": "We are investigating slow responses from the /delay endpoint."},
                 {"at": (at + timedelta(minutes=40)).isoformat(), "status": "identified",
                  "body": "An upstream dependency is timing out. A fix is being rolled out."}]))


async def seed(ctx) -> None:
    _enable_security(ctx)
    await _zap_scans(ctx)
    await _access_matrix(ctx)
    await _access_reviews(ctx)
    await _estate(ctx)
    await _monitored_page(ctx)
    await _compliance(ctx)
    await _trust_center(ctx)
