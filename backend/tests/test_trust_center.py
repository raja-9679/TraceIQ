"""WP13b trust center: posture derivation, safe incident rendering, document
sniffing and the public payload allow-lists. Pure functions only; the DB side
is tests/integration/test_trust_center_db.py."""
import json
import re
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services import trust_center as tc

ALL = list(tc.POSTURE_KEYS)
FULL = {
    "sso_enforced": True, "mfa_admins": False, "encryption_at_rest": True,
    "audit_verified_at": datetime(2026, 10, 1, 9, 30), "audit_verified_ok": True,
    "open_critical_findings": 3, "last_access_review_at": datetime(2026, 9, 30, 12),
    "last_restore_drill_at": None,
}


def _by_key(items):
    return {i["key"]: i for i in items}


def test_nothing_is_published_unless_opted_in():
    assert tc.derive_posture(FULL, []) == []
    assert tc.derive_posture(FULL, None) == []
    only = tc.derive_posture(FULL, ["encryption_at_rest"])
    assert [i["key"] for i in only] == ["encryption_at_rest"]


def test_each_item_shown_with_data_and_hidden_without():
    shown = _by_key(tc.derive_posture(FULL, ALL))
    assert shown["sso_enforced"]["value"] == "Yes"
    assert shown["mfa_admins"]["value"] == "No" and shown["mfa_admins"]["state"] == "attention"
    assert shown["encryption_at_rest"]["value"] == "Yes"
    assert shown["audit_chain"]["value"] == "Chain verified on 2026-10-01"
    assert shown["critical_findings"]["value"] == "1-4"
    assert shown["access_review"]["value"] == "2026-09-30"
    # No restore-drill data in the product: hidden, never printed as "no".
    assert "restore_drill" not in shown

    empty = {k: None for k in FULL}
    assert tc.derive_posture(empty, ALL) == []
    preview = _by_key(tc.posture_preview(empty, ["sso_enforced"]))
    assert preview["sso_enforced"]["published"] is True
    assert preview["sso_enforced"]["available"] is False
    assert all(not p["available"] for p in preview.values())


def test_audit_item_reports_a_failed_check_without_details():
    item = _by_key(tc.derive_posture({**FULL, "audit_verified_ok": False}, ["audit_chain"]))["audit_chain"]
    assert item["value"] == "Last check on 2026-10-01 did not verify"
    assert set(item) == {"key", "label", "value", "state"}


@pytest.mark.parametrize("n,band", [(0, "None"), (1, "1-4"), (4, "1-4"), (5, "5-19"),
                                    (19, "5-19"), (20, "20 or more"), (500, "20 or more")])
def test_findings_are_a_count_band(n, band):
    assert tc.count_bucket(n) == band
    item = tc.derive_posture({"open_critical_findings": n}, ["critical_findings"])[0]
    assert item["value"] == band


def test_no_finding_details_can_leak():
    # Even if a caller put rich data in the facts, an item is only
    # {key, label, value, state} and the value is the band.
    facts = {**FULL, "open_critical_findings": 2,
             "finding_titles": ["SQL injection in /admin/login"], "titles": ["x"]}
    out = json.dumps(tc.derive_posture(facts, ALL))
    assert "SQL injection" not in out and "/admin" not in out
    for item in tc.derive_posture(facts, ALL):
        assert set(item) == {"key", "label", "value", "state"}


def test_unknown_posture_keys_are_refused_and_order_is_canonical():
    with pytest.raises(ValueError):
        tc.clean_posture_keys(["sso_enforced", "everything"])
    assert tc.clean_posture_keys(["access_review", "sso_enforced"]) == ["sso_enforced", "access_review"]


# --- incident rendering ------------------------------------------------------------

ALLOWED_TAGS = {"p", "br", "ul", "li", "strong", "em", "code"}


def _tags(html):
    return set(re.findall(r"</?([a-zA-Z0-9]+)", html))


@pytest.mark.parametrize("src", [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "**<b onclick=\"x\">bold</b>**",
    "`<iframe src=//evil>`",
    "- <svg/onload=alert(1)>\n- ok",
    "[click](javascript:alert(1))",
    "\" onmouseover=\"alert(1)",
])
def test_rendering_escapes_html(src):
    out = tc.render_markdown_safe(src)
    assert _tags(out) <= ALLOWED_TAGS
    # Strip the allowed bare tags: nothing left may open a tag or an attribute.
    rest = re.sub(r"</?(p|br|ul|li|strong|em|code)>", "", out)
    assert "<" not in rest and ">" not in rest and '"' not in rest
    assert "href" not in out


