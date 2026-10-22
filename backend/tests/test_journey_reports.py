"""WP12c reporting logic: path normalisation + rules, import parsing, coverage
weighting, flag aggregation, benchmark matrix, journey health."""
import json
import time
from datetime import datetime, timedelta

import pytest

from app.services import benchmarks as bm
from app.services import business_journeys as bj
from app.services import flag_coverage as fc
from app.services import journey_coverage as jc


# --- normalisation ---------------------------------------------------------

@pytest.mark.parametrize("raw,want", [
    ("https://shop.example.com/Cart/?a=1#top", "/Cart"),
    ("/checkout/", "/checkout"),
    ("checkout", "/checkout"),
    ("//cdn.example.com/x/y", "/x/y"),
    ("https://example.com", "/"),
    ("/", "/"),
    ("/a//b///c/", "/a/b/c"),
    ("{{env.BASE_URL}}/login?next=/x", "/login"),
    ("{{base}}{{prefix}}/p/1", "/p/1"),
    ("  /spaces  ", "/spaces"),
])
def test_normalize_path(raw, want):
    assert jc.normalize_path(raw) == want


def test_normalize_path_empty_and_none():
    assert jc.normalize_path(None) is None
    assert jc.normalize_path("   ") is None


# --- rules -------------------------------------------------------------------

def _rules(*rules):
    return jc.compile_rules(jc.validate_rules(list(rules)))


def test_template_and_glob_rules():
    rules = _rules({"pattern": "/product/{id}"}, {"pattern": "/blog/**", "name": "Blog"},
                   {"pattern": "/item-*/reviews"})
    assert jc.canonical_key("/product/123", rules) == "/product/{id}"
    assert jc.canonical_key("/product/123/extra", rules) == "/product/123/extra"
    assert jc.canonical_key("/blog", rules) == "Blog"
    assert jc.canonical_key("/blog/2026/10/post", rules) == "Blog"
    assert jc.canonical_key("/item-42/reviews", rules) == "/item-*/reviews"
    assert jc.canonical_key("/other", rules) == "/other"


def test_first_matching_rule_wins():
    rules = _rules({"pattern": "/p/special"}, {"pattern": "/p/{id}"})
    assert jc.canonical_key("/p/special", rules) == "/p/special"
    assert jc.canonical_key("/p/7", rules) == "/p/{id}"


def test_regex_rule_uses_re2():
    pytest.importorskip("re2")
    rules = _rules({"pattern": r"^/order/\d+$", "kind": "regex", "name": "/order/{n}"})
    assert jc.canonical_key("/order/991", rules) == "/order/{n}"
    assert jc.canonical_key("/order/abc", rules) == "/order/abc"


def test_hostile_regex_is_linear_or_rejected():
    pytest.importorskip("re2")
    rules = _rules({"pattern": r"^(a+)+$", "kind": "regex"})
    t = time.monotonic()
    jc.canonical_key("/" + "a" * 50000 + "!", rules)
    assert time.monotonic() - t < 1.0
    for bad in (r"(?=a)a", r"(a)\1"):
        with pytest.raises(jc.JourneyImportError):
            jc.validate_rules([{"pattern": bad, "kind": "regex"}])


def test_hostile_wildcards_stay_fast():
    rules = _rules({"pattern": "/" + "*a" * 60 + "*b"}, {"pattern": "/" + "/".join(["**"] * 25) + "/z"})
    t = time.monotonic()
    assert jc.canonical_key("/" + "a" * 2000, rules) == "/" + "a" * 2000
    assert jc.canonical_key("/" + "/".join(["x"] * 300), rules) == "/" + "/".join(["x"] * 300)
    assert time.monotonic() - t < 1.0


@pytest.mark.parametrize("rules,msg", [
    ([{"pattern": ""}], "empty"),
    ([{"pattern": "nope"}], "starts with"),
    ([{"pattern": "/x", "kind": "python"}], "kind"),
    ([{"pattern": "/" + "a" * 400}], "longer"),
    ([{"pattern": "/" + "/".join(["a"] * 40)}], "segments"),
    ([{"pattern": "/a"}] * 101, "at most"),
    ("not a list", "list"),
])
def test_rule_validation(rules, msg):
    with pytest.raises(jc.JourneyImportError, match=msg):
        jc.validate_rules(rules)


# --- parsing -----------------------------------------------------------------

