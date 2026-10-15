"""Script inventory + defacement logic (WP9, PCI DSS 4.0 6.4.3 / 11.6.1).

    ./run-tests.sh tests/test_script_inventory.py -q
"""
import base64
import hashlib
from types import SimpleNamespace

from app.services import script_inventory as si
from app.services.estate_checks import text_similarity

PAGE = "https://pay.example.co.in/checkout"


def test_sri_hash_format_matches_the_integrity_attribute():
    data = b"alert(1)"
    expect = "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode()
    assert si.sri_sha384(data) == expect


def test_third_party_classification_with_two_label_suffixes():
    assert si.site_of("cdn.pay.example.co.in") == "example.co.in"
    assert si.is_third_party("https://static.example.co.in/a.js", PAGE) is False
    assert si.is_third_party("https://js.stripe.com/v3", PAGE) is True
    assert si.is_third_party("https://other.co.in/x.js", PAGE) is True
    assert si.is_third_party("not a url", PAGE) is True


def test_build_entries_normalises_and_skips_data_scripts():
    raw = [
        {"src": "/static/app.js?v=12", "integrity": "sha384-abc"},
        {"src": "https://js.stripe.com/v3/"},
        {"inline": "window.cfg = {a: 1}"},
        {"inline": "   "},                                        # empty: skipped
        {"inline": '{"@context": "x"}', "type": "application/ld+json"},   # data, not code
        {"src": "data:text/javascript,alert(1)"},
        {"inline_sha384": "sha384-precomputed"},
        {"src": "/static/app.js?v=12"},                           # duplicate
    ]
    out = si.build_entries(raw, PAGE, {"https://pay.example.co.in/static/app.js?v=12": "sha384-body"})
    keys = [e["script_key"] for e in out]
    assert keys[0] == "https://pay.example.co.in/static/app.js"      # query dropped from identity
    assert out[0]["src"].endswith("?v=12") and out[0]["sha384"] == "sha384-body"
    assert out[0]["third_party"] is False and out[1]["third_party"] is True
    assert out[2]["script_key"] == "inline:" + si.sri_sha384(b"window.cfg = {a: 1}")
    assert out[3]["script_key"].startswith("embedded:sha384-")
    assert out[4]["script_key"] == "inline:sha384-precomputed"
    assert len(out) == 5


def test_inline_hash_is_identical_whether_hashed_by_worker_or_backend():
    body = "var x = 'ü';"
    worker = si.build_entries([{"inline_sha384": si.sri_sha384(body.encode("utf-8"))}], PAGE)
    backend = si.build_entries([{"inline": body}], PAGE)
    assert worker[0]["script_key"] == backend[0]["script_key"]


def test_diff_inventory_added_changed_removed():
    known = [
        {"script_key": "https://a.com/x.js", "sha384": "h1", "src": "https://a.com/x.js?v=1"},
        {"script_key": "https://b.com/y.js", "sha384": "h2", "src": "https://b.com/y.js"},
        {"script_key": "https://c.com/z.js", "sha384": "h3", "src": "https://c.com/z.js"},
    ]
    current = [
        {"script_key": "https://a.com/x.js", "sha384": "h1", "src": "https://a.com/x.js?v=2"},  # url moved
        {"script_key": "https://b.com/y.js", "sha384": "h2-new", "src": "https://b.com/y.js"},  # body moved
        {"script_key": "https://d.com/new.js", "sha384": "h4", "src": "https://d.com/new.js"},
    ]
    d = si.diff_inventory(known, current)
    assert [a["script_key"] for a in d["added"]] == ["https://d.com/new.js"]
    assert sorted(c["script_key"] for c in d["changed"]) == ["https://a.com/x.js", "https://b.com/y.js"]
    assert [r["script_key"] for r in d["removed"]] == ["https://c.com/z.js"]
    b = next(c for c in d["changed"] if c["script_key"] == "https://b.com/y.js")
    assert b["previous_sha384"] == "h2"


def test_unhashed_script_is_compared_by_url_only():
    known = [{"script_key": "k", "sha384": "h1", "src": "https://a.com/k.js"}]
    current = [{"script_key": "k", "sha384": None, "src": "https://a.com/k.js"}]
    assert si.diff_inventory(known, current)["changed"] == []


def test_security_header_diff():
    old = si.security_headers({"Content-Security-Policy": "default-src 'self'", "X-Frame-Options": "DENY",
                               "Set-Cookie": "sid=1"})
    assert "set-cookie" not in old
    new = si.security_headers({"content-security-policy": "default-src *", "referrer-policy": "no-referrer"})
    changes = {c["header"]: c["change"] for c in si.diff_headers(old, new)}
    assert changes == {"content-security-policy": "changed", "x-frame-options": "removed",
                       "referrer-policy": "added"}
    assert si.diff_headers(None, new) == []        # first observation is the baseline


def test_similarity_and_normalisation():
    assert si.normalise_text("Price: ₹1,299  on\n12/10") == "price: ₹0,000 on 00/00"
    a = si.normalise_text("Welcome to our shop. Pay securely with card or UPI today.")
    b = si.normalise_text("Welcome to our shop. Pay securely with card or UPI now.")
    assert text_similarity(a, b) > 0.6
    assert text_similarity(a, si.normalise_text("Hacked by someone. Your site is owned.")) == 0.0
    assert text_similarity("", "") == 1.0 and text_similarity("a b c", "") == 0.0


def test_keyword_hits_are_plain_case_insensitive_substrings():
    text = "HACKED BY crew. (.*)+ is not a regex here"
    assert si.keyword_hits(text, ["hacked by", "casino", "(.*)+", " ", "Hacked By"]) == ["hacked by", "(.*)+", "Hacked By"]


def test_element_checks():
    checks = [
        {"selector": "#pay", "must_contain": "Pay now"},
        {"selector": "h1", "must_not_contain": ["hacked", ""]},
        {"selector": "#gone"},
        {"selector": "  "},
    ]
    fails = si.evaluate_element_checks(checks, {"#pay": "Pay later", "h1": "HACKED", "#gone": None})
    assert fails == [
        {"selector": "#pay", "reason": "does not contain 'Pay now'"},
        {"selector": "h1", "reason": "contains forbidden 'hacked'"},
        {"selector": "#gone", "reason": "element not found"},
    ]


def test_defacement_signals_uses_the_baseline():
    page = SimpleNamespace(content_baseline=si.normalise_text("Checkout with card or UPI securely here"),
                           forbidden_keywords=["defaced"], element_checks=[{"selector": "h1", "must_contain": "Checkout"}])
    sim, hits, fails = si.defacement_signals(page, "DEFACED by crew", {"h1": "Owned"})
    assert sim == 0.0 and hits == ["defaced"] and fails[0]["selector"] == "h1"
    page.content_baseline = None
    assert si.defacement_signals(page, "x", {"h1": "Checkout"})[0] is None