def test_markdown_subset():
    out = tc.render_markdown_safe("We **identified** the *cause* in `api`.\nFix rolling out.\n\n- EU\n- US")
    assert out == ("<p>We <strong>identified</strong> the <em>cause</em> in <code>api</code>.<br>"
                   "Fix rolling out.</p><ul><li>EU</li><li>US</li></ul>")
    assert tc.render_markdown_safe("") == "" and tc.render_markdown_safe(None) == ""


def test_public_incident_fields():
    post = SimpleNamespace(id=4, title="API latency", status="monitoring", components=["API"],
                           updates=[{"at": "2026-10-01T10:00:00", "status": "investigating", "body": "<b>x</b>",
                                     "author_email": "ops@corp.example.com"},
                                    {"at": "2026-10-01T11:00:00", "status": "monitoring", "body": "Fixed"}],
                           published_at=datetime(2026, 10, 1, 10), created_at=datetime(2026, 10, 1, 9),
                           resolved_at=None, created_by_id=7, published=True)
    out = tc.public_incident(post)
    assert set(out) == {"id", "title", "status", "components", "started_at", "resolved_at", "updates"}
    assert [u["status"] for u in out["updates"]] == ["monitoring", "investigating"]   # newest first
    assert set(out["updates"][0]) == {"at", "status", "body_html"}
    assert "&lt;b&gt;" in out["updates"][1]["body_html"]
    assert "ops@corp" not in json.dumps(out)


def test_public_page_allow_list():
    center = SimpleNamespace(title="Acme trust", intro="Hi", contact_email="security@acme.example",
                             status_page_ids=[1], allow_access_requests=True, slug="s", workspace_id=1,
                             posture_items=["sso_enforced"], audit_verified_ok=True)
    monitors = [{"name": "Checkout", "state": "up", "uptime_24h": 100.0, "uptime_7d": 99.5,
                 "last_checked_at": "2026-10-01T00:00:00", "url": "https://internal.example/health"}]
    doc = SimpleNamespace(id=3, title="SOC 2 report", description="2026", object_key="trust/1/abc",
                          filename="soc2.pdf", sha256="f" * 64)
    page = tc.public_page(center, monitors, [], [{"key": "sso_enforced", "label": "SSO", "value": "Yes",
                                                  "state": "good"}], [doc])
    assert set(page) == {"title", "intro", "contact_email", "status", "incidents", "posture",
                         "documents", "access_requests", "generated_at"}
    assert page["status"]["monitors"] == [{"name": "Checkout", "state": "up", "uptime_24h": 100.0,
                                           "uptime_7d": 99.5}]
    assert page["status"]["overall"] == "operational"
    assert page["documents"] == [{"id": 3, "title": "SOC 2 report", "description": "2026"}]
    dumped = json.dumps(page)
    assert "trust/1/abc" not in dumped and "internal.example" not in dumped and "soc2.pdf" not in dumped
    # No status pages chosen: no status section at all.
    center.status_page_ids = []
    assert tc.public_page(center, [], [], [], [])["status"] is None


def test_overall_state():
    assert tc.overall_state([]) == "unknown"
    assert tc.overall_state([{"state": "up"}, {"state": "down"}]) == "degraded"
    assert tc.overall_state([{"state": "down"}]) == "down"
    assert tc.overall_state([{"state": "up"}, {"state": "unknown"}]) == "operational"


# --- documents -----------------------------------------------------------------------

@pytest.mark.parametrize("head,name,expected", [
    (b"%PDF-1.7\n...", "report.pdf", "application/pdf"),
    (b"%PDF-1.7\n...", "report.html", None),
    (b"\x89PNG\r\n\x1a\n....", "diagram.png", "image/png"),
    (b"\x89PNG\r\n\x1a\n....", "diagram.pdf", None),
    (b"\xff\xd8\xff\xe0", "photo.jpg", "image/jpeg"),
    (b"PK\x03\x04....", "policy.docx", tc._OFFICE[".docx"]),
    (b"PK\x03\x04....", "archive.zip", None),
    (b"<html><script>alert(1)</script>", "page.html", None),
    (b"<svg onload=alert(1)>", "logo.svg", None),
    (b"Security contact: ...", "notes.txt", "text/plain"),
    (b"hello <script>x</script>", "notes.txt", None),
    (b"bin\x00ary", "notes.txt", None),
    (b"%PDF-1.4", "noext", "application/pdf"),
])
def test_sniff_document(head, name, expected):
    assert tc.sniff_document(head, name) == expected


def test_safe_filename():
    assert tc.safe_filename("../../etc/passwd") == "passwd"
    assert tc.safe_filename('a"b<c>.pdf') == "a_b_c_.pdf"
    assert tc.safe_filename(None) == "document"