def test_csv_with_aliases_dates_and_journeys():
    data = (b"\xef\xbb\xbfPage Path,Pageviews,Funnel,Step,Date\n"
            b"/cart?x=1,\"1,200\",Checkout,1,2026-10-01\n"
            b"/cart/,300,,,2026-10-05\n"
            b"/pay,abc,Checkout,2,\n"
            b",5,,,\n")
    p = jc.parse_import(data, filename="ga.csv")
    assert p.source_format == "csv"
    assert p.row_count == 4 and p.skipped_rows == 2
    assert p.period_start == "2026-10-01" and p.period_end == "2026-10-05"
    rows = {(r["path"], r.get("journey"), r.get("step")): r["views"] for r in p.rows}
    assert rows[("/cart", "Checkout", 1)] == 1200
    assert rows[("/cart", None, None)] == 300
    # page rows exist, so the funnel step does not double-count the total
    assert p.total_views == 300


def test_ga4_report_export():
    data = (b"# ----------------------------------------\n"
            b"# Pages and screens: Page path and screen class\n"
            b"# Account: Demo, Inc\n"
            b"# ----------------------------------------\n"
            b"#\n"
            b"# Start date: 20261001\n"
            b"# End date: 20261021\n"
            b"Page path and screen class,Views,Users,Event count\n"
            b"/cart,\"1,200\",300,9000\n"
            b"/,400,250,1000\n")
    p = jc.parse_import(data, filename="pages.csv")
    assert {r["path"]: r["views"] for r in p.rows} == {"/cart": 1200, "/": 400}
    assert not any("no views column" in w for w in p.warnings)


def test_ga4_exploration_headers():
    p = jc.parse_import(b"Page path + query string,Screen page views\n/a?x=1,5\n/a,2\n")
    assert {r["path"]: r["views"] for r in p.rows} == {"/a": 7}


def test_semicolon_csv_without_views_counts_rows():
    p = jc.parse_import(b"url;other\n/a;1\n/a;2\n/b;3\n")
    assert {r["path"]: r["views"] for r in p.rows} == {"/a": 2, "/b": 1}
    assert any("no views column" in w for w in p.warnings)


def test_json_list_and_object_forms():
    p = jc.parse_import(json.dumps([{"url": "https://x.io/a", "views": 3}]).encode())
    assert p.source_format == "json" and p.rows == [{"path": "/a", "views": 3}]
    p = jc.parse_import(json.dumps({"period_start": "2026-09-01", "rows": [
        {"path": "/b", "views": 2.0, "journey": "Signup", "step": 1, "nested": {"x": 1}}]}).encode())
    assert p.period_start == "2026-09-01"
    assert p.rows == [{"path": "/b", "views": 2, "journey": "Signup", "step": 1}]


@pytest.mark.parametrize("data,msg", [
    (b"", "empty"),
    (b"views\n1\n", "no path column"),
    (b"\xff\xfe\x00bad", "UTF-8"),
    (b"[" * 100000 + b"]" * 100000, "nested|JSON"),
    (b'{"rows": 5}', "list of rows"),
    (b"path,views\n/a,-5\n", "no usable rows"),
])
def test_parse_errors(data, msg):
    with pytest.raises(jc.JourneyImportError, match=msg):
        jc.parse_import(data)


