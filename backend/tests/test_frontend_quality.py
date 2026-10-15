"""WP10 frontend quality: pure logic (no database).

Matrix option validation + fan-out arithmetic, crawl config / result
normalisation, page-budget sample normalisation, selector-health aggregation
and locator suggestions, Storybook index parsing, and the step catalog.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import frontend_matrix as fm
from app.services import page_budgets as pb
from app.services import selector_health as sh
from app.services import site_crawl as sc
from app.services import storybook as sb


# --- frontend matrix --------------------------------------------------------

def test_viewport_presets_and_custom_sizes():
    vps = fm.normalise_viewports(["mobile", "Desktop", {"name": "kiosk", "width": 1080, "height": 1920},
                                  "mobile"])
    assert [v["name"] for v in vps] == ["mobile", "desktop", "kiosk"]
    assert vps[0] == {"name": "mobile", "width": 375, "height": 812}
    # A named preset object may omit its size.
    assert fm.normalise_viewports([{"name": "tablet"}])[0]["width"] == 768


@pytest.mark.parametrize("bad", [
    ["phablet"], [{"name": "x", "width": 100, "height": 800}], [{"name": "", "width": 400, "height": 800}],
    [{"name": "x", "width": "400", "height": 800}], [{"name": "x", "width": True, "height": 800}],
    "mobile", [f"v{i}" for i in range(3)],
])
def test_invalid_viewports_are_refused(bad):
    with pytest.raises(fm.MatrixError):
        fm.normalise_viewports(bad)


def test_too_many_viewports():
    with pytest.raises(fm.MatrixError):
        fm.normalise_viewports([{"name": f"v{i}", "width": 400 + i, "height": 800} for i in range(9)])


def test_locales_with_query_cookie_and_template():
    locs = fm.normalise_locales(["en-US", {"locale": "ar-EG", "query": {"lang": "ar"},
                                           "cookie": {"name": "locale", "value": "ar"},
                                           "url_template": "{origin}/{lang}{path}{query}"}])
    assert locs[0] == {"locale": "en-US"}
    assert locs[1]["query"] == {"lang": "ar"}
    assert locs[1]["cookie"] == {"name": "locale", "value": "ar"}
    assert fm.is_rtl_locale("ar-EG") and fm.is_rtl_locale("he") and not fm.is_rtl_locale("en-US")


@pytest.mark.parametrize("bad", [
    ["english"], ["en_US"], [{"locale": "fr", "url_template": "https://evil.example{path}"}],
    [{"locale": "fr", "url_template": "{path}"}], [{"locale": "fr", "url_template": "{origin}/{secret}"}],
    [{"locale": "fr", "cookie": {"name": "a;b", "value": "x"}}],
    [{"locale": "fr", "cookie": {"name": "a", "value": "x;Domain=evil"}}],
    [{"locale": "fr", "query": {"bad key": "x"}}],
])
def test_invalid_locales_are_refused(bad):
    with pytest.raises(fm.MatrixError):
        fm.normalise_locales(bad)


def test_color_schemes():
    assert fm.normalise_color_schemes(["Light", "dark", "dark"]) == ["light", "dark"]
    with pytest.raises(fm.MatrixError):
        fm.normalise_color_schemes(["sepia"])


def test_variants_are_the_cartesian_product_with_labels_and_baseline_keys():
    opts = fm.resolve_options(SimpleNamespace(viewports=["mobile", "desktop"], locales=["en-US", "ar-EG"],
                                              color_schemes=["light", "dark"], reduced_motion=None,
                                              sweep_step_screenshots=None), {})
    variants = fm.build_variants(opts)
    assert len(variants) == 8
    labels = {v["label"] for v in variants}
    assert "mobile 375x812 · ar-EG · dark" in labels
    keys = {v["baseline_key"] for v in variants}
    assert len(keys) == 8 and all(k.startswith("vp:") for k in keys)
    # A real sweep screenshots every step by default.
    assert all(v["step_screenshots"] for v in variants)


def test_no_axis_means_one_ordinary_run():
    assert fm.build_variants(fm.resolve_options(None, {})) == [None]


def test_single_variant_does_not_screenshot_every_step_unless_asked():
    one = fm.build_variants(fm.resolve_options(SimpleNamespace(
        viewports=None, locales=None, color_schemes=["dark"], reduced_motion=None,
        sweep_step_screenshots=None), {}))
    assert len(one) == 1 and one[0]["step_screenshots"] is False
    assert one[0]["color_scheme"] == "dark"


def test_request_axis_overrides_suite_axis_per_axis():
    opts = fm.resolve_options(SimpleNamespace(viewports=["wide"], locales=None, color_schemes=None,
                                              reduced_motion=None, sweep_step_screenshots=None),
                              {"viewports": ["mobile", "tablet"], "color_schemes": ["dark"],
                               "reduced_motion": True})
    assert [v["name"] for v in opts["viewports"]] == ["wide"]
    assert opts["color_schemes"] == ["dark"]
    assert opts["reduced_motion"] is True
    variants = fm.build_variants(opts)
    assert len(variants) == 1 and variants[0]["reduced_motion"] is True


def test_fanout_cap():
    assert fm.check_fanout(2, 1, 8, 1) == 16
    with pytest.raises(fm.MatrixError) as exc:
        fm.check_fanout(3, 2, 8, 3)
    assert "144" in str(exc.value)


# --- site crawl ----------------------------------------------------------------

KNOWN = sc.known_hosts_from(
    ["https://shop.example.com/app", None],
    [{"allowed_domains": ["cdn.example.com", {"domain": "*.assets.example.net"}]}, None],
    ["https://admin.example.com:8443"])


def test_known_hosts_come_from_environments_suites_and_security_allowlist():
    assert KNOWN == {"shop.example.com", "cdn.example.com", "*.assets.example.net", "admin.example.com"}
    assert sc.host_allowed("img.assets.example.net", KNOWN)
    assert not sc.host_allowed("evil.example.com", KNOWN)
    assert not sc.host_allowed("shop.example.com.evil.net", KNOWN)


def test_crawl_config_validation():
    ok = sc.validate_config({"name": "Shop", "start_url": "https://shop.example.com/", "max_pages": 50,
                             "extra_hosts": ["cdn.example.com"], "cron_expression": "0 3 * * *"}, KNOWN)
    assert ok["extra_hosts"] == ["cdn.example.com"] and ok["max_pages"] == 50
    for bad in ({"start_url": "https://elsewhere.example.org/"}, {"start_url": "ftp://shop.example.com/"},
                {"start_url": "https://u:p@shop.example.com/"}, {"extra_hosts": ["evil.example"]},
                {"max_pages": 5000}, {"rate_limit_ms": 0}, {"max_depth": True},
                {"cron_expression": "every day"}, {"name": ""}):
        with pytest.raises(sc.CrawlConfigError):
            sc.validate_config(bad, KNOWN)


def test_snapshot_and_job():
    crawl = SimpleNamespace(start_url="https://shop.example.com/", extra_hosts=["cdn.example.com"], max_depth=2,
                            max_pages=10, respect_robots=True, rate_limit_ms=300, slow_threshold_ms=2000,
                            check_external=False)
    snap = sc.snapshot(crawl)
    assert snap["crawl_hosts"] == ["shop.example.com", "cdn.example.com"]
    job = sc.build_job(42, snap, {"capture_level": "standard"})
    assert job["job_type"] == "site_crawl" and job["result_key"] == "site_crawl:result:42"
    assert job["crawl_run_id"] == 42 and job["data_policy"]["capture_level"] == "standard"


JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"


def test_crawl_result_is_retyped_capped_and_redacted():
    fields, issues = sc.normalise_result({
        "status": "ok", "links_checked": 12, "truncated": False,
        "pages": [{"url": "https://shop.example.com/", "depth": 0, "status": 200, "load_ms": 120.7, "links": 9,
                   "issues": 2}, {"url": 5}],
        "robots": {"respected": True, "fetched": True, "skipped": 1},
        "issues": [
            {"page_url": "https://shop.example.com/", "target_url": f"https://shop.example.com/reset/{JWT}",
             "element": "a", "category": "broken", "status_code": 404, "duration_ms": 30},
            {"page_url": "https://shop.example.com/", "target_url": "https://shop.example.com/x",
             "element": "a", "category": "made-up"},
            {"page_url": "https://shop.example.com/", "target_url": "https://shop.example.com/l",
             "element": "a", "category": "redirect_loop",
             "redirect_chain": [{"url": "https://shop.example.com/l", "status": 302}, "junk"]},
        ],
    })
    assert fields["status"] == "completed" and fields["pages_crawled"] == 1
    assert fields["pages"][0]["load_ms"] == 120
    assert len(issues) == 2
    assert JWT not in issues[0]["target_url"]
    assert issues[1]["redirect_chain"] == [{"url": "https://shop.example.com/l", "status": 302}]
    assert fields["issue_counts"]["broken"] == 1 and fields["issue_counts"]["redirect_loop"] == 1
    assert sc.normalise_result("nope")[0]["status"] == "error"
    assert sc.normalise_result({"status": "error", "error": "boom"})[0]["error"] == "boom"


# --- page budgets -------------------------------------------------------------------

def test_page_budget_samples_are_normalised():
    [s] = pb.normalise_samples([{
        "step_id": "b1", "url": f"https://shop.example.com/cart?session={JWT}#x",
        "metrics": {"lcp_ms": 2800.123456, "cls": 0.02, "bogus": 1, "inp_ms": float("nan")},
        "checks": [{"metric": "lcp_ms", "value": 2800, "budget": 2500, "passed": False},
                   {"metric": "nope", "value": 1}],
        "seo": [{"check": "title", "passed": True, "severity": "error", "value": "Cart"},
                {"check": "made-up", "passed": False}],
        "passed": True,   # the worker said passed, but a budget check failed: re-derived
    }, "junk", {"url": ""}])
    assert s["page_key"] == "https://shop.example.com/cart"
    assert JWT not in s["page_url"]
    assert s["metrics"] == {"lcp_ms": 2800.1235, "cls": 0.02}
    assert [c["metric"] for c in s["checks"]] == ["lcp_ms"]
    assert [c["check"] for c in s["seo"]] == ["title"]
    assert s["passed"] is False


# --- selector health -----------------------------------------------------------------

def test_fragility_reasons():
    assert sh.fragility_reasons('[data-testid="pay"]') == []
    assert sh.fragility_reasons('role=button[name="Pay"]') == []
    assert any("positional" in r for r in sh.fragility_reasons("ul > li:nth-child(3) > a"))
    assert any("XPath" in r for r in sh.fragility_reasons("/html/body/div[3]/button[2]"))
    assert any("generated" in r for r in sh.fragility_reasons(".css-1x2y3z4 .btn"))


def test_suggestions_from_the_marked_target_in_a_dom_excerpt():
    dom = ('<div class="row"><form><label for="email">Work email</label>'
           '<input id="email" name="email" type="email" data-traceiq-target="">'
           '<button class="css-1ab2cd3" data-testid="submit-order" data-traceiq-x="">Place order</button>'
           '</form></div>')
    sugg = [s["locator"] for s in sh.suggest_from_dom(dom)]
    assert sugg[0] == 'role=textbox[name="Work email"]'
    assert "#email" in sugg and 'input[name="email"]' in sugg
    btn = ('<div><button class="css-1ab2cd3" data-testid="submit-order" data-traceiq-target="">'
           'Place <b>order</b></button></div>')
    locs = [s["locator"] for s in sh.suggest_from_dom(btn)]
    assert locs[0] == '[data-testid="submit-order"]'
    assert 'role=button[name="Place order"]' in locs
    assert sh.suggest_from_dom("<div>no target</div>") == []
    assert sh.suggest_from_dom(None) == []


def test_aggregate_counts_weeks_and_suggestions():
    now = datetime(2026, 10, 14, 12)
    heals = [{"test_case_id": 1, "step_id": "s1", "old_selector": "div > div > span:nth-child(2)",
              "new_selector": '[data-testid="total"]', "status": "pending", "confidence": 0.75,
              "created_at": now - timedelta(days=8)}]
    dom = '<p><span data-testid="total" data-traceiq-target="">42</span></p>'
    failures = [
        {"test_case_id": 1, "step_id": "s1", "selector": "div > div > span:nth-child(2)", "status": "failed",
         "run_id": 10, "created_at": now - timedelta(days=1), "dom": dom, "dom_scope": "target"},
        {"test_case_id": 1, "step_id": "s1", "selector": "div > div > span:nth-child(2)", "status": "healed",
         "run_id": 11, "created_at": now},
        {"test_case_id": 2, "step_id": "s9", "selector": "#ok", "status": "failed", "run_id": 12,
         "created_at": now},
    ]
    report = sh.aggregate(heals, failures, {1: {"name": "Checkout", "suite_id": 5}, 2: {"name": "Login", "suite_id": 5}})
    top = report[0]
    assert top["test_case_id"] == 1 and top["case_name"] == "Checkout"
    assert (top["heal_count"], top["failure_count"], top["runtime_healed_count"], top["total"]) == (1, 1, 1, 3)
    assert len(top["weeks"]) == 2
    assert top["suggestions"][0] == {"locator": '[data-testid="total"]', "source": "dom",
                                     "reason": top["suggestions"][0]["reason"]}
    assert top["fragility"]
    assert top["recent_run_ids"] == [11, 10]
    assert report[1]["selector"] == "#ok"


def test_failures_from_payload_only_counts_failed_or_healed_steps_with_a_selector():
    payload = {"step_evidence": [
        {"index": 0, "type": "goto", "status": "passed"},
        {"index": 1, "type": "click", "status": "failed", "selector": "#buy", "step_id": "x"},
        {"index": 2, "type": "click", "status": "healed", "selector": "#a"},
        {"index": 3, "type": "fill", "status": "failed"},
    ]}
    rows = sh.failures_from_payload(payload, 3, 9, datetime.utcnow())
    assert [(r["selector"], r["status"]) for r in rows] == [("#buy", "failed"), ("#a", "healed")]
    assert sh.failures_from_payload(payload, None, 9, datetime.utcnow()) == []


# --- storybook -------------------------------------------------------------------

def test_storybook_index_v7_and_v6():
    v7 = {"v": 5, "entries": {
        "button--primary": {"id": "button--primary", "title": "Button", "name": "Primary", "type": "story",
                            "tags": ["autodocs"]},
        "button--docs": {"id": "button--docs", "title": "Button", "name": "Docs", "type": "docs"},
        "bad id": {"id": "../../etc", "title": "x", "name": "y", "type": "story"},
    }}
    assert [s["id"] for s in sb.parse_index(v7)] == ["button--primary"]
    v6 = {"v": 3, "stories": {"card--default": {"id": "card--default", "kind": "Card", "story": "Default",
                                                "parameters": {}},
                              "intro--page": {"id": "intro--page", "kind": "Intro", "name": "Page",
                                              "parameters": {"docsOnly": True}}}}
    assert sb.parse_index(v6) == [{"id": "card--default", "title": "Card", "name": "Default", "tags": []}]
    with pytest.raises(sb.StorybookError):
        sb.parse_index({"nothing": 1})


def test_storybook_steps_and_root():
    root = sb.base_url("https://sb.example.com/design/index.html?path=/story/button--primary")
    assert root == "https://sb.example.com/design"
    steps = sb.build_steps(root, "button--primary", visual=True, a11y=True, a11y_impact="critical")
    assert steps[0]["value"] == "https://sb.example.com/design/iframe.html?id=button--primary&viewMode=story"
    assert [s["type"] for s in steps] == ["goto", "wait-for-selector", "expect-visual-match", "check-accessibility"]
    # The component baseline is keyed by the story id (VisualBaseline: case + step id).
    assert steps[2]["id"] == "story-button--primary"
    assert steps[3]["params"] == {"impact": "critical"}
    with pytest.raises(sb.StorybookError):
        sb.build_steps(root, "button--primary", visual=False, a11y=False)
    with pytest.raises(sb.StorybookError):
        sb.base_url("file:///etc/passwd")
    existing = [SimpleNamespace(steps=steps)]
    assert sb.find_existing(existing, "button--primary") is existing[0]
    assert sb.find_existing(existing, "button--secondary") is None
    with pytest.raises(sb.StorybookError):
        sb.pick([{"id": "a--b"}], ["a--b", "c--d"])


# --- catalog -------------------------------------------------------------------------

def test_new_steps_are_in_the_catalog():
    from app.api.agent_reference import _STEP_TYPES
    types = {s["type"] for s in _STEP_TYPES}
    assert {"expect-no-horizontal-overflow", "expect-rtl-layout", "check-page-budget"} <= types


def test_matrix_axes_inherit_down_the_suite_chain():
    from app.services.test_service import _merge_settings
    merged = _merge_settings({"viewports": ["mobile"], "color_schemes": ["dark"]},
                             {"viewports": ["wide"]})
    assert merged["viewports"] == ["wide"] and merged["color_schemes"] == ["dark"]
