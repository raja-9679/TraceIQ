"""Page performance / SEO budgets (WP10): ingest `check-page-budget` samples.

The worker step (execution-engine/src/core/frontend-quality/page-budget.ts)
measures Core Web Vitals, page weight and SEO checks for the current page and
compares them with the step's thresholds. Each outcome rides on the job
result as `page_budgets`; the aggregator stores one PageBudgetSample per
entry so a page's numbers can be trended over runs.

Like step evidence, the list can come from a local worker (any API-key
holder), so it is rebuilt key by key, capped and redacted — never stored as
sent.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from app.services.redaction import redact_text

MAX_SAMPLES_PER_RESULT = 20
METRIC_KEYS = (
    "lcp_ms", "cls", "inp_ms", "fcp_ms", "ttfb_ms", "dom_content_loaded_ms", "load_ms",
    "page_weight_kb", "request_count", "image_weight_kb", "largest_image_kb",
    "script_weight_kb", "third_party_requests",
)
SEO_CHECKS = (
    "title", "meta_description", "canonical", "robots", "h1", "img_alt", "lang",
    "hreflang", "structured_data", "viewport_meta",
)
_SEVERITIES = {"error", "warning", "info"}


def page_key(url: str) -> str:
    """origin + path: query strings (session ids, tracking) would split one
    page into a trend per visit."""
    try:
        parts = urlsplit(url)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}{parts.path or '/'}"[:500]
    except ValueError:
        pass
    return url.split("?")[0].split("#")[0][:500]


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return round(float(value), 4)


def _text(value: Any, limit: int) -> Optional[str]:
    if value is None:
        return None
    return redact_text(str(value)[:limit])


def normalise_samples(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw[:MAX_SAMPLES_PER_RESULT]:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str) or not item["url"]:
            continue
        url = redact_text(item["url"][:2000])
        metrics = {}
        for key in METRIC_KEYS:
            v = _num((item.get("metrics") or {}).get(key)) if isinstance(item.get("metrics"), dict) else None
            if v is not None:
                metrics[key] = v
        checks = []
        for c in (item.get("checks") or [])[:40] if isinstance(item.get("checks"), list) else []:
            if not isinstance(c, dict) or c.get("metric") not in METRIC_KEYS:
                continue
            checks.append({
                "metric": c["metric"],
                "value": _num(c.get("value")),
                "budget": _num(c.get("budget")),
                "passed": bool(c.get("passed")),
            })
        seo = []
        for c in (item.get("seo") or [])[:40] if isinstance(item.get("seo"), list) else []:
            if not isinstance(c, dict) or c.get("check") not in SEO_CHECKS:
                continue
            seo.append({
                "check": c["check"],
                "passed": bool(c.get("passed")),
                "severity": c.get("severity") if c.get("severity") in _SEVERITIES else "warning",
                "value": _text(c.get("value"), 300),
                "detail": _text(c.get("detail"), 500),
            })
        out.append({
            "step_id": _text(item.get("step_id"), 100),
            "page_url": url,
            "page_key": page_key(url),
            "metrics": metrics,
            "checks": checks,
            "seo": seo,
            # Re-derived, not trusted: failed if any budget check failed.
            "passed": all(c["passed"] for c in checks) and bool(item.get("passed", True)),
        })
    return out


def persist_samples(session, run, test_case_id: Optional[int], test_name: Optional[str],
                    raw: Any) -> int:
    """Replace this (run, test) pair's samples with the normalised `raw`.

    Replace rather than append: a result can be re-processed (redelivered
    stream message, retried test) and the trend must count it once."""
    from sqlmodel import delete
    from app.models import PageBudgetSample

    samples = normalise_samples(raw)
    if not run or not run.project_id:
        return 0
    session.exec(delete(PageBudgetSample).where(
        PageBudgetSample.test_run_id == run.id,
        PageBudgetSample.test_name == test_name))
    variant = (run.emulation or {}).get("label") if isinstance(run.emulation, dict) else None
    for s in samples:
        session.add(PageBudgetSample(
            project_id=run.project_id, test_run_id=run.id,
            test_case_id=int(test_case_id) if test_case_id else None,
            test_name=test_name, variant=variant, **s))
    return len(samples)
