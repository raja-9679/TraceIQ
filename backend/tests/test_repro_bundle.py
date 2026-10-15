"""Reproduce-locally bundle: scrubbing, data rows, spec generation (WP11)."""
import io
import json
import zipfile

from app.services import repro_bundle as rb


def test_settings_credentials_become_secret_placeholders():
    found = {}
    out = rb.scrub_settings({
        "headers": {"Authorization": "Bearer abc", "X-Trace": "on", "X-Api-Key": "{{secret.API_KEY}}"},
        "params": {"token": "t0k", "lang": "en"},
        "domain_settings": {"api.example.com": {"headers": {"Cookie": "sid=1"}, "params": {}}},
        "storage_state": {"cookies": [{"value": "live"}]},
        "secrets": {"API_KEY": "plaintext"},
        "browsers": ["chromium"],
    }, found)
    assert out["headers"]["Authorization"] == "{{secret.REPRO_HEADER_AUTHORIZATION}}"
    assert out["headers"]["X-Trace"] == "on"
    # Already a template: left alone, not double-wrapped.
    assert out["headers"]["X-Api-Key"] == "{{secret.API_KEY}}"
    assert out["params"]["token"].startswith("{{secret.REPRO_")
    assert out["params"]["lang"] == "en"
    assert out["domain_settings"]["api.example.com"]["headers"]["Cookie"].startswith("{{secret.")
    assert "storage_state" not in out and "secrets" not in out
    assert out["browsers"] == ["chromium"]
    blob = json.dumps(out)
    for leaked in ("Bearer abc", "t0k", "sid=1", "plaintext", "live"):
        assert leaked not in blob


def test_password_fill_and_jwt_values_are_replaced_in_steps():
    found = {}
    # Assembled at runtime: a JWT-shaped literal trips the gitleaks history scan.
    jwt = ".".join(["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiIxMjM0NTY3ODkwIn0", "c2lnbmF0dXJlLXZhbHVl"])
    steps = rb.scrub_steps([
        {"type": "fill", "selector": "#email", "value": "dev@example.com"},
        {"type": "fill", "selector": "input[type=password]", "value": "hunter2"},
        {"type": "fill", "selector": "#pw", "value": "{{secret.PW}}"},
        {"type": "http-request", "value": "https://x/api",
         "params": {"headers": {"Authorization": jwt}, "body": {"password": "p", "q": 1}}},
    ], found)
    assert steps[0]["value"] == "dev@example.com"
    assert steps[1]["value"].startswith("{{secret.REPRO_STEP_2")
    assert steps[2]["value"] == "{{secret.PW}}"
    assert steps[3]["params"]["headers"]["Authorization"].startswith("{{secret.")
    assert steps[3]["params"]["body"]["password"].startswith("{{secret.")
    assert steps[3]["params"]["body"]["q"] == 1
    blob = json.dumps(steps)
    assert "hunter2" not in blob and jwt not in blob
    refs = rb.secret_refs(steps)
    assert "PW" in refs and len(refs) == 4


def test_data_row_is_picked_from_the_result_name():
    rows = [{"q": "a"}, {"q": "b"}]
    assert rb.data_row_for(rows, "search [row 2]") == (1, {"q": "b"})
    assert rb.data_row_for(rows, "search") == (None, None)
    assert rb.data_row_for(rows, "search [row 9]") == (None, None)


def test_only_this_runs_artifact_keys_are_accepted():
    assert rb.run_artifact_key("runs/5/trace.zip", 5) == "runs/5/trace.zip"
    assert rb.run_artifact_key("runs/55/trace.zip", 5) is None
    assert rb.run_artifact_key("runs/5/../6/trace.zip", 5) is None
    assert rb.run_artifact_key("https://minio/runs/5/x.png", 5) is None
    assert rb.run_artifact_key(None, 5) is None


def test_generated_spec_translates_known_steps_and_todos_the_rest():
    spec = rb.generate_spec(
        case_name='Checkout "happy" path', run_id=7, result_id=9,
        steps=[
            {"type": "goto", "value": "/cart"},
            {"type": "fill", "selector": "#pw", "value": "{{secret.PW}}"},
            {"type": "click", "selector": "button:has-text('Pay')"},
            {"type": "expect-text", "selector": ".status", "value": "Paid"},
            {"type": "expect-title", "value": "^Shop", "params": {"operator": "matches"}},
            {"type": "http-request", "value": "https://api/x", "params": {
                "method": "post", "assertions": [{"type": "status", "value": "201"}],
                "extract": [{"path": "data.id", "variable": "orderId"}]}},
            {"type": "carousel-find", "selector": ".c", "value": "*/ evil"},
        ],
        settings={"headers": {"X-A": "1"}, "params": {}}, env_vars={"HOST": "h"},
        base_url="https://shop.example", data_row=None, browser="firefox", device="iPhone 13")
    assert "import { test, expect, devices } from '@playwright/test';" in spec
    assert 'page.goto(gotoUrl(r("/cart"))' in spec
    assert 'fill(String(r("{{secret.PW}}"))' in spec
    assert "toContainText" in spec
    assert 'browserName: "firefox"' in spec
    assert 'const DEVICE: string = "iPhone 13";' in spec
    assert "TODO: expect-title with a regex operator" in spec
    assert 'TODO: step type "carousel-find"' in spec
    # Step JSON in a TODO is a // line comment, and "*/" is broken up too.
    evil = [ln for ln in spec.splitlines() if "* / evil" in ln]
    assert evil and all(ln.lstrip().startswith("//") for ln in evil)
    assert "*/ evil" not in spec
    assert 'vars["orderId"] = jsonPath(json, "data.id");' in spec
    assert "expect(res.status(), url).toBe(Number(\"201\"));" in spec
    assert 'test("Checkout \\"happy\\" path"' in spec


