"""Failure analysis — heuristic classification + optional LLM deepening.

`analyze_case_failure` always returns a valid FailureReport: a regex/heuristic
pass classifies the error first, and when an LLM provider is configured the
report is deepened with a structured JSON call (invalid LLM output falls back
to the heuristic result rather than failing).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from app.ai.providers import provider
from app.schemas.failure_report import FailureEvidence, FailureReport, RunFailureAnalysis
from app.services.llm_usage import llm_call_context

# (pattern, category, fix_target, summary) — first match wins.
_HEURISTICS = [
    (r"Timeout .*exceeded|timeout of \d+ms|Timed out", "test_bug", "test",
     "A wait timed out — the selector may be wrong, the timeout too tight, or the page genuinely slow."),
    (r"waiting for locator|not found|no element matches|failed to find element",
     "test_bug", "test",
     "A selector did not match any element — the UI may have changed or the selector is brittle."),
    (r"net::ERR_|ECONNREFUSED|ENOTFOUND|getaddrinfo|socket hang up", "environment", "infra",
     "The target host was unreachable from the worker — deployment or network issue, not a test defect."),
    (r"Expected status (\d+) but got (5\d\d)", "app_bug", "app",
     "The application returned a server error where the test expected success."),
    (r"Expected status .* but got 4\d\d", "unknown", "none",
     "The API rejected the request — either the app's contract changed (app bug) or the test sends a stale request shape."),
    (r"Assertion Failed|Expected .* to (equal|contain|match)", "app_bug", "app",
     "An assertion on page/API content failed — the observed behavior differs from the encoded expectation."),
    (r"JSON Schema validation failed|Missing required field", "app_bug", "app",
     "The API response no longer matches its schema — likely an application-side contract change."),
    (r"browser context was closed|Target page.*closed|browser has been closed", "flake", "none",
     "The browser context died mid-test — usually an infrastructure flake; retry before investigating."),
    (r"AMP Validation failed", "app_bug", "app",
     "The page fails AMP validation."),
    (r"Visual regression: diffRatio", "app_bug", "app",
     "The page's appearance changed beyond the visual-match tolerance."),
]

_MAX_STEPS_CHARS = 6_000
_MAX_ERROR_CHARS = 2_000


def heuristic_report(error_message: str, evidence: FailureEvidence) -> FailureReport:
    for pattern, category, fix_target, summary in _HEURISTICS:
        if re.search(pattern, error_message or "", re.IGNORECASE):
            return FailureReport(
                root_cause_category=category,  # type: ignore[arg-type]
                summary=summary,
                details=(error_message or "")[:_MAX_ERROR_CHARS],
                fix_target=fix_target,  # type: ignore[arg-type]
                confidence=0.5,
                evidence=evidence,
                analyzed_by="heuristic",
            )
    return FailureReport(
        root_cause_category="unknown",
        summary="Unrecognized failure pattern.",
        details=(error_message or "")[:_MAX_ERROR_CHARS],
        fix_target="none",
        confidence=0.2,
        evidence=evidence,
        analyzed_by="heuristic",
    )


def _find_failing_step(steps: List[dict], error_message: str) -> tuple[Optional[int], Optional[dict]]:
    """Best-effort: match the selector quoted in the error back to a step."""
    if not error_message:
        return None, None
    for idx, step in enumerate(steps or []):
        sel = step.get("selector") or ""
        if sel and sel in error_message:
            return idx, step
    return None, None


_LLM_SYSTEM = (
    "You are a test-failure analyst inside a CI system. Classify why an "
    "automated browser/API test failed. Respond with ONLY a JSON object, no "
    "markdown fences, matching exactly this schema:\n"
    "{\n"
    '  "root_cause_category": "app_bug" | "test_bug" | "environment" | "flake" | "unknown",\n'
    '  "summary": "one sentence, plain language",\n'
    '  "details": "2-4 sentences of reasoning grounded in the evidence",\n'
    '  "suggested_fix": "concrete next action, or null",\n'
    '  "fix_target": "app" | "test" | "infra" | "none",\n'
    '  "confidence": 0.0-1.0\n'
    "}\n"
    "app_bug = the application misbehaves; test_bug = the test encodes a wrong "
    "or stale expectation/selector; environment = deployment/network/infra; "
    "flake = transient, retry should pass."
)


def analyze_case_failure(
    *,
    test_name: str,
    error_message: str,
    steps: List[dict],
    response_status: Optional[int] = None,
    request_url: Optional[str] = None,
    network_failures: Optional[List[dict]] = None,
) -> FailureReport:
    failing_idx, failing_step = _find_failing_step(steps, error_message)
    evidence = FailureEvidence(
        error_message=(error_message or "")[:_MAX_ERROR_CHARS],
        failing_step_index=failing_idx,
        failing_step=failing_step,
        selector=(failing_step or {}).get("selector"),
        http_status=response_status,
        request_url=request_url,
        network_failures=(network_failures or [])[:10],
    )
    base = heuristic_report(error_message, evidence)

    if provider.name == "null":
        return base

    steps_json = json.dumps(steps or [])[:_MAX_STEPS_CHARS]
    prompt = (
        f"Test case: {test_name}\n"
        f"Error message:\n{evidence.error_message}\n\n"
        f"Test steps (JSON):\n{steps_json}\n\n"
        f"HTTP status observed: {response_status}\n"
        f"Request URL: {request_url}\n"
        f"Failed network requests: {json.dumps(evidence.network_failures)}\n\n"
        f"Heuristic first guess: {base.root_cause_category} — {base.summary}\n"
        "Classify the failure."
    )
    with llm_call_context(feature="failure_analysis"):
        raw = provider.complete(prompt, system=_LLM_SYSTEM, max_tokens=700)
    if not raw:
        return base

    try:
        cleaned = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        data = json.loads(cleaned)
        report = FailureReport(
            root_cause_category=data.get("root_cause_category", base.root_cause_category),
            summary=str(data.get("summary") or base.summary),
            details=str(data.get("details") or base.details),
            suggested_fix=data.get("suggested_fix"),
            fix_target=data.get("fix_target", base.fix_target),
            confidence=max(0.0, min(1.0, float(data.get("confidence", 0.6)))),
            evidence=evidence,
            analyzed_by=f"llm:{provider.name}",
        )
        return report
    except Exception as exc:  # noqa: BLE001 — malformed LLM output falls back
        print(f"[FailureAnalysis] LLM output invalid, using heuristic: {exc}")
        return base


def build_run_rollup(reports: List[FailureReport], failed_total: int) -> RunFailureAnalysis:
    categories: Dict[str, int] = {}
    for r in reports:
        categories[r.root_cause_category] = categories.get(r.root_cause_category, 0) + 1
    if not reports:
        summary = "No failures analyzed."
    else:
        dominant = max(categories, key=lambda k: categories[k])
        summary = (
            f"{failed_total} case(s) failed; {len(reports)} analyzed. "
            f"Dominant root cause: {dominant.replace('_', ' ')}. "
            + (reports[0].summary if len(reports) == 1 else "")
        ).strip()
    return RunFailureAnalysis(
        summary=summary,
        failed_cases_analyzed=len(reports),
        failed_cases_total=failed_total,
        categories=categories,
        reports=reports,
    )
