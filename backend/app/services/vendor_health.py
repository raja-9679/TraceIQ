"""Third-party dependency health (WP12b, roadmap B9) — pure logic.

Source: `TestRun.network_events`, one entry per response the browser got
(execution-engine/src/core/network-interceptor.ts: url, status, duration,
resourceType, testCaseId). Requests that never got a response are not in that
array, so "errors" here are HTTP errors (status >= 400, or no status).

A request is third-party when its registrable domain (`script_inventory.site_of`)
differs from the run's own sites: the hosts of the run's top-level documents
(first `document` response per test case), its target_url and its
environment's base_url. A vendor is that registrable domain, with a display
name from a small curated map.

Nothing here keeps a path or query string: only hosts leave this module.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlsplit

from app.services.script_inventory import site_of

# Upper bounds (ms) of the latency histogram buckets; one overflow bucket
# follows. Bucketed so daily rows merge into window percentiles.
LATENCY_BUCKETS = [25, 50, 100, 200, 300, 500, 750, 1000, 1500, 2000, 3000, 5000, 10000, 30000]
MAX_HOSTS = 20

VENDOR_NAMES = {
    "google-analytics.com": "Google Analytics",
    "googletagmanager.com": "Google Tag Manager",
    "doubleclick.net": "Google Ads (DoubleClick)",
    "googlesyndication.com": "Google AdSense",
    "googleapis.com": "Google APIs",
    "gstatic.com": "Google static content",
    "youtube.com": "YouTube",
    "facebook.net": "Meta (Facebook) SDK",
    "facebook.com": "Meta (Facebook)",
    "stripe.com": "Stripe",
    "stripe.network": "Stripe",
    "paypal.com": "PayPal",
    "razorpay.com": "Razorpay",
    "hotjar.com": "Hotjar",
    "segment.com": "Segment",
    "segment.io": "Segment",
    "sentry.io": "Sentry",
    "intercom.io": "Intercom",
    "intercomcdn.com": "Intercom",
    "cloudflare.com": "Cloudflare",
    "jsdelivr.net": "jsDelivr CDN",
    "unpkg.com": "unpkg CDN",
    "nr-data.net": "New Relic",
    "newrelic.com": "New Relic",
    "datadoghq.com": "Datadog",
    "browser-intake-datadoghq.com": "Datadog",
    "clarity.ms": "Microsoft Clarity",
    "linkedin.com": "LinkedIn",
    "licdn.com": "LinkedIn",
    "twitter.com": "X (Twitter)",
    "x.com": "X (Twitter)",
    "tiktok.com": "TikTok",
    "hubspot.com": "HubSpot",
    "hs-scripts.com": "HubSpot",
    "optimizely.com": "Optimizely",
    "launchdarkly.com": "LaunchDarkly",
    "mixpanel.com": "Mixpanel",
    "amplitude.com": "Amplitude",
    "onetrust.com": "OneTrust",
    "cookielaw.org": "OneTrust",
    "recaptcha.net": "reCAPTCHA",
    "akamaihd.net": "Akamai",
    "cloudfront.net": "Amazon CloudFront",
    "amazonaws.com": "Amazon Web Services",
}


def vendor_name(vendor: str) -> str:
    return VENDOR_NAMES.get(vendor, vendor)


def host_of(url: Any) -> Optional[str]:
    """Lower-cased host of an absolute http(s) URL; None otherwise. Userinfo,
    port, path, query and fragment are all dropped."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url.strip())
        if parts.scheme not in ("http", "https"):
            return None
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    return host or None


def strip_url(url: Any) -> Optional[str]:
    """scheme://host/path with no userinfo, port, query or fragment."""
    host = host_of(url)
    if not host:
        return None
    parts = urlsplit(url.strip())
    return f"{parts.scheme}://{host}{parts.path or '/'}"


def bucket_index(ms: float) -> int:
    for i, bound in enumerate(LATENCY_BUCKETS):
        if ms <= bound:
            return i
    return len(LATENCY_BUCKETS)


