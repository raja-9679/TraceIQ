"""Reproduce-locally bundle for one test result (WP11 item 1).

`GET /api/runs/{run_id}/results/{result_id}/bundle` zips what a developer
needs to replay a failing case on their own machine: the steps, the resolved
suite settings, the data row, run metadata, the captured artifacts and
per-step evidence, plus a standalone `repro.spec.ts` for `npx playwright test`.

The one hard rule is that a bundle never carries a credential. It is a file
that leaves the platform — it gets attached to tickets and pasted into chats —
so this module never decrypts `ProjectSecret` rows at all. Values that are
already `{{secret.X}}` templates stay templates; values that LOOK like a
credential (a sensitive header/param/field name, or a value the redaction
patterns flag) are swapped for a `{{secret.REPRO_...}}` placeholder, and the
README tells the developer which `TRACEIQ_SECRET_*` env vars to export. The
generated spec resolves those at run time from the environment.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from app.services.redaction import (
    DEFAULT_HEADER_NAMES, _looks_like_secret_field, _normalize_key,
    _NORMALIZED_BODY_KEYS, redact_text,
)

_SECRET_REF_RE = re.compile(r"\{\{\s*secret\.([\w-]+)\s*\}\}")
_ROW_SUFFIX_RE = re.compile(r" \[row (\d+)\]$")
_HEADER_NAMES = frozenset(h.lower() for h in DEFAULT_HEADER_NAMES)

# Per-artifact and total download ceilings. A trace can be large; a bundle
# that times out is worse than one that says "fetch the video separately".
MAX_ARTIFACT_BYTES = 50 * 1024 * 1024
MAX_TOTAL_ARTIFACT_BYTES = 200 * 1024 * 1024


def env_var_for_secret(key: str) -> str:
    """`{{secret.api-key}}` → `TRACEIQ_SECRET_API_KEY`. Mirrored in the
    generated spec (`secretEnvName`) — keep the two identical."""
    return "TRACEIQ_SECRET_" + re.sub(r"[^A-Z0-9]", "_", key.upper())


def _placeholder_key(*parts: str) -> str:
    raw = "_".join(p for p in parts if p)
    return "REPRO_" + (re.sub(r"[^A-Z0-9]+", "_", raw.upper()).strip("_") or "VALUE")


def _is_template(value: str) -> bool:
    return "{{" in value


def _sensitive_name(name: str) -> bool:
    return name.lower() in _HEADER_NAMES or _normalize_key(name) in _NORMALIZED_BODY_KEYS


def _scrub_value(value: Any, name: str, scope: str, found: Dict[str, str]) -> Any:
    """Replace one credential-looking value with a secret placeholder.

    `found` collects placeholder key → where it came from, for the README."""
    if isinstance(value, (dict, list)):
        return scrub_mapping(value, scope, found)
    if not isinstance(value, str) or not value or _is_template(value):
        return value
    if _sensitive_name(name) or redact_text(value) != value:
        key = _placeholder_key(scope, name)
        found[key] = f"{scope} {name}".strip()
        return "{{secret.%s}}" % key
    return value


def scrub_mapping(obj: Any, scope: str, found: Dict[str, str]) -> Any:
    if isinstance(obj, dict):
        return {k: _scrub_value(v, str(k), scope, found) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_value(v, "", scope, found) if not isinstance(v, (dict, list))
                else scrub_mapping(v, scope, found) for v in obj]
    return obj


def scrub_settings(settings: Dict[str, Any], found: Dict[str, str]) -> Dict[str, Any]:
    """The subset of effective settings a local replay needs, scrubbed.

    Execution-matrix flags and allowed_domains ride along unchanged; they
    carry no credentials. `storage_state`/`secrets` are never present here
    (they are added at dispatch, not by settings inheritance) but are dropped
    defensively in case that changes."""
    out: Dict[str, Any] = {}
    for key, value in (settings or {}).items():
        if key in ("storage_state", "secrets", "environment", "mobile_app", "data_policy"):
            continue
        if key in ("headers", "params"):
            out[key] = scrub_mapping(value or {}, key[:-1], found)
        elif key == "domain_settings" and isinstance(value, dict):
            out[key] = {
                domain: {
                    "headers": scrub_mapping((ds or {}).get("headers") or {}, f"{domain} header", found),
                    "params": scrub_mapping((ds or {}).get("params") or {}, f"{domain} param", found),
                }
                for domain, ds in value.items()
            }
        else:
            out[key] = value
    return out


def scrub_steps(steps: List[Any], found: Dict[str, str]) -> List[Any]:
    """Steps verbatim, except literal credentials (a password typed into a
    password field, an Authorization header on an http-request step)."""
    out = []
    for i, step in enumerate(steps or [], start=1):
        if not isinstance(step, dict):
            out.append(step)
            continue
        s = dict(step)
        value = s.get("value")
        if isinstance(value, str) and value and not _is_template(value):
            if _looks_like_secret_field(str(s.get("selector") or "")) or redact_text(value) != value:
                key = _placeholder_key(f"step {i}", str(s.get("type") or ""))
                found[key] = f"step {i} ({s.get('type')}) value"
                s["value"] = "{{secret.%s}}" % key
        if isinstance(s.get("params"), dict):
            # The whole params object, not just headers/body: oauth2-token,
            # totp and friends keep credentials under their own keys.
            s["params"] = scrub_mapping(s["params"], f"step {i}", found)
        out.append(s)
    return out


def secret_refs(*objs: Any) -> Set[str]:
    """Every `{{secret.X}}` key referenced anywhere in `objs`."""
    text = json.dumps(objs, default=str)
    return set(_SECRET_REF_RE.findall(text))


def data_row_for(dataset: Any, test_name: str) -> Tuple[Optional[int], Optional[dict]]:
    """A data-driven result is named "<case> [row N]" (1-based, see
    app.worker._case_payload). Returns (0-based index, row) or (None, None)."""
    m = _ROW_SUFFIX_RE.search(test_name or "")
    if not m or not isinstance(dataset, list):
        return None, None
    rows = [r for r in dataset if isinstance(r, dict)]
    idx = int(m.group(1)) - 1
    if 0 <= idx < len(rows):
        return idx, rows[idx]
    return None, None


def run_artifact_key(value: Any, run_id: int) -> Optional[str]:
    """A stored artifact reference, accepted only as a key under THIS run.

    Result rows can be written by local workers (any API-key holder), so a
    key naming another run's prefix must not be fetched into this bundle."""
    if not isinstance(value, str):
        return None
    prefix = f"runs/{run_id}/"
    if value.startswith(prefix) and ".." not in value and "\\" not in value:
        return value
    return None


