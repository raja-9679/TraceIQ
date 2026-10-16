"""Phase E — agent reference + bulk-ops endpoints.

Three flavors of endpoint:
  1. Reference: `/api/step-types`, `/api/agent-guide` — what TraceIQ supports.
     Hand-maintained content the agent reads once to ground its authoring.
  2. Bulk ops: `/api/cases/bulk-propose`, `/api/cases/bulk-set-code-paths` —
     authoring at fleet-scale without a round-trip per case.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    CaseProposal,
    CaseProposalAction,
    CaseProposalCreate,
    CaseProposalRead,
    Project,
    TestCase,
)
from app.services.access_service import access_service

router = APIRouter()


# ---------------------------------------------------------------------------
# Reference
# ---------------------------------------------------------------------------

# Hand-maintained step-type catalog. Mirrors what
# `execution-engine/src/core/test-executor.ts` actually implements. The agent
# uses this to know what step types are valid and what params each accepts —
# without having to read the runner source.
#
# Keep this in sync when adding a new step type to test-executor.ts. A CI
# script can later parse the switch statement and warn when entries diverge.
_STEP_TYPES: List[Dict[str, Any]] = [
    {
        "type": "goto",
        "category": "navigation",
        "params": {"value": "Absolute URL to navigate to"},
        "example": {"id": "<uuid>", "type": "goto", "value": "http://app/login"},
        "notes": "Use full URLs reachable from the worker container (often via the docker bridge gateway).",
    },
    {
        "type": "click",
        "category": "interaction",
        "params": {"selector": "CSS / Playwright selector"},
        "example": {"id": "<uuid>", "type": "click", "selector": "#submit-button"},
    },
    {
        "type": "fill",
        "category": "interaction",
        "params": {"selector": "Selector of an <input>/<textarea>/[contenteditable]", "value": "Text to type"},
        "example": {"id": "<uuid>", "type": "fill", "selector": "#email", "value": "alice@example.com"},
        "notes": "Does NOT work on <select> — use `select-option` instead.",
    },
    {
        "type": "select-option",
        "category": "interaction",
        "params": {"selector": "Selector of a <select>", "value": "Option value to choose"},
        "example": {"id": "<uuid>", "type": "select-option", "selector": "#priority", "value": "high"},
    },
    {
        "type": "press-key",
        "category": "interaction",
        "params": {"value": "Key name, e.g. 'Enter', 'Escape', 'ArrowDown'"},
        "example": {"id": "<uuid>", "type": "press-key", "value": "Enter"},
    },
    {
        "type": "hover",
        "category": "interaction",
        "params": {"selector": "Element to hover"},
        "example": {"id": "<uuid>", "type": "hover", "selector": "[data-testid='profile-menu']"},
    },
    {
        "type": "scroll-to",
        "category": "interaction",
        "params": {"selector": "Element to scroll into view"},
    },
    {
        "type": "check",
        "category": "interaction",
        "params": {"selector": "Checkbox / radio to check"},
        "example": {"id": "<uuid>", "type": "check", "selector": "#accept-terms"},
    },
    {
        "type": "uncheck",
        "category": "interaction",
        "params": {"selector": "Checkbox to uncheck"},
        "example": {"id": "<uuid>", "type": "uncheck", "selector": "#newsletter-opt-in"},
    },
    {
        "type": "double-click",
        "category": "interaction",
        "params": {"selector": "Element to double-click"},
        "example": {"id": "<uuid>", "type": "double-click", "selector": ".file-row[data-name='report.pdf']"},
    },
    {
        "type": "right-click",
        "category": "interaction",
        "params": {"selector": "Element to right-click (opens context menus)"},
        "example": {"id": "<uuid>", "type": "right-click", "selector": ".tree-node"},
    },
    {
        "type": "drag-and-drop",
        "category": "interaction",
        "params": {"selector": "Source element", "value": "Target element selector"},
        "example": {"id": "<uuid>", "type": "drag-and-drop", "selector": ".card[data-id='7']", "value": ".column[data-status='done']"},
    },
    {
        "type": "upload-file",
        "category": "interaction",
        "params": {
            "selector": "The <input type=file> element",
            "params.files": "Inline fixtures: [{name, content_base64}] written to a temp dir on the worker",
            "value": "Alternative: comma-separated worker-local file paths (pre-mounted fixtures)",
        },
        "example": {
            "id": "<uuid>",
            "type": "upload-file",
            "selector": "input[type=file]",
            "params": {"files": [{"name": "avatar.png", "content_base64": "<base64>"}]},
        },
        "notes": "Prefer params.files — inline base64 fixtures travel with the test case and need nothing on the worker.",
    },
    {
        "type": "download-file",
        "category": "interaction",
        "params": {
            "params.trigger_selector": "Element clicked to start the download (required in practice — steps are sequential, so the trigger must overlap the wait)",
            "params.filename_contains": "Assert the suggested filename contains this substring (optional)",
            "params.variableName": "Store the downloaded filename in this variable (optional)",
        },
        "example": {
            "id": "<uuid>",
            "type": "download-file",
            "params": {"trigger_selector": "#export-csv", "filename_contains": ".csv"},
        },
        "notes": "The file is saved with the run's artifacts.",
    },
    {
        "type": "handle-dialog",
        "category": "interaction",
        "params": {
            "params.action": "'accept' (default) or 'dismiss'",
            "params.prompt_text": "Text typed into prompt() dialogs before accepting (optional)",
            "params.variableName": "Store the dialog message for a later assert (optional)",
        },
        "example": {
            "id": "<uuid>",
            "type": "handle-dialog",
            "params": {"action": "accept"},
        },
        "notes": "Arms a one-shot handler for the NEXT dialog — place this step BEFORE the click that triggers the alert/confirm/prompt. Without it, Playwright auto-dismisses dialogs.",
    },
    {
        "type": "switch-tab",
        "category": "navigation",
        "params": {
            "value": "'latest' | 1-based tab index | URL substring",
            "params.trigger_selector": "Element clicked to open the popup/new tab; the step waits for the new page (optional)",
        },
        "example": {
            "id": "<uuid>",
            "type": "switch-tab",
            "value": "latest",
            "params": {"trigger_selector": "a[target=_blank]"},
        },
        "notes": "Subsequent steps run against the switched-to tab. Use `switch-tab` with value '1' to return to the first tab.",
    },
    {
        "type": "wait-for-selector",
        "category": "wait",
        "params": {"selector": "Selector to wait for"},
        "notes": "BEWARE: this blocks on in-flight navigations. If a redirect is happening, the wait can stall. Use `wait-timeout` + a title/URL assertion instead when the page may navigate.",
    },
    {
        "type": "wait-timeout",
        "category": "wait",
        "params": {"value": "Milliseconds to wait (string)"},
        "example": {"id": "<uuid>", "type": "wait-timeout", "value": "1500"},
    },
    {
        "type": "wait-for-response",
        "category": "wait",
        "params": {
            "value": "URL substring the response must contain",
            "params.status": "Expected HTTP status of the matching response (optional)",
            "params.trigger_selector": "Element clicked AFTER arming the wait, so the request it fires is caught (optional)",
            "params.variableName": "Store the matched response status (optional)",
        },
        "example": {
            "id": "<uuid>",
            "type": "wait-for-response",
            "value": "/api/search",
            "params": {"status": 200, "trigger_selector": "#search-btn"},
        },
        "notes": "Prefer this over wait-timeout for XHR-driven UIs. Use trigger_selector when a click fires the request — sequential steps cannot overlap otherwise.",
    },
    {
        "type": "wait-for-email",
        "category": "wait",
        "params": {
            "params.to": "Inbox to read — {{inbox.address}} (default) or {{inbox.address.<label>}}; only this run's addresses work",
            "params.subject": "Subject must contain this (case-insensitive, optional)",
            "params.subjectRegex": "Subject must match this regex (optional)",
            "params.from": "Sender must contain this (optional)",
            "params.extract": "'link' | 'code' | 'body' | 'subject' | 'none' (default 'link')",
            "params.pattern": "link: regex the URL must match (e.g. '/reset'); code: regex, group 1 if present (default \\b(\\d{6})\\b)",
            "params.variableName": "Variable for the extracted value (default email_link / email_code / email_body / email_subject)",
            "params.timeout": "Milliseconds to wait (default 60000, max 300000)",
        },
        "example": {
            "id": "<uuid>",
            "type": "wait-for-email",
            "params": {"to": "{{inbox.address}}", "subject": "Reset your password",
                       "extract": "link", "pattern": "/reset", "variableName": "reset_link"},
        },
        "notes": "Type {{inbox.address}} into the app's email field first — it is a per-run, per-case "
                 "address on the instance's test inbox (Mailpit or an IMAP plus-address). Use "
                 "{{inbox.address.<label>}} for several addresses in one case. Waits for the NEWEST "
                 "matching email, then e.g. goto '{{reset_link}}' or fill the OTP field with "
                 "'{{email_code}}'. Fails at once when the instance has no test inbox configured.",
    },
    {
        "type": "wait-for-webhook",
        "category": "wait",
        "params": {
            "params.url": "Capture URL — {{webhook.url}} (default) or {{webhook.url.<label>}}",
            "params.method": "Only requests with this HTTP method (optional)",
            "params.path": "Only requests whose path after the token contains this (optional)",
            "params.where": "Object of {jsonPath: expected} the JSON body must match, e.g. {'event': 'payment.succeeded'} (optional)",
            "params.extract": "JSON path to store, e.g. 'data.order.id' (optional; default stores the whole body)",
            "params.equals": "Fail unless the extracted value equals this (optional)",
            "params.variableName": "Variable for the extracted value (default 'webhook_body', or 'webhook_value' with extract)",
            "params.timeout": "Milliseconds to wait (default 60000, max 300000)",
        },
        "example": {
            "id": "<uuid>",
            "type": "wait-for-webhook",
            "params": {"url": "{{webhook.url}}", "method": "POST", "where": {"type": "order.paid"},
                       "extract": "data.id", "variableName": "order_id"},
        },
        "notes": "Register {{webhook.url}} as the callback URL in the app under test (UI or http-request "
                 "step). It resolves to <public TraceIQ URL>/api/hooks/capture/<token>, which the app must "
                 "be able to reach. Captures are redacted before storage (credential headers and denylisted "
                 "JSON keys such as token/password become [REDACTED]), capped at 256 kB / 50 per URL, and "
                 "kept 24 h. The newest matching request wins.",
    },
    {
        "type": "expect-visible",
        "category": "assertion",
        "params": {"selector": "Element that must be visible"},
    },
    {
        "type": "expect-hidden",
        "category": "assertion",
        "params": {"selector": "Element that must NOT be visible"},
    },
    {
        "type": "expect-text",
        "category": "assertion",
        "params": {"selector": "Element to check", "value": "Expected text (default: exact match)"},
    },
    {
        "type": "expect-not-text",
        "category": "assertion",
        "params": {"selector": "Element to check (must exist)", "value": "Text that must be ABSENT from the element"},
        "example": {"id": "<uuid>", "type": "expect-not-text", "selector": ".todo-list", "value": "Deleted item"},
        "notes": "The element itself must exist; to assert an element is gone entirely use expect-hidden.",
    },
    {
        "type": "expect-url",
        "category": "assertion",
        "params": {"value": "URL pattern (use Playwright glob: '**/dashboard**', not '/dashboard')"},
        "notes": "Playwright's waitForURL with a bare string does EXACT match. Use globs (`**`) for substring.",
    },
    {
        "type": "expect-title",
        "category": "assertion",
        "params": {
            "value": "Expected page title (or fragment)",
            "params.operator": "'contains' (default) | 'equals' | 'matches' (regex)",
        },
        "example": {"id": "<uuid>", "type": "expect-title", "value": "Dashboard"},
    },
    {
        "type": "assert",
        "category": "assertion",
        "params": {
            "selector": "Element to inspect",
            "params.source": "'text' | 'value' | 'attribute' | 'count' | 'css'",
            "params.operator": "'equals' | 'contains' | ...",
            "params.attribute": "Required when source='attribute'",
            "params.property": "CSS property name, required when source='css' (compared against the computed style, e.g. 'color' -> 'rgb(255, 0, 0)')",
            "value": "Expected value",
        },
        "example": {
            "id": "<uuid>",
            "type": "assert",
            "selector": "title",
            "params": {"source": "text", "operator": "contains"},
            "value": "Dashboard",
        },
    },
    {
        "type": "screenshot",
        "category": "capture",
        "params": {"value": "Filename (no extension)"},
    },
    {
        "type": "expect-visual-match",
        "category": "assertion",
        "params": {},
        "notes": "Phase B. Compares against a stored VisualBaseline keyed by (test_case_id, step.id, browser, device).",
    },
    {
        "type": "mock-response",
        "category": "network",
        "params": {
            "selector": "URL glob to intercept (e.g. '**/api/user')",
            "params.status": "HTTP status to return (default 200)",
            "params.json": "JSON body to return (sets content-type application/json)",
            "params.body": "Raw string body (when not JSON)",
            "params.content_type": "Override content-type",
            "params.headers": "Response headers object",
        },
        "example": {"id": "<uuid>", "type": "mock-response", "selector": "**/api/me",
                    "params": {"status": 200, "json": {"name": "Test User"}}},
        "notes": "Register BEFORE the goto/action that triggers the request.",
    },
    {
        "type": "block-request",
        "category": "network",
        "params": {"selector": "URL glob to abort (e.g. '**/analytics/**')"},
        "example": {"id": "<uuid>", "type": "block-request", "selector": "**/gtag/**"},
    },
    {
        "type": "set-network-latency",
        "category": "network",
        "params": {"selector": "URL glob (default '**/*')", "params.ms": "Delay in ms before continuing"},
        "example": {"id": "<uuid>", "type": "set-network-latency", "selector": "**/api/**", "params": {"ms": 2000}},
    },
    {
        "type": "check-accessibility",
        "category": "assertion",
        "params": {
            "params.impact": "Minimum severity that fails the step: 'minor'|'moderate'|'serious'(default)|'critical'",
            "params.fail": "false = report-only (never throws)",
        },
        "example": {"id": "<uuid>", "type": "check-accessibility", "params": {"impact": "serious"}},
        "notes": "Runs axe-core against the current page; returns a violations summary.",
    },
    # ------------------------------------------------------------------
    # Frontend quality (WP10). Layout checks run in the page; budgets store a
    # PageBudgetSample per run so each page has a trend.
    # ------------------------------------------------------------------
    {
        "type": "expect-no-horizontal-overflow",
        "category": "assertion",
        "params": {
            "selector": "Optional scope: only elements inside this container are checked",
            "params.tolerance_px": "Pixels of overflow tolerated (default 1)",
            "params.ignore": "List of CSS selectors whose elements (and descendants) are ignored, e.g. carousels",
            "params.max_offenders": "How many offending elements to report (default 10, max 50)",
        },
        "example": {"id": "<uuid>", "type": "expect-no-horizontal-overflow",
                    "params": {"tolerance_px": 2, "ignore": [".carousel-track"]}},
        "notes": "Fails when the document scrolls horizontally or a visible element spills past the "
                 "left/right viewport edge (elements clipped by an overflow:hidden/auto ancestor are "
                 "not counted). Pair with a viewport sweep (POST /api/runs body `viewports`).",
    },
    {
        "type": "expect-rtl-layout",
        "category": "assertion",
        "params": {
            "params.expect_dir": "'rtl' (default) or 'ltr' — the direction the document must have",
            "selector": "Optional element whose computed direction must also match",
            "params.tolerance_px": "Horizontal-overflow tolerance (default 1)",
            "params.ignore": "Selectors to ignore in the overflow part",
        },
        "example": {"id": "<uuid>", "type": "expect-rtl-layout", "params": {"expect_dir": "rtl"}},
        "notes": "Checks <html dir> / computed direction, then the same no-overflow check — RTL "
                 "regressions usually show up as content pushed off the LEFT edge. Use with a locale "
                 "matrix run (body `locales: [\"ar-EG\"]`).",
    },
    {
        "type": "check-page-budget",
        "category": "assertion",
        "params": {
            "params.budgets": "Max values: {lcp_ms, cls, inp_ms, fcp_ms, ttfb_ms, load_ms, page_weight_kb, "
                              "request_count, image_weight_kb, largest_image_kb, script_weight_kb, "
                              "third_party_requests}",
            "params.seo": "'warn' (default: report, never fail) | 'fail' (an SEO error fails the step) | 'off'",
            "params.title_length": "[min, max] title length (default [10, 60])",
            "params.description_length": "[min, max] meta description length (default [50, 160])",
            "params.settle_ms": "Wait before measuring so LCP/CLS settle (default 1000, max 10000)",
            "params.fail": "false = record only, never fail",
        },
        "example": {"id": "<uuid>", "type": "check-page-budget",
                    "params": {"budgets": {"lcp_ms": 2500, "cls": 0.1, "page_weight_kb": 1500,
                                           "request_count": 80}, "seo": "warn"}},
        "notes": "Measures the CURRENT page with Playwright (no Lighthouse dependency): LCP, CLS, FCP, "
                 "TTFB, an INP approximation (longest event-timing entry — needs a preceding "
                 "interaction step, else null), page/image/script weight, request count, and SEO "
                 "checks (title/description length, canonical, robots noindex, one h1, img alt "
                 "coverage, lang, hreflang validity, JSON-LD parse errors, viewport meta). Each "
                 "outcome is stored for the per-page trend (GET /api/projects/{id}/page-budgets).",
    },
    {
        "type": "http-request",
        "category": "api",
        "params": {
            "value": "URL to call (comma-separated for batch)",
            "params.method": "'GET'|'POST'|'PUT'|'PATCH'|'DELETE' (default 'GET')",
            "params.headers": "Object — e.g. {'Authorization': 'Bearer ...'}",
            "params.body": "Request body (object or string)",
            "params.params": "Query-param object",
            "params.assertions": "Array of {type, ...} — see assertion shapes below",
            "params.extract": "Array of {path, variable, required?} — store JSON response values as runtime vars",
            "params.cleanup": ("true (DELETE the response's Location) or {method?, url, headers?, body?, "
                               "expect_status?} — registers a cleanup that runs after the case even if it "
                               "fails; see `templates` → cleanup"),
            "params.expect_schema": (
                "Optional response contract check: {schema: <JSON Schema object>} or "
                "{openapi: {operationId} | {method, path}, status?: '200'} referencing the project's "
                "stored OpenAPI spec (PUT /api/projects/{id}/openapi-spec, or imported via "
                "/cases/from-openapi). Strict JSON Schema; every violation is reported by path"),
        },
        "notes": (
            "params.expect_schema validates the JSON body strictly (unlike the lenient "
            "json-schema assertion); an openapi reference that cannot be resolved fails the step. "
            "Assertion shapes inside params.assertions: "
            "{type:'status', value:200}; "
            "{type:'json-path', path:'postgres.articles', operator:'equals'|'contains', value:...}; "
            "{type:'json-schema', value:'<JSON-schema-as-string>'} — json-path uses dotted paths (NO '$.' prefix); "
            "array indexing via 'items.0.id' or 'items[0].id'. "
            "params.extract chains requests: extract {path:'data.token', variable:'token'} then use "
            "{{token}} in a later step's headers/body/url. Extraction failure fails the step unless required:false."
        ),
        "example": {
            "id": "<uuid>",
            "type": "http-request",
            "value": "http://app/api/overview",
            "params": {
                "method": "GET",
                "headers": {"Authorization": "Bearer ..."},
                "assertions": [
                    {"type": "status", "value": 200},
                    {"type": "json-path", "path": "status", "operator": "equals", "value": "healthy"},
                ],
            },
        },
    },
    {
        "type": "graphql",
        "category": "api",
        "params": {
            "value": "GraphQL endpoint URL",
            "params.query": "GraphQL query/mutation string",
            "params.variables": "Optional variables object",
            "params.headers": "Optional extra headers (merged over suite headers)",
            "params.allow_errors": "true to skip the default errors[]-must-be-empty check",
            "params.assertions": "Array of {type:'status', value} or {type:'data-path', path, operator:'equals'|'contains'|'exists', value}",
            "params.extract": "Array of {path, variable} — paths relative to response `data`",
        },
        "example": {
            "id": "<uuid>",
            "type": "graphql",
            "value": "http://app/graphql",
            "params": {
                "query": "query($id: ID!) { user(id: $id) { name email } }",
                "variables": {"id": "1"},
                "assertions": [{"type": "data-path", "path": "user.name", "operator": "exists"}],
            },
        },
        "notes": "POSTs {query, variables}. Fails when the response carries GraphQL errors[] unless allow_errors. data-path paths omit the leading 'data.'.",
    },
    {
        "type": "oauth2-token",
        "category": "api",
        "params": {
            "value": "Token endpoint URL",
            "params.client_id": "Client id (supports {{secret.X}})",
            "params.client_secret": "Client secret — ALWAYS use {{secret.X}}",
            "params.scope": "Optional scope",
            "params.audience": "Optional audience",
            "params.variable": "Runtime var for the token (default 'access_token')",
        },
        "example": {
            "id": "<uuid>",
            "type": "oauth2-token",
            "value": "http://app/oauth/token",
            "params": {"client_id": "{{secret.CLIENT_ID}}", "client_secret": "{{secret.CLIENT_SECRET}}"},
        },
        "notes": "client_credentials grant for the app under test. Later steps use {{access_token}} e.g. headers: {'Authorization': 'Bearer {{access_token}}'}.",
    },
    {
        "type": "feed-check",
        "category": "api",
        "params": {"value": "URL to fetch", "params.assertions": "Same shape as http-request assertions"},
        "notes": "Self-contained fetch+assert. NOT a way to assert on a previous step's response — use http-request's embedded assertions for that.",
    },
    {
        "type": "check-tls",
        "category": "api",
        "params": {
            "value": "Host or URL (port from URL, default 443)",
            "params.min_days_remaining": "Fail when the cert expires within N days (default 14)",
        },
        "example": {"id": "<uuid>", "type": "check-tls", "value": "https://example.com",
                    "params": {"min_days_remaining": 21}},
        "notes": "TLS probe: validates the chain and the expiry window. Pairs well with a monitor schedule for cert-expiry alerting.",
    },
    {
        "type": "manual-step",
        "category": "manual",
        "params": {
            "value": "Instruction for the tester (what to do)",
            "params.expected": "Expected result the tester checks against",
        },
        "example": {
            "id": "<uuid>",
            "type": "manual-step",
            "value": "Open the invoice PDF from the order confirmation email",
            "params": {"expected": "The PDF shows the order number and the VAT breakdown"},
        },
        "notes": (
            "A human-executed step. A case containing it gets executor='manual' automatically and MUST "
            "contain only manual-step steps. Manual cases are never sent to workers — automated runs skip "
            "them; a tester runs them from a manual run (POST /api/manual-runs) and records "
            "passed / failed / blocked / skipped per case. Results appear in run history with "
            "executor='manual'."
        ),
    },
    {
        "type": "load-test",
        "category": "load",
        "params": {
            "value": "Target URL",
            "params.method": "'GET' (default) | POST | PUT | PATCH | DELETE",
            "params.headers": "Optional headers object",
            "params.body": "Optional body (object or string)",
            "params.vus": "Virtual users (default 10, capped by LOAD_MAX_VUS)",
            "params.duration_s": "Steady-state seconds (default 30, capped by LOAD_MAX_DURATION_S)",
            "params.ramp_up_s": "Optional ramp-up seconds to reach vus",
            "params.think_time_s": "Sleep between iterations (default 1)",
            "params.thresholds": "{p95_ms?, p99_ms?, error_rate? (0-1), min_rps?} — any breach fails the case",
            "params.prometheus_queries": "Optional [{name, template}] (max 10): names of operator-approved "
                                         "PromQL templates (PROMETHEUS_QUERY_TEMPLATES) evaluated over the "
                                         "test window and shown next to the k6 metrics. Free-form "
                                         "[{name, query}] PromQL only runs where the instance enables "
                                         "PROMETHEUS_ALLOW_ADHOC_QUERIES",
        },
        "example": {
            "id": "<uuid>",
            "type": "load-test",
            "value": "http://app/api/overview",
            "params": {"vus": 20, "duration_s": 60, "thresholds": {"p95_ms": 500, "error_rate": 0.01}},
        },
        "notes": (
            "Declares a k6 load test — a case containing this step gets executor='load' automatically and "
            "MUST be the only meaningful step in the case (load cases skip the browser entirely). "
            "Aggregate metrics land in result_payload (result_kind='load'); the generated k6 script and "
            "summary are uploaded as run artifacts. Only run against apps you own."
        ),
    },
    {
        "type": "run-script",
        "category": "interaction",
        "params": {
            "params.language": "'javascript' (default) | 'python'",
            "params.body": "Script body — receives `variables` (Object) — return value stored under params.variableName",
            "params.variableName": "Optional. Where to save the return value in the test context.",
        },
        "example": {
            "id": "<uuid>",
            "type": "run-script",
            "params": {
                "language": "javascript",
                "body": "localStorage.setItem('user', JSON.stringify({token: 'X'})); return 'ok';",
            },
        },
    },
    {
        "type": "extract-value",
        "category": "data",
        "params": {"selector": "Element", "params.source": "'text'|'value'|'attribute'", "params.variableName": "Variable name to save under",
                   "params.cleanup": "true (the extracted value is a URL to DELETE) or {method?, url, ...} — see templates → cleanup"},
    },
    {
        "type": "totp",
        "category": "data",
        "params": {
            "params.secret": "Authenticator (base32) secret — MUST be {{secret.NAME}}; a raw key fails the step",
            "params.variableName": "Variable to store the code under (default 'otp')",
            "params.digits": "Optional, default 6",
            "params.period": "Optional seconds per code, default 30",
        },
        "example": {
            "id": "<uuid>",
            "type": "totp",
            "params": {"secret": "{{secret.MFA_SEED}}", "variableName": "otp"},
        },
        "notes": "Generates the current RFC 6238 2FA code (Google/Microsoft Authenticator style) and stores it; "
                 "a later step types it with value '{{otp}}'. Waits for the next code when under 5 s remain. "
                 "Store the seed shown at the site's 2FA enrolment as a project secret first.",
    },
    {
        "type": "switch-frame",
        "category": "navigation",
        "params": {"selector": "iframe selector"},
    },
    {
        "type": "carousel-find",
        "category": "interaction",
        "params": {"selector": "Target", "value": "Next-button selector", "params.max_swipes": "Default 10"},
    },
    {
        "type": "verify-nth-child",
        "category": "assertion",
        "params": {"selector": "Parent", "params.n": "Index", "value": "Expected text/attr"},
    },
    {
        "type": "count-children",
        "category": "assertion",
        "params": {"selector": "Parent", "params.operator": "'equals' | 'gt' | 'gte' | 'lt' | 'lte'", "value": "Expected count"},
    },
    {
        "type": "amp-validate",
        "category": "api",
        "params": {"value": "URL of an AMP doc"},
        "notes": "Validates AMP HTML compliance.",
    },
    # ------------------------------------------------------------------
    # Security assertions (WP4). Header values in results are redacted;
    # cookie values are never reported, only flags.
    # ------------------------------------------------------------------
    {
        "type": "expect-header",
        "category": "security",
        "params": {
            "params.name": "Header name (case-insensitive), e.g. Strict-Transport-Security",
            "params.mode": "'present' (default) | 'absent' | 'equals' | 'regex' (case-insensitive)",
            "params.value": "Expected value for equals / pattern for regex",
            "params.url": "Optional URL to GET (shares the browser's cookies); default the current page URL",
        },
        "example": {"id": "<uuid>", "type": "expect-header",
                    "params": {"name": "Content-Security-Policy", "mode": "regex", "value": "default-src"}},
        "notes": "Re-requests the page and checks the response header — HSTS, CSP, X-Frame-Options, "
                 "or 'absent' for X-Powered-By / Server version leaks.",
    },
    {
        "type": "expect-cookie-flags",
        "category": "security",
        "params": {
            "params.name": "Cookie name",
            "params.secure": "true/false — required Secure flag (omit to not check)",
            "params.httpOnly": "true/false — required HttpOnly flag",
            "params.sameSite": "'Strict' | 'Lax' | 'None'",
            "params.url": "Optional URL to GET first so its Set-Cookie lands in the jar",
        },
        "example": {"id": "<uuid>", "type": "expect-cookie-flags",
                    "params": {"name": "sessionid", "secure": True, "httpOnly": True, "sameSite": "Lax"}},
        "notes": "Fails when the cookie is missing or any matching cookie lacks a required flag.",
    },
    {
        "type": "expect-blocked-after",
        "category": "security",
        "params": {
            "value": "URL to repeat (or params.url); relative paths use the environment base_url",
            "params.method": "Default POST",
            "params.body": "Request body (e.g. a wrong-password login)",
            "params.headers": "Extra headers",
            "params.attempts": "N — must be blocked by this attempt (default 10, max 50)",
            "params.statuses": "Blocking statuses (default [429, 423, 403])",
            "params.interval_ms": "Optional delay between attempts (max 5000)",
            "params.authorized": "MUST be true — attestation that you may hammer this target",
        },
        "example": {"id": "<uuid>", "type": "expect-blocked-after", "value": "/api/auth/login",
                    "params": {"body": {"email": "probe@example.com", "password": "wrong"},
                               "attempts": 10, "authorized": True}},
        "notes": "Rate-limit / lockout check. Refused unless the project's security testing is enabled, "
                 "the host is on its Security allowlist, and authorized=true.",
    },
    {
        "type": "expect-session-dead",
        "category": "security",
        "params": {
            "value": "URL to replay with the pre-logout session (or params.url)",
            "params.logout_url": "Logout endpoint (sent with params.logout_method, default POST)",
            "params.logout_selector": "…or a logout button to click instead",
            "params.headers": "Extra replay headers, e.g. {'Authorization': 'Bearer {{token}}'}",
            "params.statuses": "Statuses that mean 'dead' (default [401, 403, 440])",
            "params.login_pattern": "Regex a redirect Location must match to count as dead",
        },
        "example": {"id": "<uuid>", "type": "expect-session-dead", "value": "/api/me",
                    "params": {"logout_url": "/api/auth/logout"}},
        "notes": "Snapshots the cookies, logs out, then replays the old session. Passes on a dead status "
                 "or a redirect to login; fails if the server still accepts it (logout only cleared the "
                 "browser cookie).",
    },
    # ------------------------------------------------------------------
    # Mobile (native app) steps — Phase MOB. Cases using these MUST set
    # executor='mobile_appium' and the run MUST pin an app_build_id
    # (POST /api/runs body). Executed by the mobile worker via Appium.
    # Selector convention (Appium locator strategies):
    #   "~foo"                     → accessibility id
    #   "//..." or "xpath=..."     → XPath over the native page source
    #   "id=com.app:id/btn"        → resource-id (Android) / name (iOS)
    #   "android=new UiSelector()…"→ UiAutomator (Android only)
    #   "ios=label == 'Done'"      → NSPredicate (iOS only)
    # ------------------------------------------------------------------
    {
        "type": "mobile-launch-app",
        "category": "mobile",
        "params": {},
        "example": {"id": "<uuid>", "type": "mobile-launch-app"},
        "notes": "Activates the run's installed app build (uses the build's package_id). Implicit before the first mobile step; include it explicitly to relaunch mid-test.",
    },
    {
        "type": "mobile-tap",
        "category": "mobile",
        "params": {"selector": "Appium locator (see convention above)"},
        "example": {"id": "<uuid>", "type": "mobile-tap", "selector": "~login-button"},
    },
    {
        "type": "mobile-long-press",
        "category": "mobile",
        "params": {"selector": "Appium locator", "params.duration_ms": "Hold time, default 800"},
    },
    {
        "type": "mobile-type",
        "category": "mobile",
        "params": {"selector": "Appium locator of a text field", "value": "Text to type"},
        "example": {"id": "<uuid>", "type": "mobile-type", "selector": "~email-input", "value": "alice@example.com"},
    },
    {
        "type": "mobile-swipe",
        "category": "mobile",
        "params": {"params.direction": "'up' | 'down' | 'left' | 'right'", "params.distance": "0..1 fraction of the screen, default 0.5"},
        "example": {"id": "<uuid>", "type": "mobile-swipe", "params": {"direction": "up"}},
        "notes": "Swipes from screen centre; direction is the finger's movement (swipe up scrolls content down).",
    },
    {
        "type": "mobile-press-key",
        "category": "mobile",
        "params": {"value": "'back' | 'home' | 'enter' (Android keycodes)"},
        "example": {"id": "<uuid>", "type": "mobile-press-key", "value": "back"},
        "notes": "Android only; iOS has no hardware back key.",
    },
    {
        "type": "mobile-wait-for",
        "category": "mobile",
        "params": {"selector": "Appium locator", "params.timeout_ms": "Default 10000"},
    },
    {
        "type": "mobile-expect-visible",
        "category": "mobile",
        "params": {"selector": "Appium locator"},
        "example": {"id": "<uuid>", "type": "mobile-expect-visible", "selector": "~dashboard-header"},
    },
    {
        "type": "mobile-expect-text",
        "category": "mobile",
        "params": {"selector": "Appium locator", "value": "Expected text (substring match)"},
    },
    {
        "type": "mobile-screenshot",
        "category": "mobile",
        "params": {"value": "Optional label for the artifact filename"},
        "notes": "Uploaded to MinIO under runs/{run_id}/screenshots/ like web screenshots. A failure screenshot is also captured automatically when a mobile case fails.",
    },
    {
        "type": "mobile-extract-value",
        "category": "mobile",
        "params": {"selector": "Appium locator", "value": "Variable name to save the element's text under"},
        "example": {"id": "<uuid>", "type": "mobile-extract-value", "selector": "~order-id", "value": "orderId"},
        "notes": "Later steps in the same case reference it as {{orderId}}.",
    },
    {
        "type": "mobile-expect-visual-match",
        "category": "mobile",
        "params": {},
        "notes": "Captures the device screen and pixelmatch-compares it against the pinned VisualBaseline (browser key 'mobile'). First run is capture-only; promote the candidate via POST /api/visual-baselines/promote.",
    },
    {
        "type": "mobile-terminate-app",
        "category": "mobile",
        "params": {},
        "notes": "Force-stops the app (uses the build's package_id). Useful for cold-start and state-persistence tests.",
    },
]


# Template tokens resolved in any step value/selector/header/param/body, and
# the suite-hook / cleanup lifecycle. Served with /step-types so an agent
# reads one reference.
_TEMPLATES: Dict[str, Any] = {
    "tokens": [
        {"token": "{{env.KEY}}", "notes": "ProjectEnvironment variable (run's environment)."},
        {"token": "{{secret.KEY}}", "notes": "Decrypted project secret; never type credentials into steps."},
        {"token": "{{data.KEY}}", "notes": "Current dataset row of a data-driven case."},
        {"token": "{{name}}", "notes": "Runtime variable from extract-value / params.extract / totp earlier in the case."},
        {"token": "{{fake.KIND}}", "notes": ("uuid|email|name|first_name|last_name|username|phone|number|date|word. "
                                             "Random, unless a data seed is set: suite setting `data_seed` or POST /runs "
                                             "body `data_seed`. Same seed + case + data row => same values (each "
                                             "occurrence still differs within a case). The effective seed is recorded "
                                             "on the run (`data_seed`) — re-run with it to replay a failure's data.")},
        {"token": "{{run.id}}", "notes": "The TestRun id."},
        {"token": "{{run.short}}", "notes": "8-char lowercase token unique to the run (HMAC-derived; not the id)."},
        {"token": "{{run.worker}}", "notes": "Id of the worker executing this case."},
        {"token": "{{run.case}}", "notes": "The TestCase id."},
        {"token": "{{run.row}}", "notes": "1-based dataset row (matches the '[row N]' result name); 0 if not data-driven."},
        {"token": "{{run.started_at}}", "notes": "When the run was dispatched (ISO-8601 UTC)."},
        {"token": "{{run.attempt}}", "notes": "1 for the first try, 2+ for auto-retries."},
        {"token": "{{unique.NAME}}", "notes": ("12 lowercase alphanumerics, starting with a letter, unique per run + "
                                               "case + data row + attempt and stable within the attempt — use for "
                                               "emails/usernames that must not collide across parallel workers, e.g. "
                                               "'{{unique.user}}@example.com'. Never seeded.")},
        {"token": "{{setup.NAME}}", "notes": "Value extracted by a suite setup step (any suite in the case's chain)."},
        {"token": "{{inbox.address[.label]}}", "notes": "Per-run test inbox address (see wait-for-email)."},
        {"token": "{{webhook.url[.label]}}", "notes": "Per-run webhook capture URL (see wait-for-webhook)."},
    ],
    "suite_hooks": (
        "Suite settings `setup_steps` / `teardown_steps` (same step schema as cases; usually http-request). "
        "Each case runs its suite chain's setup outermost-first and teardown innermost-first; the chain stops "
        "at a suite with inherit_settings=false (its own hooks still apply). Per job: SEPARATE / PARALLEL / "
        "single-case dispatch = around every case; a multi-case CONTINUOUS sub-suite job runs each suite's "
        "setup once before its first case and teardown once after its last. A setup's extracted values are "
        "{{setup.NAME}} in cases and bare {{NAME}} inside that same setup. A failed setup marks the case "
        "'error' without running it. Teardown ALWAYS runs (after failures, errors and case timeouts — the "
        "worker reserves HOOK_TEARDOWN_BUDGET_MS of the job's time for it) and its failures are warnings "
        "(result_payload.hook_warnings), never a red case. Change them on existing suites with "
        "propose_update_suite_settings — never auto-applied. Server and local web workers only (not mobile)."
    ),
    "cleanup": (
        "params.cleanup on an http-request / extract-value step registers a cleanup once the step created "
        "something (a request that returned 2xx and then failed an assertion still counts). true = DELETE the "
        "response Location header (http-request) or the extracted URL (extract-value); an object "
        "{method='DELETE', url, headers?, body?, expect_status?} is resolved right after the step, so "
        "variables it extracted are available (url '/items/{{item_id}}'; relative URLs resolve against the "
        "request URL or the environment base_url; headers default to the suite headers + the step's own). "
        "Case cleanups run LIFO right after the case, setup cleanups after that suite's teardown. Success = "
        "expect_status, else 2xx/404/410. Outcomes land in result_payload.cleanup with the URL template "
        "(captured values unresolved) and query values dropped."
    ),
}


@router.get("/step-types")
async def list_step_types() -> Dict[str, Any]:
    """Catalogue of every step type the runner supports plus its params shape.

    No auth — this is reference content an agent can pull early in a session
    to ground its proposals. Includes examples and known gotchas.
    """
    return {
        "step_types": _STEP_TYPES,
        "total": len(_STEP_TYPES),
        "templates": _TEMPLATES,
        "last_updated": "2026-10-16",
    }


@router.get("/agent-guide")
async def get_agent_guide() -> Dict[str, Any]:
    """Returns the bundled AGENT_GUIDE.md as text + last-modified timestamp.

    The MCP server's `get_authoring_guide` tool surfaces this to the agent
    at the start of a session. The guide explains conventions
    (suite layout, code_paths globs, persona auth) and the five common
    pitfalls — designed so a fresh agent can author correct cases without
    relearning the same lessons.

    Path resolution:
      1. $TRACEIQ_AGENT_GUIDE_PATH — operator override
      2. <backend>/app/AGENT_GUIDE.md — bundled with the image
      3. <repo>/integrations/mcp-server/AGENT_GUIDE.md — dev/host run
    """
    import os as _os
    candidates: List[Optional[Path]] = [
        Path(_os.environ["TRACEIQ_AGENT_GUIDE_PATH"]) if _os.environ.get("TRACEIQ_AGENT_GUIDE_PATH") else None,
        # bundled copy (Dockerfile COPY . . picks it up from backend/app/)
        Path(__file__).resolve().parent.parent / "AGENT_GUIDE.md",
        # dev / host path (canonical source)
        Path(__file__).resolve().parents[3] / "integrations" / "mcp-server" / "AGENT_GUIDE.md",
    ]
    guide_path: Optional[Path] = None
    for c in candidates:
        if c and c.exists():
            guide_path = c
            break
    if not guide_path:
        raise HTTPException(
            status_code=503,
            detail=f"AGENT_GUIDE.md not found in any of: {[str(c) for c in candidates if c]}",
        )
    body = guide_path.read_text(encoding="utf-8")
    try:
        mtime = guide_path.stat().st_mtime
        last_modified = datetime.utcfromtimestamp(mtime).isoformat() + "Z"
    except OSError:
        last_modified = None
    return {
        "guide": body,
        "last_modified": last_modified,
        "size_chars": len(body),
        "source_path": str(guide_path),
    }


# ---------------------------------------------------------------------------
# Bulk-propose
# ---------------------------------------------------------------------------

class BulkProposalRequest(BaseModel):
    project_id: int
    proposals: List[CaseProposalCreate]


class BulkProposalItemResult(BaseModel):
    index: int
    status: str  # "created" | "rejected"
    proposal_id: Optional[int] = None
    error: Optional[str] = None


class BulkProposalResponse(BaseModel):
    project_id: int
    submitted: int
    created: int
    rejected: int
    results: List[BulkProposalItemResult]


@router.post("/cases/bulk-propose", response_model=BulkProposalResponse)
async def bulk_propose_cases(
    body: BulkProposalRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> BulkProposalResponse:
    """Submit many CaseProposals in one round-trip.

    Best-effort: a single bad proposal doesn't roll back the rest. Each
    item gets a per-index result so the agent knows what to retry.
    """
    if not await access_service.has_project_access(
        principal.user.id, body.project_id, session, min_role="editor"
    ):
        raise HTTPException(status_code=403, detail="Editor access required")
    # Agent kill switch + per-session proposal budget (WP14b), once per call.
    from app.services.policy import PolicyContext, evaluate
    _project = await session.get(Project, body.project_id)
    _ws_id = getattr(_project, "workspace_id", None)
    verdict = await evaluate("proposal.create", PolicyContext.from_principal(
        principal, workspace_id=_ws_id, project_id=body.project_id, session=session))
    if not verdict.allowed:
        raise verdict.http_exception()

    results: List[BulkProposalItemResult] = []
    created = 0
    for idx, p in enumerate(body.proposals):
        try:
            if p.project_id != body.project_id:
                raise ValueError(f"proposal[{idx}].project_id mismatch with outer body")
            if p.action == CaseProposalAction.CREATE and p.test_suite_id is None:
                raise ValueError("CREATE requires test_suite_id")
            if p.action in (CaseProposalAction.UPDATE, CaseProposalAction.DELETE, CaseProposalAction.MOVE):
                if p.target_case_id is None:
                    raise ValueError(f"{p.action.value.upper()} requires target_case_id")
            proposal = CaseProposal(
                project_id=p.project_id,
                test_suite_id=p.test_suite_id,
                target_case_id=p.target_case_id,
                action=p.action,
                payload=p.payload or {},
                rationale=p.rationale,
                ai_confidence=p.ai_confidence,
                agent_id=principal.agent_id,
                created_by_agent_id=principal.agent_id,
                agent_session_id=principal.agent_session_id,
            )
            session.add(proposal)
            await session.flush()
            # Workspace auto-apply policy — per-item, best-effort.
            from app.api.agent_ownership import maybe_auto_apply
            await maybe_auto_apply(proposal, principal.user.id, session)
            results.append(BulkProposalItemResult(index=idx, status="created", proposal_id=proposal.id))
            created += 1
        except Exception as exc:  # noqa: BLE001
            results.append(BulkProposalItemResult(index=idx, status="rejected", error=str(exc)[:200]))

    await session.commit()
    from app.services.agent_controls import record_usage
    await record_usage(session, principal, _ws_id, proposals=created)
    return BulkProposalResponse(
        project_id=body.project_id,
        submitted=len(body.proposals),
        created=created,
        rejected=len(body.proposals) - created,
        results=results,
    )


# ---------------------------------------------------------------------------
# Bulk-set-code-paths
# ---------------------------------------------------------------------------

class BulkCodePathsRequest(BaseModel):
    project_id: int
    mapping: Dict[int, List[str]]  # case_id → list of code path globs/prefixes


class BulkCodePathsItemResult(BaseModel):
    case_id: int
    status: str  # "updated" | "skipped" | "error"
    error: Optional[str] = None


class BulkCodePathsResponse(BaseModel):
    project_id: int
    submitted: int
    updated: int
    results: List[BulkCodePathsItemResult]


@router.post("/cases/bulk-set-code-paths", response_model=BulkCodePathsResponse)
async def bulk_set_code_paths(
    body: BulkCodePathsRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> BulkCodePathsResponse:
    """Atomically (per-row) set `code_paths` on many cases.

    Used by Mode-1 agents that walk source code locally and want to push
    file→case mapping into TraceIQ in one shot. Cases in projects the
    caller can't access are silently skipped (no info leak).
    """
    if not await access_service.has_project_access(
        principal.user.id, body.project_id, session, min_role="editor"
    ):
        raise HTTPException(status_code=403, detail="Editor access required")

    results: List[BulkCodePathsItemResult] = []
    updated = 0
    for case_id, paths in body.mapping.items():
        try:
            case = await session.get(TestCase, int(case_id))
            if not case:
                results.append(BulkCodePathsItemResult(case_id=case_id, status="skipped", error="not found"))
                continue
            if case.project_id != body.project_id:
                results.append(BulkCodePathsItemResult(case_id=case_id, status="skipped",
                                                       error="not in requested project"))
                continue
            case.code_paths = paths
            session.add(case)
            results.append(BulkCodePathsItemResult(case_id=case_id, status="updated"))
            updated += 1
        except Exception as exc:  # noqa: BLE001
            results.append(BulkCodePathsItemResult(case_id=case_id, status="error", error=str(exc)[:200]))

    await session.commit()
    return BulkCodePathsResponse(
        project_id=body.project_id,
        submitted=len(body.mapping),
        updated=updated,
        results=results,
    )
