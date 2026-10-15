"""Leak detection over what a finished run stored (WP7, roadmap C12).

The question here is not "did TraceIQ store a secret" — redaction answers that
— but "did the APPLICATION UNDER TEST hand one out". A response body with an AWS
key in it is a vulnerability in the customer's app whether or not our capture
layer masked it on the way in. So this looks at two kinds of evidence:

* **Raw hits** — a credential still in clear in stored data. Happens with
  detectors redaction does not apply by default (high-entropy values under
  suspicious keys), with projects that switched `cloud_keys` off, and with
  results from a worker image older than the redaction rule.
* **Redaction markers** — `[REDACTED:aws_access_key]` in a stored response
  means a key WAS there; redaction removed the value, not the fact. Markers for
  the credential family are therefore findings too.

Only the response side is scanned: response bodies/headers, network-event
responses, console errors, step DOM evidence, error text. Request headers and
bodies are what the TEST sent — the test's own login credentials are not a leak.

Every finding carries a MASKED sample and a location (run, result, case, URL,
field), never the secret itself: the finding is stored, exported as SARIF and
streamed to a SIEM, and each of those would otherwise re-leak it.

Pure functions; the task in app/tasks/security_tasks.py persists the output.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from app.services.redaction import CLOUD_KEY_PATTERNS

CATEGORY = "sensitive_data_exposure"
SCAN_TYPE = "leak"

#: Cap per run. A response that echoes a key on every row of a 10k-row table
#: is one problem, and a findings page with 10k rows is no longer a findings page.
MAX_FINDINGS_PER_RUN = 200

# detector -> (severity, human label)
DETECTORS: Dict[str, Tuple[str, str]] = {
    "private_key": ("high", "Private key"),
    "aws_access_key": ("high", "AWS access key ID"),
    "github_token": ("high", "GitHub token"),
    "slack_token": ("high", "Slack token"),
    "stripe_key": ("high", "Stripe live secret key"),
    "google_api_key": ("medium", "Google API key"),
    "azure_storage_key": ("high", "Azure storage account key"),
    "gcp_service_account": ("high", "GCP service-account key file"),
    "high_entropy_secret": ("medium", "High-entropy secret"),
    "email": ("low", "Email address"),
    "phone": ("low", "Phone number"),
}

_MARKER_RE = re.compile(r"\[REDACTED:([a-z_]+)\]")
_CLOUD_KINDS = frozenset(name for name, _ in CLOUD_KEY_PATTERNS)
_GCP_SA_RE = re.compile(r'"type"\s*:\s*"service_account"')
_GCP_SA_KEY_RE = re.compile(r'"private_key(?:_id)?"\s*:')
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<![\d.])\+?\d[\d ()-]{8,16}\d(?![\d.])")

#: Key-name fragments that make a long random value suspicious. Compared on the
#: separator-stripped lowercase key, like the redaction denylist.
_SUSPICIOUS_KEY_FRAGMENTS = (
    "secret", "token", "apikey", "privatekey", "accesskey", "password",
    "credential", "signingkey", "clientsecret", "connectionstring",
)
_ENTROPY_MIN_LEN = 20
_ENTROPY_MIN_BITS = 3.5


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    n = len(value)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def mask(secret: str) -> str:
    """A sample that identifies a secret to its owner without disclosing it:
    the vendor prefix (useful — it says WHICH kind of key) and the last two
    characters, plus the length."""
    secret = secret.strip()
    if secret.startswith("-----BEGIN"):
        return f"-----{secret.split('-----')[1]}----- …"   # the header line only
    if len(secret) >= 12:
        return f"{secret[:4]}…{secret[-2:]} ({len(secret)} chars)"
    return f"{secret[:1]}… ({len(secret)} chars)"


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


#: Random-looking values that responses carry by design: anti-CSRF tokens,
#: pagination cursors, OAuth state/nonce. Flagging these would bury real leaks.
_BENIGN_KEY_FRAGMENTS = ("csrf", "xsrf", "page", "next", "continuation", "cursor",
                         "nonce", "state")


def _suspicious_key(key: str) -> bool:
    k = _normalize_key(key)
    if any(fragment in k for fragment in _BENIGN_KEY_FRAGMENTS):
        return False
    return any(fragment in k for fragment in _SUSPICIOUS_KEY_FRAGMENTS)


def _field_shape(field: str) -> str:
    """`network_events[3].responseBody.items[0].key` → `network_events[].responseBody.items[].key`.
    Indexes move between runs; a fingerprint built on them would make every
    run's hit look new and retest verification could never match."""
    return re.sub(r"\[\d+\]", "[]", field)


