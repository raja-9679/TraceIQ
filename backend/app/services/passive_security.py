"""Passive security analysis (PLATFORM_VISION.md P-4, phase 1).

Zero-risk, zero-new-infra security checks that piggyback on data a normal run
*already captured* — no active scanning, no traffic to the target. We inspect
the HTTP responses recorded during execution and flag missing/weak security
headers, insecure cookies, information disclosure, and plaintext transport.

Primary source: `TestRun.network_events` — each browser request records
`responseHeaders` (lowercase keys), `resourceType`, `status`, and `url`. We
focus on `resourceType == "document"` (main navigations) to keep findings
high-signal rather than flagging every asset. For API/http-request runs we also
inspect the run-level `response_headers` + `request_url`.

Pure functions only (no DB, no I/O) so the rules are trivially testable. The
task/endpoint layer persists the returned dicts as `SecurityFinding` rows —
the same findings model the ZAP/nuclei executors (phases 2–4) will reuse.
"""
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def finding_fingerprint(category: str, title: str, target_url: Optional[str]) -> str:
    """Stable identity for 'the same logical finding' across scans: category +
    title + host/path (query stripped, so volatile params don't split it)."""
    import hashlib
    loc = ""
    if target_url:
        try:
            parts = urlsplit(target_url)
            loc = f"{parts.netloc}{parts.path}"
        except ValueError:
            loc = target_url
    raw = f"{category}|{title}|{loc}".lower()
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _origin(url: Optional[str]) -> Optional[str]:
    """scheme://host for a URL, used to dedupe per-origin header findings so a
    site with several page loads reports each missing header once, not per URL.
    Distinct origins (e.g. a third-party host) stay separate."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    except ValueError:
        pass
    return url


def _finding(category: str, severity: str, title: str, description: str,
             evidence: str, target_url: Optional[str]) -> Dict[str, Any]:
    return {
        "category": category,
        "severity": severity,
        "title": title,
        "description": description,
        "evidence": evidence,
        "target_url": target_url,
    }


def _analyze_response(url: Optional[str], headers: Dict[str, str],
                      set_cookie: Optional[str]) -> List[Dict[str, Any]]:
    """Rules for a single response. `headers` keys must be lowercased."""
    out: List[Dict[str, Any]] = []
    h = {(k or "").lower(): (v or "") for k, v in (headers or {}).items()}
    is_https = bool(url) and url.lower().startswith("https://")

    if url and url.lower().startswith("http://"):
        out.append(_finding(
            "insecure-transport", "high",
            "Page served over plaintext HTTP",
            "Traffic is unencrypted and open to interception/tampering. Serve over HTTPS and redirect HTTP to HTTPS.",
            url, url))

    if "content-security-policy" not in h:
        out.append(_finding(
            "missing-header", "medium",
            "Missing Content-Security-Policy",
            "No CSP header. CSP is the primary defense against XSS and data injection.",
            "response has no content-security-policy header", url))

    # HSTS only matters over HTTPS.
    if is_https and "strict-transport-security" not in h:
        out.append(_finding(
            "missing-header", "medium",
            "Missing Strict-Transport-Security (HSTS)",
            "Without HSTS, clients can be downgraded to HTTP via man-in-the-middle.",
            "response has no strict-transport-security header", url))

    xcto = h.get("x-content-type-options", "")
    if xcto.strip().lower() != "nosniff":
        out.append(_finding(
            "missing-header", "low",
            "Missing/weak X-Content-Type-Options",
            "Set 'X-Content-Type-Options: nosniff' to stop MIME-type sniffing.",
            f"x-content-type-options={xcto!r}" if xcto else "header absent", url))

    csp = h.get("content-security-policy", "")
    has_frame_ancestors = "frame-ancestors" in csp.lower()
    if "x-frame-options" not in h and not has_frame_ancestors:
        out.append(_finding(
            "missing-header", "medium",
            "No clickjacking protection",
            "Neither X-Frame-Options nor CSP frame-ancestors is set; the page can be framed for clickjacking.",
            "no x-frame-options and no CSP frame-ancestors", url))

    if "referrer-policy" not in h:
        out.append(_finding(
            "missing-header", "low",
            "Missing Referrer-Policy",
            "Set a Referrer-Policy to avoid leaking full URLs to third parties.",
            "response has no referrer-policy header", url))

    if "permissions-policy" not in h:
        out.append(_finding(
            "missing-header", "info",
            "Missing Permissions-Policy",
            "Consider a Permissions-Policy to restrict powerful browser features.",
            "response has no permissions-policy header", url))

    # Information disclosure.
    server = h.get("server", "")
    if any(ch.isdigit() for ch in server):
        out.append(_finding(
            "info-disclosure", "low",
            "Server version disclosed",
            "The Server header reveals software/version, easing targeted attacks. Suppress or genericize it.",
            f"server={server!r}", url))
    if "x-powered-by" in h:
        out.append(_finding(
            "info-disclosure", "low",
            "X-Powered-By disclosed",
            "X-Powered-By reveals the tech stack. Remove it.",
            f"x-powered-by={h.get('x-powered-by')!r}", url))

    # Cookie flags (best-effort: Playwright may flatten set-cookie into one string).
    cookie = set_cookie or h.get("set-cookie", "")
    if cookie:
        low = cookie.lower()
        missing = [flag for flag, tok in
                   (("Secure", "secure"), ("HttpOnly", "httponly"), ("SameSite", "samesite"))
                   if tok not in low]
        if missing:
            out.append(_finding(
                "insecure-cookie", "medium",
                f"Cookie missing {', '.join(missing)}",
                "Set Secure, HttpOnly and SameSite on session cookies to reduce theft/CSRF risk.",
                cookie[:200], url))

    return out


def analyze_run(run) -> List[Dict[str, Any]]:
    """Analyze a completed TestRun. Returns de-duplicated finding dicts sorted
    by severity (high first)."""
    findings: List[Dict[str, Any]] = []
    seen = set()

    def add(items):
        for f in items:
            # Group per-origin: the same missing header across several pages of
            # one site is one finding. The stored target_url is the origin.
            origin = _origin(f["target_url"])
            key = (origin, f["category"], f["title"])
            if key not in seen:
                seen.add(key)
                f["target_url"] = origin or f["target_url"]
                findings.append(f)

    # 1. Browser document responses from network_events.
    events = getattr(run, "network_events", None) or []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("resourceType") != "document":
            continue
        resp_headers = ev.get("responseHeaders")
        if not resp_headers:
            continue
        add(_analyze_response(ev.get("url"), resp_headers, None))

    # 2. API / http-request run-level response.
    run_headers = getattr(run, "response_headers", None)
    if run_headers:
        add(_analyze_response(getattr(run, "request_url", None), run_headers, None))

    findings.sort(key=lambda f: SEVERITY_ORDER.get(f["severity"], 9))
    return findings


def summarize(findings: List[Dict[str, Any]]) -> Dict[str, int]:
    """Counts per severity for a quick headline."""
    counts = {"high": 0, "medium": 0, "low": 0, "info": 0}
    for f in findings:
        sev = f.get("severity")
        if sev in counts:
            counts[sev] += 1
    return counts
