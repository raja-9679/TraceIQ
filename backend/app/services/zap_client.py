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
    """One ZAP alert → a SecurityFinding-shaped dict (scan_type set by caller)."""
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
        "description": alert.get("description") or None,
        "evidence": (str(evidence)[:2000] if evidence else None),
        "target_url": alert.get("url") or None,
    }


def map_alerts(alerts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map + de-duplicate alerts by (severity, title, origin)."""
    out: List[Dict[str, Any]] = []
    seen = set()
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    for a in alerts or []:
        f = map_zap_alert(a)
        origin = _origin(f["target_url"])
        key = (f["severity"], f["title"], origin)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
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
    def add_cookie_header(self, cookie: str) -> None:
        """Inject a Cookie request header on every ZAP request (session auth)."""
        self._get("replacer/action/addRule/", description="traceiq-auth",
                  enabled="true", matchType="REQ_HEADER", matchString="Cookie",
                  matchRegex="false", replacement=cookie)

    # --- spider (crawl) ---
    def spider(self, target: str) -> str:
        return str(self._get("spider/action/scan/", url=target).get("scan"))

    def spider_status(self, scan_id: str) -> int:
        return int(self._get("spider/view/status/", scanId=scan_id).get("status", 0))

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