_WHERE = (
    ("network_events", "response"), ("response", "response"),
    ("console_errors", "console output"), ("step_dom", "page DOM"),
    ("error_message", "error message"), ("execution_log", "execution log"),
    ("run.response", "response"),
)


def _where(field: str) -> str:
    for prefix, label in _WHERE:
        if field.startswith(prefix):
            return label
    return "run evidence"


def _looks_random(value: str) -> bool:
    if len(value) < _ENTROPY_MIN_LEN or " " in value.strip():
        return False
    if value.startswith("[REDACTED") or value.startswith("{{"):
        return False
    return shannon_entropy(value) >= _ENTROPY_MIN_BITS


def detect_in_text(text: str, *, sensitive_pii: Sequence[str] = ()) -> List[Tuple[str, str]]:
    """(detector, masked sample) hits in one string."""
    if not isinstance(text, str) or not text:
        return []
    hits: List[Tuple[str, str]] = []
    for name, pattern in CLOUD_KEY_PATTERNS:
        for m in pattern.finditer(text):
            hits.append((name, mask(m.group(0))))
    for m in _MARKER_RE.finditer(text):
        kind = m.group(1)
        if kind in _CLOUD_KINDS or kind in sensitive_pii:
            hits.append((kind, f"{m.group(0)} (masked at capture)"))
    if _GCP_SA_RE.search(text) and _GCP_SA_KEY_RE.search(text):
        hits.append(("gcp_service_account", '{"type": "service_account", "private_key": …}'))
    if "email" in sensitive_pii:
        hits += [("email", mask(m.group(0))) for m in _EMAIL_RE.finditer(text)]
    if "phone" in sensitive_pii:
        hits += [("phone", mask(m.group(0))) for m in _PHONE_RE.finditer(text)]
    return hits