# --------------------------------------------------------------------------
# repro.spec.ts generation
# --------------------------------------------------------------------------

def _js(value: Any) -> str:
    """A JS literal. JSON is valid JS; `ensure_ascii` keeps U+2028/2029 (legal
    in JSON, line terminators in older JS) out of the source."""
    return json.dumps(value, ensure_ascii=True)


def _todo(step: dict, why: str) -> List[str]:
    return [f"    // TODO: {why}", f"    // step JSON: {_js(step)}"]


def _loc(sel: str) -> str:
    return f"page.locator(r({_js(sel)})).first()"


def _timeout(step: dict, default: int = 30000) -> int:
    try:
        return int(str(step.get("timeout") or default))
    except ValueError:
        return default


def _http_lines(step: dict) -> List[str]:
    p = step.get("params") or {}
    lines = [
        f"    const method = {_js(str(p.get('method') or 'GET').upper())};",
        f"    const urls = String(r({_js(step.get('value') or step.get('selector') or '')}))"
        ".split(',').map((u) => u.trim()).filter(Boolean);",
        f"    const headers = {{ ...r(SETTINGS.headers || {{}}), ...r({_js(p.get('headers') or {})}) }};",
        f"    const params = {{ ...r(SETTINGS.params || {{}}), ...r({_js(p.get('params') or {})}) }};",
        f"    const data = r({_js(p.get('body'))});",
        "    for (const url of urls) {",
        "      const res = await page.request.fetch(url, { method, headers, params, data, timeout: 30000 });",
        "      const text = await res.text();",
        "      let json: any;",
        "      try { json = JSON.parse(text); } catch { json = undefined; }",
    ]
    for a in p.get("assertions") or []:
        if not isinstance(a, dict):
            continue
        if a.get("type") == "status":
            lines.append(f"      expect(res.status(), url).toBe(Number({_js(a.get('value'))}));")
        elif a.get("type") == "json-path" and a.get("operator") in ("equals", "contains"):
            matcher = "toBe" if a["operator"] == "equals" else "toContain"
            lines.append(
                f"      expect(String(jsonPath(json, {_js(a.get('path') or '')}))).{matcher}(String(r({_js(a.get('value'))})));")
        else:
            lines.append(f"      // TODO: assertion not translated: {_js(a)}")
    if p.get("expect_schema"):
        lines.append(f"      // TODO: expect_schema (response JSON Schema) not translated: {_js(p['expect_schema'])}")
    for ex in p.get("extract") or []:
        if isinstance(ex, dict) and ex.get("path") and ex.get("variable"):
            lines.append(f"      vars[{_js(ex['variable'])}] = jsonPath(json, {_js(ex['path'])});")
    lines.append("    }")
    return lines


