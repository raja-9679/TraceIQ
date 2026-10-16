"""W5 heal candidates: deterministic strategies, scoring/ranking and the
server-side matcher the accept recheck relies on. Mirrors
execution-engine/src/core/heal-candidates.test.ts for the shared parts."""
import pytest

from app.services import heal_candidates as hc

PAGE = """<html><body>
  <form id="login-form" class="auth card">
    <label for="email">Email address</label>
    <input id="email" name="email" type="email" placeholder="you@example.com">
    <input id="pw" name="password" type="password">
    <button data-testid="login-submit" class="btn btn-primary" type="submit">Sign in</button>
    <a href="/forgot">Forgot password?</a>
  </form>
  <div class="footer"><span>Sign in</span><span>Help</span></div>
  <ul class="items"><li>One</li><li class="x">Two</li><li>Three</li></ul>
  <button hidden>Ghost</button>
  <h2>Account</h2>
</body></html>"""


def _el(html, pred):
    root = hc.parse_html(html)
    for e in hc._elements(root):
        if pred(e):
            return root, e
    raise AssertionError("not found")


def test_tokens_and_similarity():
    assert hc.tokens("#loginSubmit-btn") == ["login", "submit", "btn"]
    assert hc.tokens("div > span.nth-child") == []
    assert hc.similarity(["submit"], ["submit"]) == 1
    assert hc.similarity(["submit"], ["submitting"]) == 0.8
    assert hc.similarity(["submit", "login"], ["login"]) == 0.5
    assert hc.similarity([], ["x"]) == 0


def test_test_id_strategy():
    _, e = _el('<button data-testid="save" data-qa="save-qa">Save</button>', lambda e: e.tag == "button")
    assert hc.test_id_strategy(e) == ['[data-testid="save"]', '[data-qa="save-qa"]']


def test_id_strategy_forms_and_generated_ids():
    assert hc.id_strategy(_el('<i id="save"></i>', lambda e: e.tag == "i")[1]) == [("#save", False)]
    assert hc.id_strategy(_el('<i id="a b"></i>', lambda e: e.tag == "i")[1]) == [('[id="a b"]', False)]
    assert hc.id_strategy(_el('<i id="ember1234"></i>', lambda e: e.tag == "i")[1])[0][1] is True
    assert hc.looks_generated(":r1:") and hc.looks_generated("x-9f8e7d6c5b") and not hc.looks_generated("email")


def test_role_strategy_uses_implicit_role_and_accessible_name():
    root, button = _el(PAGE, lambda e: e.tag == "button")
    assert hc.role_strategy(button, root) == ['role=button[name="Sign in"]']
    root, email = _el(PAGE, lambda e: e.get("id") == "email")
    assert hc.accessible_name(email, root) == "Email address"
    assert hc.role_strategy(email, root) == ['role=textbox[name="Email address"]']
    root, div = _el("<div>Hi</div>", lambda e: e.tag == "div")
    assert hc.role_strategy(div, root) == []


def test_label_strategy():
    _, e = _el('<input aria-label="Search" placeholder="Find…" name="q">', lambda e: e.tag == "input")
    assert hc.label_strategy(e) == ['[aria-label="Search"]', '[placeholder="Find…"]', 'input[name="q"]']


def test_text_strategy_innermost_short_unredacted():
    assert hc.text_strategy(_el("<span>Sign in</span>", lambda e: e.tag == "span")[1]) == ['text="Sign in"']
    assert hc.text_strategy(_el("<p><b>Sign in</b></p>", lambda e: e.tag == "p")[1]) == []
    assert hc.text_strategy(_el(f"<p>{'x' * 60}</p>", lambda e: e.tag == "p")[1]) == []
    assert hc.text_strategy(_el("<p>[REDACTED:pan]</p>", lambda e: e.tag == "p")[1]) == []


def test_css_path_stops_at_stable_id_without_nth_child():
    _, e = _el(PAGE, lambda e: e.tag == "button")
    [path] = hc.css_path_strategy(e)
    assert path == 'form#login-form > button.btn.btn-primary[type="submit"]'
    assert "nth" not in path


def test_quote_escaping_round_trips_through_the_matcher():
    html = """<button aria-label='Say "hi"'>x</button><button aria-label="other">y</button>"""
    root, e = _el(html, lambda e: e.tag == "button")
    [sel] = hc.label_strategy(e)
    assert sel == '[aria-label="Say \\"hi\\""]'
    assert hc.match_selector(root, sel) == [e]


def test_classify_selector():
    assert hc.classify_selector('[data-testid="x"]') == hc.STABILITY["test-id"]
    assert hc.classify_selector("#save") == hc.STABILITY["id"]
    assert hc.classify_selector('role=button[name="Go"]') == hc.STABILITY["role"]
    assert hc.classify_selector('button:has-text("Go")') == hc.STABILITY["text"]
    assert hc.classify_selector("div > div > span") == 0.4