def test_readme_lists_env_vars_and_zip_has_one_root():
    text = rb.readme(case_name="c", run_id=1, result_id=2, secret_keys={"PW", "REPRO_HEADER_X"},
                     placeholders={"REPRO_HEADER_X": "header X"}, artifacts=[], skipped=[], env_name=None)
    assert "export TRACEIQ_SECRET_PW=" in text
    assert "export TRACEIQ_SECRET_REPRO_HEADER_X=" in text
    assert rb.env_var_for_secret("api-key.v2") == "TRACEIQ_SECRET_API_KEY_V2"
    buf = rb.build_zip({"root/a.json": b"{}", "root/artifacts/v.webm": b"x"})
    names = zipfile.ZipFile(io.BytesIO(buf.read())).namelist()
    assert all(n.startswith("root/") for n in names)


def test_credentials_under_any_step_param_key_are_replaced():
    found = {}
    steps = rb.scrub_steps([
        {"type": "oauth2-token", "value": "https://idp/token",
         "params": {"client_id": "app", "client_secret": "shh-literal", "variable": "token"}},
        {"type": "totp", "params": {"secret": "{{secret.TOTP_SEED}}"}},
    ], found)
    assert steps[0]["params"]["client_secret"].startswith("{{secret.REPRO_STEP_1")
    assert steps[0]["params"]["client_id"] == "app" and steps[0]["params"]["variable"] == "token"
    assert steps[1]["params"]["secret"] == "{{secret.TOTP_SEED}}"


# --- generated-source injection (security review) ---------------------------

# Every hostile value carries this marker; after stripping the double-quoted
# JSON string literals from the generated file, the marker must be gone —
# i.e. it only ever appears inside an inert string literal.
_MARK = "PWNMARK"
_HOSTILE = [
    f"`${{process.exit(1)}}`{_MARK}",
    f"*/ require('child_process').execSync('id') /*{_MARK}",
    f"line1\nprocess.exit(1); // {_MARK}",
    f" process.exit(2) {_MARK}",
    f"</script><script>alert(1)</script>{_MARK}",
    f"\"); process.exit(3); (\"{_MARK}",
    f"'); process.exit(4); ('{_MARK}",
    f"\\\"); process.exit(5); //{_MARK}",
    f"/.*/.test(x) && {_MARK}",
]

_STRING_LITERAL_RE = __import__("re").compile(r'"(?:[^"\\\n]|\\.)*"')


def _hostile_spec(h):
    step_types = ["goto", "click", "fill", "select-option", "press-key", "expect-visible", "expect-text",
                  "expect-not-text", "expect-url", "expect-title", "screenshot", "scroll-to",
                  "wait-for-selector", "switch-frame", h]
    steps = [{"type": t, "selector": h, "value": h, "timeout": h,
              "params": {"wait_until": h, "operator": h}} for t in step_types]
    steps.append({"type": "http-request", "value": h, "params": {
        "method": h, "headers": {h: h}, "params": {h: h}, "body": {h: [h]},
        "assertions": [{"type": "status", "value": h},
                       {"type": "json-path", "operator": "equals", "path": h, "value": h},
                       {"type": h, "value": h}],
        "expect_schema": {h: h}, "extract": [{"path": h, "variable": h}]}})
    steps.append(h)  # malformed step
    return rb.generate_spec(case_name=h, run_id=1, result_id=2, steps=steps,
                            settings={"headers": {h: h}, "params": {h: h}}, env_vars={h: h},
                            base_url=h, data_row={h: h}, browser=h, device=h)


def test_generated_spec_keeps_hostile_case_data_inside_string_literals():
    benign = _hostile_spec("x")
    for h in _HOSTILE:
        spec = _hostile_spec(h)
        # No value can add a line (newline / U+2028 breaking out of a comment).
        assert spec.count("\n") == benign.count("\n"), h
        assert " " not in spec and " " not in spec
        stripped = _STRING_LITERAL_RE.sub('""', spec)
        assert _MARK not in stripped, (h, [ln for ln in stripped.splitlines() if _MARK in ln])
        # Comments generated from case data never contain a block-comment end
        # or any raw non-ASCII.
        for ln in spec.splitlines():
            if ln.lstrip().startswith("//"):
                assert "*/" not in ln and all(32 <= ord(c) < 127 for c in ln), ln
        assert "</script" not in spec


def test_generated_spec_structure_does_not_depend_on_values():
    """Blank every string literal: the remaining code must be identical for a
    benign and a hostile value — data can only ever change literal contents."""
    blank = lambda s: _STRING_LITERAL_RE.sub('""', s)  # noqa: E731
    benign = blank(_hostile_spec("x"))
    for h in _HOSTILE:
        hostile = blank(_hostile_spec(h))
        # Comments carry escaped data; compare code lines only.
        code = lambda s: [ln for ln in s.splitlines() if not ln.lstrip().startswith("//")]  # noqa: E731
        assert code(hostile) == code(benign), h


def test_comment_helper_neutralises_terminators():
    assert rb._comment("a\nb c*/d") == "a?b?c* /d"
