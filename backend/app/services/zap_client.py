"""OWASP ZAP client + alert mapping (PLATFORM_VISION.md P-4, item 6).

Thin wrapper over ZAP's JSON REST API (a `zap` daemon service) plus a pure
`map_alerts` that converts ZAP alerts into TraceIQ SecurityFinding dicts — the
same findings model the passive analyzer uses. The mapping is pure and
unit-tested; the HTTP client requires a running ZAP daemon (ZAP_API_URL).

ZAP is the embeddable open-source equivalent of Burp Scanner; we wrap it rather
than build our own scanner (see PLATFORM_VISION.md P-4 "On Burp").
"""
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import requests

# ZAP risk labels → TraceIQ severities.
_RISK_TO_SEVERITY = {
    "high": "high",
    "medium": "medium",
    "low": "low",
    "informational": "info",
    "info": "info",
}


def map_zap_alert(alert: Dict[str, Any]) -> Dict[str, Any]:
    """One ZAP alert → a SecurityFinding-shaped dict (scan_type set by caller).

    Carries ZAP's remediation-relevant fields (solution/reference/cwe/
    confidence/param/attack) alongside the base description so map_alerts can
    compose a report that's actually actionable, not just a title."""
    risk = str(alert.get("risk") or "").strip().lower()
    # ZAP sometimes suffixes confidence, e.g. "High (Medium)"; take the head.
    risk_head = risk.split()[0] if risk else ""
    severity = _RISK_TO_SEVERITY.get(risk_head, "info")
    title = alert.get("alert") or alert.get("name") or "ZAP alert"
    evidence = alert.get("evidence") or alert.get("param") or None
    return {
        "category": "dast",
        "severity": severity,
        "title": title,
        "description": (alert.get("description") or "").strip() or None,
        "evidence": (str(evidence)[:2000] if evidence else None),
        "target_url": alert.get("url") or None,
        # Enrichment — kept raw here, folded into the final description by
        # map_alerts (so no DB schema change is needed).
        "solution": (alert.get("solution") or "").strip() or None,
        "reference": (alert.get("reference") or "").strip() or None,
        "cweid": _clean_id(alert.get("cweid")),
        "wascid": _clean_id(alert.get("wascid")),
        "confidence": (alert.get("confidence") or "").strip() or None,
        "param": (alert.get("param") or "").strip() or None,
        "attack": (alert.get("attack") or "").strip() or None,
        "otherinfo": (alert.get("otherinfo") or "").strip() or None,
    }


def _clean_id(v: Any) -> Optional[str]:
    """ZAP uses "-1"/"" for 'not applicable'; drop those."""
    s = str(v or "").strip()
    return s if s and s not in ("-1", "0") else None


def _compose_description(f: Dict[str, Any], urls: List[str]) -> str:
    """Build a readable, actionable finding body from an alert + affected URLs."""
    parts: List[str] = []
    if f.get("description"):
        parts.append(f["description"])

    meta = []
    if f.get("confidence"):
        meta.append(f"Confidence: {f['confidence']}")
    if f.get("cweid"):
        meta.append(f"CWE-{f['cweid']}")
    if f.get("wascid"):
        meta.append(f"WASC-{f['wascid']}")
    if meta:
        parts.append(" · ".join(meta))

    if f.get("param"):
        parts.append(f"**Parameter:** `{f['param']}`")
    if f.get("attack"):
        parts.append(f"**Attack:** `{f['attack'][:500]}`")
    if f.get("otherinfo"):
        parts.append(f["otherinfo"])
    if f.get("solution"):
        parts.append(f"**Remediation:** {f['solution']}")
    if f.get("reference"):
        refs = [r for r in f["reference"].splitlines() if r.strip()][:5]
        if refs:
            parts.append("**References:**\n" + "\n".join(f"- {r.strip()}" for r in refs))

    if len(urls) > 1:
        sample = "\n".join(f"- {u}" for u in urls[:10])
        more = f"\n…and {len(urls) - 10} more" if len(urls) > 10 else ""
        parts.append(f"**Affected locations ({len(urls)}):**\n{sample}{more}")

    return "\n\n".join(p for p in parts if p)