def _walk(value: Any, path: str = "") -> Iterator[Tuple[str, Optional[str], str]]:
    """(json path, parent key, string leaf) for every string in a structure."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk_keyed(v, f"{path}.{k}" if path else str(k), str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")
    elif isinstance(value, str):
        yield path, None, value


def _walk_keyed(value: Any, path: str, key: str):
    if isinstance(value, str):
        yield path, key, value
    else:
        yield from _walk(value, path)


def detect_in_value(value: Any, *, sensitive_pii: Sequence[str] = ()) -> List[Tuple[str, str, str]]:
    """(detector, masked sample, sub-path) hits in a body / header map / JSON.

    A string that parses as JSON is walked as JSON so the high-entropy rule can
    see key names; anything else is scanned as text.
    """
    if value is None or value == "":
        return []
    parsed = value
    if isinstance(value, str):
        stripped = value.lstrip()
        if stripped[:1] in ("{", "["):
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError):
                parsed = value
    out: List[Tuple[str, str, str]] = []
    if isinstance(parsed, str):
        return [(d, s, "") for d, s in detect_in_text(parsed, sensitive_pii=sensitive_pii)]
    # Whole-document detectors need the raw text (a service-account file is
    # recognised by two keys together, not by one leaf).
    raw = value if isinstance(value, str) else json.dumps(parsed, default=str)
    if _GCP_SA_RE.search(raw) and _GCP_SA_KEY_RE.search(raw):
        out.append(("gcp_service_account", '{"type": "service_account", "private_key": …}', ""))
    for path, key, leaf in _walk(parsed):
        for detector, sample in detect_in_text(leaf, sensitive_pii=sensitive_pii):
            if detector == "gcp_service_account":
                continue
            out.append((detector, sample, path))
        if key and _suspicious_key(key) and _looks_random(leaf) and not _already_typed(leaf):
            out.append(("high_entropy_secret", mask(leaf), path))
    return out


def _already_typed(value: str) -> bool:
    """A value a typed detector already explained should not ALSO be reported
    as a generic high-entropy secret."""
    return any(p.search(value) for _, p in CLOUD_KEY_PATTERNS)


def _sources_for_result(result: Any) -> Iterator[Tuple[str, Any, Optional[str]]]:
    """(field, value, url) per scannable field of one TestCaseResult."""
    url = getattr(result, "request_url", None)
    yield "response_body", getattr(result, "response_body", None), url
    yield "response_headers", getattr(result, "response_headers", None), url
    yield "error_message", getattr(result, "error_message", None), url
    payload = getattr(result, "result_payload", None) or {}
    if isinstance(payload, dict):
        for i, err in enumerate(payload.get("console_errors") or []):
            text = err.get("text") if isinstance(err, dict) else err
            yield f"console_errors[{i}]", text, (err.get("url") if isinstance(err, dict) else None) or url
        dom = payload.get("step_dom")
        if isinstance(dom, dict):
            for step_id, snippet in dom.items():
                yield f"step_dom.{step_id}", snippet, url


def _sources_for_run(run: Any) -> Iterator[Tuple[str, Any, Optional[str]]]:
    for i, ev in enumerate(getattr(run, "network_events", None) or []):
        if not isinstance(ev, dict):
            continue
        url = ev.get("url")
        for key in ("responseBody", "response_body"):
            if ev.get(key):
                yield f"network_events[{i}].{key}", ev.get(key), url
        for key in ("responseHeaders", "response_headers"):
            if ev.get(key):
                yield f"network_events[{i}].{key}", ev.get(key), url
        # A key in a URL the app redirected to (or linked) is exposed in every
        # proxy and referrer log on the way.
        if isinstance(url, str):
            yield f"network_events[{i}].url", url, url
    yield "run.response_headers", getattr(run, "response_headers", None), getattr(run, "request_url", None)
    for i, entry in enumerate(getattr(run, "execution_log", None) or []):
        yield f"execution_log[{i}]", entry, None


def sensitive_pii_from_policy(data_policy: Optional[dict]) -> Tuple[str, ...]:
    """email/phone are leaks only where the project said so — they are opt-in
    redaction patterns, and the same opt-in decides whether finding one in a
    response is a problem or a normal profile page."""
    patterns = (data_policy or {}).get("redact_patterns") if isinstance(data_policy, dict) else None
    return tuple(p for p in ("email", "phone") if p in (patterns or ()))


def scan_run(run: Any, results: Iterable[Any], *,
             data_policy: Optional[dict] = None) -> List[Dict[str, Any]]:
    """Finding dicts for one run, de-duplicated by (detector, field, url)."""
    from app.services.passive_security import finding_fingerprint

    pii = sensitive_pii_from_policy(data_policy)
    findings: List[Dict[str, Any]] = []
    seen = set()

    def emit(detector: str, sample: str, field: str, url: Optional[str],
             result: Any = None) -> None:
        severity, label = DETECTORS.get(detector, ("medium", detector))
        title = f"{label} exposed in {_where(field)}"
        if field.endswith(".url") and detector in _CLOUD_KINDS:
            title = f"{label} exposed in a URL"
        shape = _field_shape(field)
        key = (detector, shape, url)
        if key in seen:
            return
        seen.add(key)
        location = {
            "run_id": getattr(run, "id", None),
            "result_id": getattr(result, "id", None) if result is not None else None,
            "case_id": getattr(result, "test_case_id", None) if result is not None
            else getattr(run, "test_case_id", None),
            "url": url,
            "field": field,
        }
        findings.append({
            "category": CATEGORY,
            "severity": severity,
            "title": title,
            "description": (
                f"A {label.lower()} appeared in data the application returned "
                f"(`{field}`). Treat the credential as compromised: rotate it, then "
                "stop the application from sending it. TraceIQ stores only the "
                "masked sample below."),
            "evidence": f"{field}: {sample}",
            "target_url": url,
            "location": location,
            "fingerprint": finding_fingerprint(CATEGORY, f"{detector}|{shape}", url),
            "detector": detector,
        })

    for result in results or []:
        for field, value, url in _sources_for_result(result):
            for detector, sample, sub in detect_in_value(value, sensitive_pii=pii):
                emit(detector, sample, f"{field}.{sub}" if sub else field, url, result)
                if len(findings) >= MAX_FINDINGS_PER_RUN:
                    return findings
    for field, value, url in _sources_for_run(run):
        for detector, sample, sub in detect_in_value(value, sensitive_pii=pii):
            emit(detector, sample, f"{field}.{sub}" if sub else field, url)
            if len(findings) >= MAX_FINDINGS_PER_RUN:
                return findings
    return findings