def test_score_is_weighted_sum():
    b = {"stability": 1, "similarity": 0.5, "llm": 0.6, "uniqueness": 1}
    assert hc.score_candidate(b) == round(0.35 + 0.175 + 0.09 + 0.15, 3)


def test_rank_candidates_unique_first_with_evidence():
    ranked = hc.rank_candidates(PAGE, "#login-btn", "submit the login form",
                                llm_selector="button.btn", llm_confidence=0.9)
    assert ranked[0]["selector"] == '[data-testid="login-submit"]'
    assert ranked[0]["applicable"] and ranked[0]["matches"] == 1
    text = next(c for c in ranked if c["selector"] == 'text="Sign in"')
    assert text["matches"] == 2 and not text["applicable"]
    llm = next(c for c in ranked if c["selector"] == "button.btn")
    assert llm["source"] == "llm" and llm["breakdown"]["llm"] == 0.9
    # The LLM pointed at the same button, so the test-id candidate agrees with it.
    assert ranked[0]["breakdown"]["llm"] == 0.9
    first_na = next(i for i, c in enumerate(ranked) if not c["applicable"])
    assert all(not c["applicable"] for c in ranked[first_na:])
    assert {"test-id", "role", "text", "css-path"} <= {c["strategy"] for c in ranked}


def test_rank_without_llm_is_deterministic_only():
    ranked = hc.rank_candidates(PAGE, "#login-btn", "submit")
    assert ranked and all(c["source"] == "deterministic" for c in ranked)
    assert hc.choose_candidate(ranked)["selector"] == '[data-testid="login-submit"]'


def test_sanitize_candidates_drops_junk_and_recomputes_applicable():
    out = hc.sanitize_candidates([
        {"selector": "#a", "strategy": "id", "matches": 1, "score": 5, "breakdown": {"stability": 2}},
        {"selector": "", "matches": 1},
        "junk",
        {"selector": "#b", "strategy": "evil", "source": "x", "matches": "3", "applicable": True},
    ])
    assert [c["selector"] for c in out] == ["#a", "#b"]
    assert out[0]["score"] == 1.0 and out[0]["breakdown"]["stability"] == 1.0
    assert out[1]["applicable"] is False and out[1]["strategy"] == "llm"


# --- matcher -----------------------------------------------------------------

@pytest.mark.parametrize("selector,count", [
    ('[data-testid="login-submit"]', 1),
    ("#email", 1),
    ('[id="email"]', 1),
    ("button", 2),
    ("form > button", 1),
    ("form button.btn-primary", 1),
    ("body > button", 1),
    ("label + input", 1),
    ("label ~ input", 2),
    ('input[type="email" i]', 1),
    ('input[name^="pass"]', 1),
    ('[class~="btn"]', 1),
    ("li:nth-child(2)", 1),
    ("li:nth-child(odd)", 2),
    ("li:first-child, li:last-child", 2),
    ("li:not(.x)", 2),
    ('li:has-text("thr")', 1),
    ('form:has(button)', 1),
    ('button:has-text("sign")', 1),
    ('text="Sign in"', 2),
    ("text=sign", 2),
    ('text="Sign"', 0),
    ('role=button[name="Sign in"]', 1),
    ('role=button[name="Ghost"]', 0),
    ('role=button[name="Ghost"][include-hidden]', 1),
    ('role=button[name="sign"i]', 1),
    ('internal:role=textbox[name="Email address"]', 1),
    ("role=heading[level=2]", 1),
    ("role=link", 1),
    ("//button", 2),
    ("xpath=//li[2]", 1),
    ("id=email", 1),
    ("data-testid=login-submit", 1),
    ("form >> button", 1),
    ('.footer >> text="Sign in"', 1),
    ("css=ul.items li", 3),
])
def test_matcher_counts(selector, count):
    assert len(hc.match_selector(hc.parse_html(PAGE), selector)) == count


@pytest.mark.parametrize("selector", [
    "text=/sign/i", "li::before", "li:hover", "role=button[pressed]", "", "a[", "nth=0",
])
def test_matcher_rejects_unsupported_forms(selector):
    with pytest.raises(hc.UnsupportedSelector):
        hc.match_selector(hc.parse_html(PAGE), selector)


def test_recheck_statuses():
    assert hc.recheck(PAGE, '[data-testid="login-submit"]')["status"] == "unique"
    out = hc.recheck(PAGE, "button")
    assert out["status"] == "not_unique" and out["matches"] == 2
    assert hc.recheck(PAGE, "#nope")["status"] == "no_match"
    assert hc.recheck(PAGE, "li:hover")["status"] == "unsupported"
    assert hc.recheck(None, "#x")["status"] == "unavailable"


def test_snapshot_key_stays_under_the_run_prefix():
    key = hc.snapshot_key(9, 3, "../../etc/passwd", "<p>x</p>")
    assert key.startswith("runs/9/heal/3-")
    assert ".." not in key and "/etc" not in key