def step_lines(step: Any) -> List[str]:
    """TypeScript for one step, indented for a `test.step` body.

    Only step types whose semantics translate one-to-one are emitted as code;
    everything else becomes a commented TODO carrying the step JSON, so the
    file still compiles and the gap is visible where it occurs."""
    if not isinstance(step, dict):
        return [f"    // TODO: malformed step {_js(step)}"]
    t = step.get("type")
    sel = step.get("selector") or step.get("value") or ""
    val = step.get("value")
    to = _timeout(step)
    if t == "goto":
        wait = (step.get("params") or {}).get("wait_until") or "domcontentloaded"
        return [f"    await page.goto(gotoUrl(r({_js(val or step.get('selector') or 'about:blank')})), "
                f"{{ waitUntil: {_js(wait)} }});"]
    if t in ("click", "double-click", "right-click", "check", "uncheck", "hover"):
        call = {"click": "click()", "double-click": "dblclick()", "right-click": "click({ button: 'right' })",
                "check": "check()", "uncheck": "uncheck()", "hover": "hover()"}[t]
        if t == "hover":
            return [f"    await {_loc(sel)}.hover();"]
        return [f"    await {_loc(sel)}.waitFor({{ state: 'visible', timeout: {to} }});",
                f"    await {_loc(sel)}.{call};"]
    if t == "fill":
        return [f"    await {_loc(step.get('selector') or '')}.fill(String(r({_js(val or '')})), {{ timeout: {to} }});"]
    if t == "select-option":
        return [f"    await {_loc(step.get('selector') or '')}.selectOption(String(r({_js(val or '')})));"]
    if t == "press-key":
        return [f"    await page.keyboard.press(String(r({_js(sel)})));"]
    if t == "expect-visible":
        return [f"    await expect({_loc(sel)}).toBeVisible({{ timeout: {to} }});"]
    if t == "wait-for-selector":
        return [f"    await {_loc(sel)}.waitFor({{ state: 'attached', timeout: {to} }});"]
    if t == "expect-hidden":
        return [f"    await expect({_loc(sel)}).toBeHidden({{ timeout: 50000 }});"]
    if t == "expect-text":
        return [f"    await expect({_loc(step.get('selector') or '')}).toContainText(String(r({_js(val or '')})), {{ timeout: 50000 }});"]
    if t == "expect-not-text":
        return [f"    await {_loc(step.get('selector') or '')}.waitFor({{ state: 'attached', timeout: {to} }});",
                f"    expect((await {_loc(step.get('selector') or '')}.textContent()) || '').not.toContain(String(r({_js(val or '')})));"]
    if t == "expect-url":
        return [f"    await page.waitForURL(String(r({_js(sel)})), {{ timeout: 15000 }});"]
    if t == "expect-title":
        op = (step.get("params") or {}).get("operator") or "contains"
        if op == "equals":
            return [f"    expect(await page.title()).toBe(String(r({_js(sel)})));"]
        if op == "contains":
            return [f"    expect(await page.title()).toContain(String(r({_js(sel)})));"]
        # A test author's regex is not compiled into generated code: see the
        # regex rule in info/AGENT_BRIEF_ROADMAP.md.
        return _todo(step, "expect-title with a regex operator is not translated")
    if t == "screenshot":
        name = re.sub(r"[^\w.-]+", "_", str(val or "screenshot"))
        return [f"    await page.screenshot({{ path: {_js('repro-' + name + '.png')}, fullPage: true }});"]
    if t == "scroll-to":
        return [f"    await {_loc(sel)}.scrollIntoViewIfNeeded();"]
    if t == "wait-timeout":
        try:
            ms = int(str(val or step.get("selector") or "1000"))
        except ValueError:
            ms = 1000
        return [f"    await page.waitForTimeout({ms});"]
    if t == "http-request":
        return _http_lines(step)
    if t == "switch-frame":
        return _todo(step, "switch-frame is not translated; later steps run against the main page")
    return _todo(step, f"step type '{t}' has no Playwright translation")