def test_size_and_row_caps(monkeypatch):
    with pytest.raises(jc.JourneyImportError, match="larger than"):
        jc.parse_import(b"path,views\n" + b"/a,1\n" * (jc.MAX_IMPORT_BYTES // 5 + 10))
    monkeypatch.setattr(jc, "MAX_IMPORT_ROWS", 10)
    with pytest.raises(jc.JourneyImportError, match="more than 10"):
        jc.parse_import(b"path,views\n" + b"/a,1\n" * 11)
    with pytest.raises(jc.JourneyImportError, match="more than 10"):
        jc.parse_import(json.dumps([{"path": "/a", "views": 1}] * 11).encode())


def test_explicit_format_must_be_known():
    with pytest.raises(jc.JourneyImportError, match="format"):
        jc.parse_import(b"path\n/a\n", fmt="xml")


# --- coverage ----------------------------------------------------------------

def test_coverage_is_traffic_weighted_and_uses_rules():
    rows = [{"path": "/", "views": 600}, {"path": "/product/1", "views": 200},
            {"path": "/product/2", "views": 100}, {"path": "/about", "views": 99},
            {"path": "/legal", "views": 1},
            {"path": "/cart", "views": 50, "journey": "Checkout", "step": 1},
            {"path": "/pay", "views": 40, "journey": "Checkout", "step": 2}]
    rules = _rules({"pattern": "/product/{id}"})
    covered = jc.covered_keys([(1, [{"type": "goto", "value": "{{env.BASE}}/product/77"}]),
                               (2, [{"type": "goto", "value": "https://shop.io/legal?x"},
                                    {"type": "click", "selector": "a"}]),
                               (3, [{"type": "goto", "value": "/cart"}]),
                               (4, [{"type": "goto", "value": "/never-visited"}])], rules)
    rep = jc.compute_coverage(rows, covered, rules)
    assert rep["total_views"] == 1000
    assert rep["covered_views"] == 301          # /product/{id} 300 + /legal 1
    assert rep["coverage_pct"] == 30.1
    assert [e["key"] for e in rep["top_uncovered"]] == ["/", "/about"]
    low = {e["key"]: e["views"] for e in rep["covered_low_traffic"]}
    assert low == {"/legal": 1, "/cart": 0, "/never-visited": 0}
    prod = next(e for e in rep["covered"] if e["key"] == "/product/{id}")
    assert prod["paths"] == 2 and prod["case_ids"] == [1]
    (j,) = rep["journeys"]
    assert j["journey"] == "Checkout" and [s["key"] for s in j["steps"]] == ["/cart", "/pay"]
    assert j["coverage_pct"] == 50.0 and j["weighted_coverage_pct"] == round(100 * 50 / 90, 1)
    assert j["first_gap"] == "/pay"


def test_coverage_of_empty_traffic():
    rep = jc.compute_coverage([{"path": "/a", "views": 0}], {}, [])
    assert rep["coverage_pct"] is None and rep["total_views"] == 0


# --- flags -------------------------------------------------------------------

def test_validate_flags():
    assert fc.validate_flags(None) is None
    assert fc.validate_flags({}) is None
    assert fc.validate_flags({"checkout_v2": True, "exp.price": " B ", "n": 3}) == {
        "checkout_v2": True, "exp.price": "B", "n": "3"}
    for bad in ({"bad name": "x"}, {"a": {"x": 1}}, {"a": "x" * 300}, ["a"],
                {f"f{i}": "x" for i in range(51)}):
        with pytest.raises(fc.FlagError):
            fc.validate_flags(bad)


def test_flag_aggregation_and_never_tested():
    now = datetime(2026, 10, 16)
    runs = [
        {"id": 1, "flags": {"checkout_v2": True, "exp": "A"}, "status": "passed",
         "created_at": now - timedelta(days=2), "case_ids": {10, 11}},
        {"id": 2, "flags": {"checkout_v2": True}, "status": "failed",
         "created_at": now - timedelta(days=1), "case_ids": {11, 12}},
        {"id": 3, "flags": {"checkout_v2": False, "beta": "on"}, "status": "passed",
         "created_at": now, "case_ids": set()},
        {"id": 4, "flags": None, "status": "passed", "created_at": now, "case_ids": {1}},
    ]
    catalog = [{"id": 7, "name": "checkout_v2", "values": ["true", "false"], "description": None},
               {"id": 8, "name": "exp", "values": ["A", "B", "C"], "description": "pricing"},
               {"id": 9, "name": "dark_launch", "values": ["on"], "description": None}]
    rep = fc.aggregate(runs, catalog)
    assert rep["runs_with_flags"] == 3
    by = {f["name"]: f for f in rep["flags"]}
    v2 = {v["value"]: v for v in by["checkout_v2"]["values"]}
    assert v2["true"]["runs"] == 2 and v2["true"]["cases"] == 3
    assert v2["true"]["passed_runs"] == 1 and v2["true"]["failed_runs"] == 1
    assert v2["true"]["last_run_id"] == 2 and v2["true"]["in_catalog"]
    assert by["checkout_v2"]["never_tested"] == []
    assert by["exp"]["never_tested"] == ["B", "C"]
    assert by["dark_launch"]["never_tested"] == ["on"] and by["dark_launch"]["runs"] == 0
    assert rep["uncatalogued"] == ["beta"]
    assert rep["never_tested_total"] == 3


# --- benchmarks --------------------------------------------------------------

def test_clean_targets():
    t = bm.clean_targets(["https://A.example.com/", {"label": "B", "base_url": "http://b.io/shop/"}])
    assert t == [{"label": "a.example.com", "base_url": "https://a.example.com"},
                 {"label": "B", "base_url": "http://b.io/shop"}]
    for bad in (["https://a.io"], ["https://a.io"] * 2, ["ftp://a.io", "https://b.io"],
                ["https://u:p@a.io", "https://b.io"], ["https://a.io"] * 11, "x"):
        with pytest.raises(bm.BenchmarkError):
            bm.clean_targets(bad)


def test_host_allowed_matches_egress_semantics():
    assert bm.host_allowed("shop.example.com", ["example.com"])
    assert bm.host_allowed("example.com", ["example.com"])
    assert bm.host_allowed("a.b.io", ["*.b.io"])
    assert not bm.host_allowed("evilexample.com", ["example.com"])
    assert not bm.host_allowed("b.io", ["*.b.io"])


async def test_target_outside_network_policy_is_refused(monkeypatch):
    async def ok(url, **kw):
        return url
    monkeypatch.setattr("app.core.net_guard.validate_outbound_url", ok)
    policy = {"enforce": True, "allowed_hosts": ["mine.io"], "allow_private": False}
    await bm.check_target_reachable_policy("https://www.mine.io", policy)
    with pytest.raises(bm.BenchmarkError, match="allowlist"):
        await bm.check_target_reachable_policy("https://rival.io", policy)
    await bm.check_target_reachable_policy("https://rival.io", None)


def test_build_matrix():
    targets = [{"label": "Us", "base_url": "https://us.io"},
               {"label": "Them", "base_url": "https://them.io"},
               {"label": "Gone", "base_url": "https://gone.io"}]
    runs = [{"id": 1, "target_url": "https://us.io", "status": "passed", "duration_ms": 900},
            {"id": 2, "target_url": "https://them.io", "status": "failed", "duration_ms": None}]
    results = [
        {"test_run_id": 1, "test_name": "home", "test_case_id": 5, "status": "passed", "duration_ms": 400},
        {"test_run_id": 1, "test_name": "search", "test_case_id": 6, "status": "passed", "duration_ms": 500},
        {"test_run_id": 2, "test_name": "home", "test_case_id": 5, "status": "passed", "duration_ms": 700},
        {"test_run_id": 2, "test_name": "search", "test_case_id": 6, "status": "failed", "duration_ms": 900},
        {"test_run_id": 2, "test_name": "search", "test_case_id": 6, "status": "passed", "duration_ms": 1},
    ]
    samples = [{"test_run_id": 1, "metrics": {"lcp_ms": 1000, "cls": 0.1}},
               {"test_run_id": 1, "metrics": {"lcp_ms": 2000, "cls": True}},
               {"test_run_id": 2, "metrics": {"lcp_ms": 3000}}]
    m = bm.build_matrix(targets, runs, results, samples)
    us, them, gone = m["sites"]
    assert us["pass_rate"] == 100.0 and us["duration_ms"] == 900 and us["avg_case_ms"] == 450.0
    assert us["budgets"] == {"lcp_ms": 1500.0, "cls": 0.1}
    assert them["passed"] == 2 and them["failed"] == 1 and them["duration_ms"] == 1601.0
    assert gone["status"] == "missing" and gone["run_id"] is None
    rows = {r["test_name"]: r for r in m["tests"]}
    assert rows["search"]["cells"][1]["status"] == "failed"   # worst of the repeats
    assert rows["search"]["cells"][2] is None and rows["home"]["differs"]
    assert m["summary"]["best_pass_rate"] == "Us" and m["summary"]["fastest"] == "Us"
    assert not m["summary"]["complete"]
    assert set(m["metrics"]) == {"lcp_ms", "cls"}


def test_absolute_goto_cases():
    class C:
        def __init__(self, i, steps):
            self.id, self.name, self.steps = i, f"c{i}", steps
    out = bm.absolute_goto_cases([C(1, [{"type": "goto", "value": "/rel"}]),
                                  C(2, [{"type": "goto", "value": "https://abs.io/x"}])])
    assert out == [{"id": 2, "name": "c2"}]


# --- business journeys -------------------------------------------------------

def test_case_state_and_rollup():
    t = datetime(2026, 10, 16, 12)
    h = lambda *st: [{"status": s, "created_at": t - timedelta(hours=i), "run_id": i}
                     for i, s in enumerate(st)]
    assert bj.case_state([], False)["state"] == "not_run"
    assert bj.case_state(h("failed", "passed"), False)["state"] == "failing"
    assert bj.case_state(h("passed", "failed"), False)["state"] == "flaky"
    assert bj.case_state(h("passed", "passed"), True)["state"] == "flaky"
    assert bj.case_state(h("passed"), False)["state"] == "passing"
    assert bj.rollup(["passing", "not_run"]) == "passing"
    assert bj.rollup(["passing", "flaky"]) == "flaky"
    assert bj.rollup(["flaky", "failing"]) == "failing"
    assert bj.rollup([]) == "not_run"


def test_journey_health_trend():
    now = datetime(2026, 10, 16, 12)
    hist = {1: [{"status": "passed", "created_at": now, "run_id": 9},
                {"status": "failed", "created_at": now - timedelta(days=1), "run_id": 8}],
            2: [{"status": "passed", "created_at": now, "run_id": 9}]}
    out = bj.journey_health([1, 2, 3], {1: "a", 2: "b", 3: "c"}, hist, [], now)
    assert out["health"] == "flaky"
    assert out["counts"] == {"passing": 1, "failing": 0, "flaky": 1, "not_run": 1}
    assert out["trend"][-1] == {"date": "2026-10-16", "passed": 2, "total": 2, "pass_rate": 100.0}
    assert out["trend"][-2]["pass_rate"] == 0.0 and len(out["trend"]) == bj.TREND_DAYS
