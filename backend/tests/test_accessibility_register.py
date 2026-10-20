"""Accessibility register (WP15d) — pure rules: glob, waivers, ingestion,
new/fixed. DB-backed behaviour is in tests/integration/test_accessibility_db.py."""
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models import QualityGatePolicy
from app.services import accessibility as a11y


def test_glob_matches_star_and_literals():
    assert a11y.glob_match("*", "anything")
    assert a11y.glob_match("*", "")
    assert a11y.glob_match("#nav *", "#nav > li > a")
    assert a11y.glob_match("*.btn", "form .btn")
    assert not a11y.glob_match("#nav *", "#main a")
    assert a11y.glob_match("a*b*c", "aXXbYYc")
    assert not a11y.glob_match("a*b*c", "aXXbYY")
    # No regex semantics: brackets and dots are literal.
    assert a11y.glob_match("[name]", "[name]")
    assert not a11y.glob_match("a.c", "abc")


def test_glob_is_linear_on_hostile_input():
    started = time.monotonic()
    assert not a11y.glob_match("*a" * 50 + "b", "a" * 5000)
    assert time.monotonic() - started < 2.0


def _waiver(rule="color-contrast", pattern="*", days=10, now=None):
    now = now or datetime.utcnow()
    return SimpleNamespace(id=1, rule_id=rule, selector_pattern=pattern,
                           expires_at=now + timedelta(days=days))


def test_waiver_covers_only_its_rule_and_pattern_until_expiry():
    now = datetime(2026, 10, 1)
    w = _waiver(pattern="#footer *", now=now)
    assert a11y.waiver_covers(w, "color-contrast", "#footer > a", now)
    assert not a11y.waiver_covers(w, "image-alt", "#footer > a", now)
    assert not a11y.waiver_covers(w, "color-contrast", "#header > a", now)
    later = now + timedelta(days=11)
    assert a11y.waiver_state(w, later) == "expired"
    # Expired waivers exclude nothing: the violation counts again.
    assert not a11y.waiver_covers(w, "color-contrast", "#footer > a", later)


def test_every_waiver_expires_within_90_days():
    w = SimpleNamespace(expires_at=None, updated_at=None)
    now = datetime(2026, 10, 1)
    a11y.set_expiry(w, 90, now)
    assert w.expires_at == now + timedelta(days=90)
    for bad in (0, 91, -1, True, "30"):
        with pytest.raises(ValueError):
            a11y.set_expiry(w, bad, now)


def test_rule_and_pattern_validation():
    assert a11y.validate_rule_id(" Color-Contrast ") == "color-contrast"
    for bad in ("", "color contrast", "x" * 81, "a;drop"):
        with pytest.raises(ValueError):
            a11y.validate_rule_id(bad)
    assert a11y.validate_pattern("") == "*"
    with pytest.raises(ValueError):
        a11y.validate_pattern("x" * 301)


def _raw(**over):
    v = {"id": "image-alt", "impact": "critical", "node_count": 2,
         "help_url": "https://dequeuniversity.com/rules/axe/image-alt?x=1",
         "targets": [{"selector": 'img[src="/a.png?sig=1"]', "nodes": 2}]}
    v.update(over)
    return [{"step_id": "s1", "url": "https://app.example/shop?session=abc#x", "violations": [v]}]


def test_normalise_rows_are_redacted_and_fingerprinted():
    checks = a11y.normalise_results(_raw())
    assert len(checks) == 1
    c = checks[0]
    assert c["page_key"] == "https://app.example/shop"
    row = c["violations"][0]
    assert row["target"] == "img[src]"
    assert row["help_url"] == "https://dequeuniversity.com/rules/axe/image-alt"
    assert row["fingerprint"] == a11y.fingerprint("image-alt", "https://app.example/shop", "img[src]")


def test_normalise_counts_nodes_beyond_the_listed_targets():
    rows = a11y.normalise_results(_raw(node_count=30))[0]["violations"]
    assert {r["target"]: r["node_count"] for r in rows} == {"img[src]": 2, a11y.MORE_TARGET: 28}


def test_normalise_drops_bad_rules_and_defaults_unknown_impact():
    rows = a11y.normalise_results(
        _raw(id="<script>") + _raw(impact="weird", id="label"))
    assert [r["rule_id"] for c in rows for r in c["violations"]] == ["label"]
    assert rows[1]["violations"][0]["impact"] == "minor"
    assert a11y.normalise_results("garbage") == []


def test_clean_scan_is_still_evidence():
    checks = a11y.normalise_results([{"step_id": "s1", "url": "https://a.example/", "violations": []}])
    assert checks == [{"step_id": "s1", "page_key": "https://a.example/", "violations": []}]


def test_new_without_a_baseline_is_everything():
    assert a11y.new_fingerprints({"a", "b"}, None) == {"a", "b"}
    assert a11y.new_fingerprints({"a", "b"}, {"a"}) == {"b"}


def test_impact_threshold_and_comparable_key():
    assert a11y.at_or_above("serious") and a11y.at_or_above("critical")
    assert not a11y.at_or_above("moderate")
    assert a11y.comparable_key(5, "x", "firefox", None) == ("case", 5, "firefox", "")
    assert a11y.comparable_key(None, "x", None, "Pixel 7") == ("name", "x", "chromium", "Pixel 7")


def test_gate_check_is_opt_in():
    assert QualityGatePolicy().block_new_accessibility_violations is False