SPEC_PRELUDE = r"""
// Template resolution, mirroring execution-engine/src/core/interpolate.ts:
//   {{secret.X}} -> env TRACEIQ_SECRET_X (required; never shipped in the bundle)
//   {{env.X}}    -> env TRACEIQ_ENV_X, else the run's environment variable
//   {{data.X}}   -> this result's data row
//   {{name}}     -> values captured by earlier http-request `extract`s
const vars: Record<string, any> = {};

function secretEnvName(key: string): string {
  return 'TRACEIQ_SECRET_' + key.toUpperCase().replace(/[^A-Z0-9]/g, '_');
}

function resolveToken(token: string, whole: string): string {
  const dot = token.indexOf('.');
  const ns = dot > 0 ? token.slice(0, dot) : '';
  const key = dot > 0 ? token.slice(dot + 1) : token;
  if (ns === 'secret') {
    const v = process.env[secretEnvName(key)];
    if (v === undefined) throw new Error(`Set ${secretEnvName(key)} (see README.txt) to replay this step`);
    return v;
  }
  if (ns === 'env') {
    const fromEnv = process.env['TRACEIQ_ENV_' + key.toUpperCase().replace(/[^A-Z0-9]/g, '_')];
    if (fromEnv !== undefined) return fromEnv;
    return ENV_VARS[key] !== undefined ? String(ENV_VARS[key]) : whole;
  }
  if (ns === 'data') return DATA_ROW[key] !== undefined ? String(DATA_ROW[key]) : whole;
  if (ns === 'fake') return `repro-${key}-${Date.now()}`;
  if (!ns) return vars[key] !== undefined ? String(vars[key]) : whole;
  return whole; // inbox./webhook. hooks need the TraceIQ server
}

function r(val: any): any {
  if (typeof val === 'string') return val.replace(/\{\{\s*([\w.-]+)\s*\}\}/g, (whole, tok) => resolveToken(tok, whole));
  if (Array.isArray(val)) return val.map(r);
  if (val && typeof val === 'object') {
    const out: Record<string, any> = {};
    for (const k of Object.keys(val)) out[k] = r(val[k]);
    return out;
  }
  return val;
}

function gotoUrl(raw: string): string {
  let url = raw;
  if (url.startsWith('/') && BASE_URL) url = BASE_URL.replace(/\/$/, '') + url;
  const params = r(SETTINGS.params || {});
  if (Object.keys(params).length) {
    try {
      const u = new URL(url);
      for (const [k, v] of Object.entries(params)) u.searchParams.append(k, String(v));
      url = u.toString();
    } catch { /* relative or invalid: navigate as-is */ }
  }
  return url;
}

function jsonPath(obj: any, p: string): any {
  let cur = obj;
  for (const part of p.replace(/^\$\.?/, '').split(/\.|\[(\d+)\]/).filter((s) => s !== undefined && s !== '')) {
    if (cur === null || cur === undefined) return undefined;
    cur = cur[part];
  }
  return cur;
}
"""


def generate_spec(*, case_name: str, run_id: int, result_id: int, steps: List[Any],
                  settings: Dict[str, Any], env_vars: Dict[str, Any], base_url: Optional[str],
                  data_row: Optional[dict], browser: Optional[str], device: Optional[str]) -> str:
    browser = browser if browser in ("chromium", "firefox", "webkit") else "chromium"
    header = [
        f"// Generated by TraceIQ: reproduces run #{run_id}, result #{result_id}.",
        "// Run with:  npx playwright test repro.spec.ts --headed",
        "// Secrets are NOT in this file. Export the TRACEIQ_SECRET_* variables listed in README.txt.",
        "import { test, expect, devices } from '@playwright/test';",
        "",
        f"const SETTINGS: Record<string, any> = {_js({k: settings.get(k) for k in ('headers', 'params') if k in settings})};",
        f"const ENV_VARS: Record<string, any> = {_js(env_vars or {})};",
        f"const BASE_URL: string = {_js(base_url or '')};",
        f"const DATA_ROW: Record<string, any> = {_js(data_row or {})};",
        f"const DEVICE: string = {_js(device or '')};",
    ]
    body = [
        "",
        "// devices[...] carries defaultBrowserType, which test.use rejects alongside browserName.",
        "const { defaultBrowserType: _ignored, ...deviceOptions } = (DEVICE && (devices as any)[DEVICE]) || ({} as any);",
        f"test.use({{ browserName: {_js(browser)}, ...deviceOptions, extraHTTPHeaders: r(SETTINGS.headers || {{}}) }});",
        "",
        f"test({_js(case_name)}, async ({{ page }}) => {{",
        "  test.setTimeout(10 * 60 * 1000);",
    ]
    for i, step in enumerate(steps or [], start=1):
        label = f"{i}. {step.get('type') if isinstance(step, dict) else 'step'}"
        if isinstance(step, dict) and (step.get("selector") or step.get("value")):
            label += f" {str(step.get('selector') or step.get('value'))[:80]}"
        body.append(f"  await test.step({_js(label)}, async () => {{")
        body.extend(step_lines(step))
        body.append("  });")
    body.append("});")
    return "\n".join(header) + "\n" + SPEC_PRELUDE + "\n".join(body) + "\n"