def hist_percentile(hist: Optional[List[int]], q: float) -> Optional[float]:
    """Upper bound of the bucket holding the q-th percentile. Approximate by
    design (the price of mergeable daily rows); the overflow bucket reports
    the last bound."""
    if not hist:
        return None
    total = sum(hist)
    if total <= 0:
        return None
    target = q / 100.0 * total
    seen = 0
    for i, n in enumerate(hist):
        seen += n
        if seen >= target and n:
            return float(LATENCY_BUCKETS[min(i, len(LATENCY_BUCKETS) - 1)])
    return float(LATENCY_BUCKETS[-1])


def merge_hist(a: Optional[List[int]], b: Optional[List[int]]) -> List[int]:
    size = len(LATENCY_BUCKETS) + 1
    a = list(a or []) + [0] * (size - len(a or []))
    b = list(b or []) + [0] * (size - len(b or []))
    return [x + y for x, y in zip(a[:size], b[:size])]


def is_error(event: dict) -> bool:
    status = event.get("status")
    try:
        status = int(status)
    except (TypeError, ValueError):
        return True
    return status >= 400 or status <= 0


def first_party_sites(events: Iterable[dict], extra_urls: Iterable[Optional[str]] = ()) -> Set[str]:
    sites: Set[str] = set()
    for u in extra_urls:
        h = host_of(u)
        if h:
            sites.add(site_of(h))
    seen_cases: Set[Any] = set()
    first_host: Optional[str] = None
    hosts: Counter = Counter()
    for e in events:
        if not isinstance(e, dict):
            continue
        h = host_of(e.get("url"))
        if not h:
            continue
        hosts[site_of(h)] += 1
        if first_host is None:
            first_host = h
        if e.get("resourceType") == "document":
            key = e.get("testCaseId") or e.get("testCaseName")
            if key not in seen_cases:
                seen_cases.add(key)
                sites.add(site_of(h))
    if not sites and hosts:
        # No navigation recorded (e.g. API-only steps): the busiest site is
        # the app under test.
        sites.add(hosts.most_common(1)[0][0])
    return sites


@dataclass
class VendorRun:
    requests: int = 0
    errors: int = 0
    hist: List[int] = field(default_factory=lambda: [0] * (len(LATENCY_BUCKETS) + 1))
    hosts: Counter = field(default_factory=Counter)


def vendors_in_run(events: Optional[List[dict]], extra_urls: Iterable[Optional[str]] = ()) -> Dict[str, VendorRun]:
    events = [e for e in (events or []) if isinstance(e, dict)]
    own = first_party_sites(events, extra_urls)
    out: Dict[str, VendorRun] = {}
    for e in events:
        h = host_of(e.get("url"))
        if not h:
            continue
        site = site_of(h)
        if site in own:
            continue
        v = out.setdefault(site, VendorRun())
        v.requests += 1
        v.hosts[h] += 1
        if is_error(e):
            v.errors += 1
        try:
            dur = float(e.get("duration"))
        except (TypeError, ValueError):
            dur = None
        if dur is not None and dur >= 0:
            v.hist[bucket_index(dur)] += 1
    return out


@dataclass
class VendorDay:
    requests: int = 0
    errors: int = 0
    hist: List[int] = field(default_factory=lambda: [0] * (len(LATENCY_BUCKETS) + 1))
    hosts: Counter = field(default_factory=Counter)
    runs: int = 0
    runs_with_errors: int = 0
    failed_results: int = 0
    failed_results_vendor_errors: int = 0
    passed_results: int = 0
    passed_results_vendor_errors: int = 0


def add_run(day: Dict[str, VendorDay], vendors: Dict[str, VendorRun],
            passed_results: int, failed_results: int) -> None:
    """Fold one run's vendors and its result outcome counts into a day."""
    for name, v in vendors.items():
        d = day.setdefault(name, VendorDay())
        d.requests += v.requests
        d.errors += v.errors
        d.hist = merge_hist(d.hist, v.hist)
        d.hosts.update(v.hosts)
        d.runs += 1
        had_err = v.errors > 0
        d.runs_with_errors += 1 if had_err else 0
        d.failed_results += failed_results
        d.passed_results += passed_results
        if had_err:
            d.failed_results_vendor_errors += failed_results
            d.passed_results_vendor_errors += passed_results


