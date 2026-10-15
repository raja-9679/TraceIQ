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
    assert "TODO: step type 'carousel-find'" in spec
    # Step JSON in a TODO is a // line comment, so "*/" can't end a block.
    evil = [ln for ln in spec.splitlines() if "*/ evil" in ln]
    assert evil and all(ln.lstrip().startswith("//") for ln in evil)
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