def readme(*, case_name: str, run_id: int, result_id: int, secret_keys: Iterable[str],
           placeholders: Dict[str, str], artifacts: List[dict], skipped: List[dict],
           env_name: Optional[str]) -> str:
    keys = sorted(set(secret_keys))
    lines = [
        f"TraceIQ reproduce-locally bundle: run #{run_id}, result #{result_id}",
        f"Case: {case_name}",
        "",
        "Contents",
        "  case.json          the case's steps exactly as the run used them (credentials replaced)",
        "  settings.json      resolved suite settings (headers/params inherited down the suite tree)",
        "  data_row.json      the dataset row this result ran with (data-driven cases only)",
        "  run.json           run metadata: commit, branch, browser, device, persona name, environment",
        "  result.json        status, error, per-step evidence for this result",
        "  artifacts/         trace / video / screenshots captured by the run (when the project's",
        "                     capture policy recorded them)",
        "  repro.spec.ts      standalone @playwright/test file performing the same steps",
        "",
        "Running it",
        "  npm init -y && npm i -D @playwright/test && npx playwright install",
        "  npx playwright test repro.spec.ts --headed",
        "  (or: traceiq replay <run> --result <result>)",
        "",
        "  A captured trace opens with:  npx playwright show-trace artifacts/<trace>.zip",
        "",
        "Secrets",
        "  The bundle never contains secret values. Every {{secret.NAME}} in the files is",
        "  read from the environment variable TRACEIQ_SECRET_<NAME> (upper-cased, non-alphanumerics",
        "  as underscores) when repro.spec.ts runs. Steps that use one fail with a message naming",
        "  the variable if it is unset.",
    ]
    if keys:
        lines += ["", "  Variables this case needs:"]
        for k in keys:
            origin = placeholders.get(k)
            note = f"   (replaced a literal {origin} — ask the project owner for the value)" if origin \
                else "   (a project secret — same value as in TraceIQ)"
            lines.append(f"    export {env_var_for_secret(k)}=...{note}")
    else:
        lines += ["", "  This case references no secrets."]
    lines += [
        "",
        "Environment",
        f"  The run used environment: {env_name or '(none)'}. {{{{env.X}}}} values come from run.json;",
        "  override any of them with TRACEIQ_ENV_<X>.",
        "",
        "Not reproduced",
        "  Persona sessions and the project's stored login session are not exported (they are",
        "  live credentials). Steps the generator cannot translate are commented TODOs in",
        "  repro.spec.ts with the original step JSON.",
    ]
    if artifacts:
        lines += ["", "Artifacts included:"] + [f"  {a['path']} ({a['bytes']} bytes)" for a in artifacts]
    if skipped:
        lines += ["", "Artifacts not included:"] + [f"  {s['key']}: {s['reason']}" for s in skipped]
    return "\n".join(lines) + "\n"


def build_zip(files: Dict[str, bytes], fileobj: Optional[Any] = None) -> Any:
    """Write `files` (archive path → bytes) into a zip. Paths are prefixed
    with one top-level directory so unzipping never scatters files."""
    out = fileobj if fileobj is not None else io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(files):
            data = files[path]
            # Media is already compressed; deflating it only burns CPU.
            ctype = zipfile.ZIP_STORED if path.endswith((".webm", ".png", ".zip", ".jpg")) else zipfile.ZIP_DEFLATED
            zf.writestr(path, data, compress_type=ctype)
    out.seek(0)
    return out


def dumps(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")