def row_values(d: VendorDay) -> Dict[str, Any]:
    return {
        "requests": d.requests, "errors": d.errors, "latency_hist": d.hist,
        "latency_p50_ms": hist_percentile(d.hist, 50),
        "latency_p95_ms": hist_percentile(d.hist, 95),
        "hosts": [h for h, _ in d.hosts.most_common(MAX_HOSTS)],
        "runs": d.runs, "runs_with_errors": d.runs_with_errors,
        "failed_results": d.failed_results,
        "failed_results_vendor_errors": d.failed_results_vendor_errors,
        "passed_results": d.passed_results,
        "passed_results_vendor_errors": d.passed_results_vendor_errors,
    }


def _share(part: int, whole: int) -> Optional[float]:
    return round(100.0 * part / whole, 1) if whole else None


def _lift(failed_err: int, failed: int, passed_err: int,
          passed: int) -> Tuple[Optional[float], bool]:
    """(lift, is_lower_bound). When failing results saw errors and NO passing
    result did, the plain ratio divides by zero — yet that is the strongest
    signal there is, so it must not read as "not enough data". Credit the
    passing side with half an error instead (the usual continuity correction):
    the result is a floor on the real ratio, and grows with the evidence."""
    if not failed or not passed:
        return None, False
    if passed_err == 0:
        if failed_err == 0:
            return None, False
        return round((failed_err / failed) / (0.5 / passed), 2), True
    return round((failed_err / failed) / (passed_err / passed), 2), False


def summarise(rows: Iterable[Any]) -> List[Dict[str, Any]]:
    """Merge daily rows (VendorHealthDaily-like objects) into one entry per
    vendor with a daily series, sorted by errors then requests."""
    by_vendor: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        v = by_vendor.setdefault(r.vendor, {
            "vendor": r.vendor, "name": vendor_name(r.vendor), "requests": 0, "errors": 0,
            "hist": None, "hosts": [], "runs": 0, "runs_with_errors": 0,
            "failed_results": 0, "failed_results_vendor_errors": 0,
            "passed_results": 0, "passed_results_vendor_errors": 0, "daily": [],
        })
        for k in ("requests", "errors", "runs", "runs_with_errors", "failed_results",
                  "failed_results_vendor_errors", "passed_results", "passed_results_vendor_errors"):
            v[k] += int(getattr(r, k) or 0)
        v["hist"] = merge_hist(v["hist"], r.latency_hist)
        for h in r.hosts or []:
            if h not in v["hosts"] and len(v["hosts"]) < MAX_HOSTS:
                v["hosts"].append(h)
        v["daily"].append({
            "date": r.day.isoformat() if hasattr(r.day, "isoformat") else str(r.day),
            "requests": r.requests, "errors": r.errors,
            "error_rate": _share(r.errors, r.requests),
            "p95_ms": r.latency_p95_ms,
        })
    out = []
    for v in by_vendor.values():
        hist = v.pop("hist")
        v["daily"].sort(key=lambda d: d["date"])
        v["error_rate"] = _share(v["errors"], v["requests"])
        v["latency_p50_ms"] = hist_percentile(hist, 50)
        v["latency_p95_ms"] = hist_percentile(hist, 95)
        failed_share = _share(v["failed_results_vendor_errors"], v["failed_results"])
        passed_share = _share(v["passed_results_vendor_errors"], v["passed_results"])
        lift, lower_bound = _lift(v["failed_results_vendor_errors"], v["failed_results"],
                                  v["passed_results_vendor_errors"], v["passed_results"])
        v["correlation"] = {
            "failed_results": v["failed_results"],
            "failed_with_vendor_errors_pct": failed_share,
            "passed_results": v["passed_results"],
            "passed_with_vendor_errors_pct": passed_share,
            # How much more often failing results saw this vendor erroring than
            # passing ones did. None when either side has no results, or when
            # no result on either side saw an error (nothing to compare).
            "lift": lift,
            # True when no passing result saw an error: the ratio is then
            # infinite, so `lift` is a conservative "at least" figure.
            "lift_is_lower_bound": lower_bound,
        }
        out.append(v)
    out.sort(key=lambda v: (v["errors"], v["requests"]), reverse=True)
    return out


def run_outcomes(statuses: Iterable[Tuple[str, int]]) -> Tuple[int, int]:
    """(passed, failed) result counts from (status, count) pairs."""
    passed = failed = 0
    for status, count in statuses:
        s = str(getattr(status, "value", status)).lower()
        if s == "passed":
            passed += int(count)
        elif s in ("failed", "error"):
            failed += int(count)
    return passed, failed