def map_alerts(alerts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map + de-duplicate alerts by (severity, title, origin).

    Instances of the same alert across many URLs collapse into one finding, but
    the affected-URL list and count are preserved in the description so the
    report shows breadth of impact instead of hiding it."""
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    grouped: Dict[Any, Dict[str, Any]] = {}
    url_map: Dict[Any, List[str]] = {}
    for a in alerts or []:
        f = map_zap_alert(a)
        origin = _origin(f["target_url"])
        key = (f["severity"], f["title"], origin)
        if key not in grouped:
            grouped[key] = f
            url_map[key] = []
        url = f.get("target_url")
        if url and url not in url_map[key]:
            url_map[key].append(url)

    out: List[Dict[str, Any]] = []
    for key, f in grouped.items():
        urls = url_map[key]
        out.append({
            "category": f["category"],
            "severity": f["severity"],
            "title": f["title"],
            "description": _compose_description(f, urls) or None,
            "evidence": f["evidence"],
            "target_url": f["target_url"],
        })
    out.sort(key=lambda f: order.get(f["severity"], 9))
    return out


def _origin(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        p = urlsplit(url)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    except ValueError:
        pass
    return url


def cookie_header_from_storage_state(storage_state: Optional[dict]) -> Optional[str]:
    """Build a `name=value; …` Cookie header from a Playwright storageState so
    ZAP can scan behind login — the authenticated-scan differentiator."""
    if not storage_state or not isinstance(storage_state, dict):
        return None
    cookies = storage_state.get("cookies") or []
    pairs = [f"{c['name']}={c['value']}" for c in cookies
             if isinstance(c, dict) and c.get("name") and c.get("value") is not None]
    return "; ".join(pairs) or None


class ZapError(Exception):
    pass


class ZapClient:
    """Minimal ZAP JSON-API client. All calls raise ZapError on transport
    failure so the scan task can mark the scan errored."""

    def __init__(self, base_url: str, api_key: str = "", timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _get(self, path: str, **params) -> Dict[str, Any]:
        params["apikey"] = self.api_key
        try:
            r = requests.get(f"{self.base_url}/JSON/{path}", params=params, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 - normalise all transport errors
            raise ZapError(f"ZAP {path} failed: {e}") from e

    # --- authenticated scanning ---
    def add_header_rule(self, name: str, value: str, description: str = "traceiq-auth") -> None:
        """Inject a request header on every ZAP request (a 'replacer' rule).
        Used for both cookie-session auth and header/token auth (Authorization
        bearer, X-API-Key, …)."""
        self._get("replacer/action/addRule/", description=description,
                  enabled="true", matchType="REQ_HEADER", matchString=name,
                  matchRegex="false", replacement=value)

    def add_cookie_header(self, cookie: str) -> None:
        """Inject a Cookie request header on every ZAP request (session auth)."""
        self.add_header_rule("Cookie", cookie, description="traceiq-auth-cookie")

    # --- API import (OpenAPI / Swagger) ---
    def import_openapi(self, spec_url: str, host_override: Optional[str] = None) -> None:
        """Import an OpenAPI/Swagger spec by URL so its endpoints enter ZAP's
        site tree and get scanned even when nothing links to them."""
        params: Dict[str, Any] = {"url": spec_url}
        if host_override:
            params["hostOverride"] = host_override
        self._get("openapi/action/importUrl/", **params)

    # --- spider (crawl) ---
    def spider(self, target: str, max_children: int = 0, max_depth: int = 0) -> str:
        """Start the traditional (HTML link) spider. max_children/max_depth=0
        mean 'ZAP default'; pass positive values to widen or bound the crawl."""
        params: Dict[str, Any] = {"url": target, "recurse": "true"}
        if max_children:
            params["maxChildren"] = max_children
        return str(self._get("spider/action/scan/", **params).get("scan"))

    def spider_status(self, scan_id: str) -> int:
        return int(self._get("spider/view/status/", scanId=scan_id).get("status", 0))

    def set_spider_max_depth(self, depth: int) -> None:
        """Best-effort: widen the crawl depth (default 5) for large sites."""
        if depth > 0:
            self._get("spider/action/setOptionMaxDepth/", Integer=depth)

    # --- AJAX spider (real browser; crawls JS-rendered / SPA content) ---
    def ajax_spider(self, target: str) -> None:
        """Start the AJAX spider — needed for JS-rendered sites the HTML spider
        can't see. Status is a string ('running'/'stopped'), not a percentage."""
        self._get("ajaxSpider/action/scan/", url=target, inScope="false")

    def ajax_spider_running(self) -> bool:
        status = str(self._get("ajaxSpider/view/status/").get("status", "stopped"))
        return status.lower() == "running"

    # --- passive scan queue ---
    def passive_records_to_scan(self) -> int:
        return int(self._get("pscan/view/recordsToScan/").get("recordsToScan", 0))

    # --- active scan (attacking; gated) ---
    def active_scan(self, target: str) -> str:
        return str(self._get("ascan/action/scan/", url=target).get("scan"))

    def active_scan_status(self, scan_id: str) -> int:
        return int(self._get("ascan/view/status/", scanId=scan_id).get("status", 0))

    # --- results ---
    def alerts(self, baseurl: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {}
        if baseurl:
            params["baseurl"] = baseurl
        return self._get("core/view/alerts/", **params).get("alerts", [])
