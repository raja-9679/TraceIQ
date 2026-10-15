"""Incident ingestion: payload adapters, webhook authentication, recurrence
fingerprints and matching against existing failures/cases. Pure functions —
the API layer (app/api/incidents.py) does the I/O.

Webhook authentication is HMAC-SHA256 over the raw request body with the
project's secret, accepted in either of two headers:

* ``X-TraceIQ-Signature: sha256=<hex>`` — the same scheme TraceIQ uses to sign
  its own outbound webhooks, for generic senders and relays;
* ``X-PagerDuty-Signature: v1=<hex>[,v1=<hex>]`` — PagerDuty v3 webhook
  subscriptions sign this way natively (several signatures during a secret
  rotation, any one may match). Configure the subscription's secret as the
  project secret.

Opsgenie's outgoing webhook integration cannot compute a signature — it can
only attach static headers — so ``X-TraceIQ-Token: <secret>`` is accepted as
the third mode. It is the same secret and constant-time compared, but it is a
bearer token rather than a body signature, so prefer the HMAC modes where the
sender supports them.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.services.tracker_import import parse_dt, safe_link

SEVERITIES = ("critical", "high", "medium", "low", "unknown")
STATUSES = ("open", "acknowledged", "resolved")


class IncidentPayloadError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_request(secret: str, body: bytes, headers: Dict[str, str]) -> bool:
    """True when any supported auth header proves knowledge of `secret`."""
    if not secret:
        return False
    h = {k.lower(): v for k, v in headers.items()}
    expected = sign(secret, body)

    sig = (h.get("x-traceiq-signature") or "").strip()
    if sig:
        value = sig.split("=", 1)[1] if sig.lower().startswith("sha256=") else sig
        if hmac.compare_digest(value.strip().lower(), expected):
            return True

    pd = h.get("x-pagerduty-signature") or ""
    for part in pd.split(","):
        part = part.strip()
        if part.startswith("v1=") and hmac.compare_digest(part[3:].lower(), expected):
            return True

    token = (h.get("x-traceiq-token") or "").strip()
    if token and hmac.compare_digest(token.encode("utf-8"), secret.encode("utf-8")):
        return True
    return False


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def normalise_severity(value: Any) -> str:
    v = str(value or "").strip().lower()
    if not v:
        return "unknown"
    table = {
        "p1": "critical", "sev1": "critical", "sev-1": "critical", "sev 1": "critical",
        "critical": "critical", "crit": "critical", "s1": "critical", "1": "critical",
        "p2": "high", "sev2": "high", "sev-2": "high", "sev 2": "high", "high": "high",
        "major": "high", "error": "high", "s2": "high", "2": "high",
        "p3": "medium", "sev3": "medium", "sev-3": "medium", "sev 3": "medium",
        "medium": "medium", "moderate": "medium", "warning": "medium", "s3": "medium", "3": "medium",
        "p4": "low", "p5": "low", "sev4": "low", "sev5": "low", "sev-4": "low", "low": "low",
        "minor": "low", "info": "low", "informational": "low", "s4": "low", "4": "low", "5": "low",
    }
    return table.get(v, "unknown")


def normalise_status(value: Any) -> str:
    v = str(value or "").strip().lower()
    if v in ("resolved", "closed", "close", "done", "fixed", "incident.resolved"):
        return "resolved"
    if v in ("acknowledged", "acknowledge", "ack", "investigating", "incident.acknowledged"):
        return "acknowledged"
    return "open"


_NOISE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"   # uuid
    r"|\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"                              # ip[:port]
    r"|\b\d{4}-\d{2}-\d{2}(?:[t ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?z?)?\b"  # dates
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\b"                                       # times
    r"|\b(?=[0-9a-f]*\d)[0-9a-f]{7,}\b"                                    # hashes/hex ids
    r"|\b\d+(?:\.\d+)?\s*(?:ms|s|%|mb|gb)?\b",                             # numbers
    re.I)
_STOP = {"the", "a", "an", "of", "on", "in", "for", "to", "is", "are", "and", "or",
         "at", "with", "from", "by", "be", "was", "were", "has", "have"}


def normalise_title(title: str) -> str:
    """Lowercase, numbers/ids/timestamps removed, punctuation folded.

    "Checkout API 503 for 12% of requests at 10:42" and "checkout api 502 for
    40% of requests" both become "checkout api for of requests" — the same
    outage, as far as recurrence is concerned."""
    t = (title or "").lower()
    t = re.sub(r"^\s*\[?(?:firing|resolved|alert|incident|triggered)(?::\s*\d+)?\]?:?\s*", "", t)
    t = re.sub(r"^#?\d+\s*[:\-]\s*", "", t)
    t = _NOISE.sub(" ", t)
    t = re.sub(r"[^a-z]+", " ", t)
    return " ".join(w for w in t.split() if w)


def fingerprint(service: Optional[str], title: str) -> str:
    key = f"{(service or '').strip().lower()}|{normalise_title(title)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def keywords(text: str) -> set:
    return {w for w in normalise_title(text).split() if len(w) >= 4 and w not in _STOP}


# ---------------------------------------------------------------------------
# Payload adapters → one normalised dict
# ---------------------------------------------------------------------------

def _epoch_ms(v: Any) -> Optional[datetime]:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n > 1e12:
        n /= 1000.0
    return datetime.fromtimestamp(n, tz=timezone.utc).replace(tzinfo=None)


def _str(v: Any, limit: int = 500) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s[:limit] if s else None


def detect_source(payload: dict) -> str:
    event = payload.get("event")
    if isinstance(event, dict) and isinstance(event.get("data"), dict) and \
            str(event.get("event_type") or "").startswith("incident."):
        return "pagerduty"
    if isinstance(payload.get("alert"), dict) and "action" in payload:
        return "opsgenie"
    return "generic"


def from_pagerduty(payload: dict) -> Dict[str, Any]:
    """PagerDuty v3 webhook (`{"event": {"event_type": "incident.*", "data": {...}}}`)."""
    event = payload.get("event") or {}
    data = event.get("data") or {}
    if data.get("type") not in (None, "incident"):
        raise IncidentPayloadError("PagerDuty event is not about an incident")
    etype = str(event.get("event_type") or "")
    status = data.get("status") or etype
    priority = (data.get("priority") or {}).get("summary") if isinstance(data.get("priority"), dict) else None
    severity = normalise_severity(priority) if priority else \
        ("high" if data.get("urgency") == "high" else "medium" if data.get("urgency") == "low" else "unknown")
    started = parse_dt(data.get("created_at")) or parse_dt(event.get("occurred_at"))
    norm_status = normalise_status(status)
    return {
        "source": "pagerduty",
        "external_id": _str(data.get("id"), 120),
        "title": _str(data.get("title") or data.get("summary"), 500),
        "severity": severity,
        "status": norm_status,
        "service": _str((data.get("service") or {}).get("summary"), 200),
        "url": safe_link(data.get("html_url")),
        "started_at": started,
        "resolved_at": parse_dt(event.get("occurred_at")) if norm_status == "resolved" else None,
        "description": _str(data.get("description") or data.get("summary"), 5000),
    }


def from_opsgenie(payload: dict) -> Dict[str, Any]:
    """Opsgenie outgoing-webhook integration (`{"action": "Create", "alert": {...}}`)."""
    alert = payload.get("alert") or {}
    details = alert.get("details") if isinstance(alert.get("details"), dict) else {}
    action = str(payload.get("action") or "").lower()
    status = {"close": "resolved", "delete": "resolved",
              "acknowledge": "acknowledged"}.get(action, "open")
    tags = alert.get("tags") if isinstance(alert.get("tags"), list) else []
    service = details.get("service") or alert.get("entity") or \
        next((t.split(":", 1)[1] for t in tags if isinstance(t, str) and t.startswith("service:")), None)
    return {
        "source": "opsgenie",
        "external_id": _str(alert.get("alertId") or alert.get("alias"), 120),
        "title": _str(alert.get("message"), 500),
        "severity": normalise_severity(alert.get("priority")),
        "status": status,
        "service": _str(service, 200),
        "url": safe_link(details.get("incident_url")),
        "affected_url": safe_link(details.get("url") or details.get("affected_url")),
        "started_at": _epoch_ms(alert.get("createdAt")),
        "resolved_at": _epoch_ms(alert.get("updatedAt")) if status == "resolved" else None,
        "description": _str(alert.get("description"), 5000),
        "git_commit": _str(details.get("git_commit") or details.get("commit"), 80),
        "release": _str(details.get("release") or details.get("version"), 120),
    }


def from_generic(payload: dict) -> Dict[str, Any]:
    """TraceIQ's own shape: {title, severity, status, service, url, affected_url,
    started_at, resolved_at, external_id, description, git_commit, git_branch,
    release}. Anything missing defaults."""
    return {
        "source": "generic",
        "external_id": _str(payload.get("external_id") or payload.get("id"), 120),
        "title": _str(payload.get("title") or payload.get("summary") or payload.get("message"), 500),
        "severity": normalise_severity(payload.get("severity") or payload.get("priority")),
        "status": normalise_status(payload.get("status")),
        "service": _str(payload.get("service"), 200),
        "url": safe_link(payload.get("url")),
        "affected_url": safe_link(payload.get("affected_url")),
        "started_at": parse_dt(payload.get("started_at")) if isinstance(payload.get("started_at"), str)
        else _epoch_ms(payload.get("started_at")),
        "resolved_at": parse_dt(payload.get("resolved_at")) if isinstance(payload.get("resolved_at"), str)
        else _epoch_ms(payload.get("resolved_at")),
        "description": _str(payload.get("description"), 5000),
        "git_commit": _str(payload.get("git_commit"), 80),
        "git_branch": _str(payload.get("git_branch"), 200),
        "release": _str(payload.get("release"), 120),
    }


def normalise_payload(payload: Any, source: Optional[str] = None) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise IncidentPayloadError("payload must be a JSON object")
    src = (source or "").lower() or detect_source(payload)
    adapter = {"pagerduty": from_pagerduty, "opsgenie": from_opsgenie,
               "generic": from_generic}.get(src)
    if adapter is None:
        raise IncidentPayloadError(f"unknown source {src!r}")
    out = adapter(payload)
    if not out.get("title"):
        raise IncidentPayloadError("incident has no title")
    return out


# ---------------------------------------------------------------------------
# Matching an incident to what TraceIQ already knows
# ---------------------------------------------------------------------------

def _url_path(url: Optional[str]) -> str:
    if not url:
        return ""
    m = re.match(r"^[a-z]+://[^/]+(/[^?#]*)?", url.strip(), re.I)
    path = (m.group(1) if m else url) or "/"
    return path.rstrip("/") or "/"


def match_existing(title: str, affected_url: Optional[str],
                   clusters: Iterable[Tuple[int, str, Optional[str]]],
                   cases: Iterable[Tuple[int, str, list]]) -> Dict[str, Any]:
    """Best guess at the failure cluster and/or case this incident is about.

    `clusters`: (id, title, sample_error). `cases`: (id, name, steps).
    A case whose `goto`/`http-request` targets the incident's URL path wins over
    keyword overlap — a URL is evidence, shared words are a hint. Keyword
    matches need at least two shared significant words."""
    out: Dict[str, Any] = {"cluster_id": None, "case_id": None, "reason": None}
    reasons: List[str] = []
    kw = keywords(title)
    path = _url_path(affected_url) if affected_url else ""

    best_case, best_case_score = None, 0
    for cid, name, steps in cases:
        score = 0
        if path and path != "/":
            for s in steps or []:
                if isinstance(s, dict) and s.get("type") in ("goto", "http-request", "navigate"):
                    if _url_path(str(s.get("value") or "")) == path:
                        score = 100
                        break
        if not score:
            shared = kw & keywords(name or "")
            if len(shared) >= 2:
                score = len(shared)
        if score > best_case_score:
            best_case, best_case_score = cid, score
    if best_case is not None:
        out["case_id"] = best_case
        reasons.append("case targets the incident URL" if best_case_score >= 100
                       else f"case name shares {best_case_score} keywords")

    best_cl, best_cl_score = None, 0
    for clid, ctitle, sample in clusters:
        shared = kw & (keywords(ctitle or "") | keywords((sample or "")[:500]))
        if path and path != "/" and path in (sample or ""):
            shared = shared | {"<url>"}
        if len(shared) >= 2 and len(shared) > best_cl_score:
            best_cl, best_cl_score = clid, len(shared)
    if best_cl is not None:
        out["cluster_id"] = best_cl
        reasons.append(f"failure cluster shares {best_cl_score} keywords")
    out["reason"] = "; ".join(reasons) or None
    return out


def regression_description(incident: Any) -> str:
    """The generation prompt's "description" for an incident's regression test."""
    parts = [f"Regression test for production incident: {incident.title}."]
    if incident.service:
        parts.append(f"Affected service: {incident.service}.")
    if incident.affected_url:
        parts.append(f"Affected URL: {incident.affected_url}.")
    if incident.description:
        parts.append(f"Details: {incident.description[:1500]}")
    parts.append("The test must fail if this incident's user-visible symptom returns.")
    return " ".join(parts)
