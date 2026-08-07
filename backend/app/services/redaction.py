"""Redaction of credentials and PII from captured request/response data.

This is the backend half of a two-layer defence. The execution worker redacts
at capture time (`execution-engine/src/core/redact.ts`), which is what actually
keeps secrets out of MinIO artifacts. This module runs again on ingestion,
because the worker image bakes its code at build time — a deployment can easily
be running a worker older than its backend, and in that window the worker sends
raw bodies and headers. The backend must not trust the worker to have scrubbed
anything.

Two mechanisms, deliberately different in character:

*Key denylist* — a field named `password` or `cvv` is sensitive regardless of
what it contains. This catches the things no regex can (a 3-digit CVV, a short
PIN, a free-form secret).

*Value patterns* — a card number is sensitive wherever it appears, including
under an innocuous key or embedded in prose. These are checksum-validated
(Luhn for PAN, Verhoeff for Aadhaar) because the alternative — redacting every
12-to-16-digit run — destroys the debugging value of a captured response, and a
redaction layer nobody trusts gets switched off.

`email` and `phone` are available but OFF by default: email addresses are
ordinary test-fixture data and blanket-redacting them would break most stored
assertions. Deployments handling health or KYC data enable them per project via
`Project.data_policy`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode

REDACTED = "[REDACTED]"


def _token(name: str) -> str:
    """Replacement marker. Naming what was removed keeps a redacted capture
    diagnosable — "there was a card number here" is useful; a bare blank is
    not."""
    return f"[REDACTED:{name}]"


# --------------------------------------------------------------------------
# Denylists
# --------------------------------------------------------------------------

#: Header names whose *value* is always a credential.
DEFAULT_HEADER_NAMES: Tuple[str, ...] = (
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-access-token",
    "x-csrf-token",
    "x-xsrf-token",
    "x-session-token",
    "x-traceiq-secret",
    "x-worker-secret",
)

#: Body/form field names whose value is always sensitive. Compared after
#: stripping separators and lowercasing, so `api_key`, `apiKey`, `API-KEY` and
#: `apikey` all collapse to the same entry.
DEFAULT_BODY_KEYS: Tuple[str, ...] = (
    "password", "passwd", "pwd", "currentpassword", "newpassword",
    "secret", "clientsecret", "apikey", "apisecret", "token",
    "accesstoken", "refreshtoken", "idtoken", "sessiontoken", "authtoken",
    "authorization", "auth", "credentials", "privatekey", "sessionstate",
    "cookie", "otp", "pin", "mfacode", "totp", "recoverycode",
    "cvv", "cvc", "cardcode", "securitycode",
    "cardnumber", "creditcard", "pan", "accountnumber",
    "ssn", "socialsecuritynumber", "aadhaar", "aadhar", "taxid",
)

#: Value patterns enabled unless a policy says otherwise. Each is either
#: checksum-validated or structurally unambiguous.
DEFAULT_PATTERNS: Tuple[str, ...] = ("pan", "aadhaar", "jwt")


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


_NORMALIZED_BODY_KEYS = frozenset(_normalize_key(k) for k in DEFAULT_BODY_KEYS)


@dataclass(frozen=True)
class RedactionPolicy:
    """What to redact. Mirrors the `data_policy` blob stored on `Project`."""

    #: Extra header names beyond DEFAULT_HEADER_NAMES.
    header_names: Sequence[str] = ()
    #: Extra body field names beyond DEFAULT_BODY_KEYS.
    body_keys: Sequence[str] = ()
    #: Value patterns to apply. Defaults to DEFAULT_PATTERNS; pass () to
    #: disable value scanning entirely and rely on key names alone.
    patterns: Optional[Sequence[str]] = None

    def header_set(self) -> frozenset:
        return frozenset(h.lower() for h in DEFAULT_HEADER_NAMES) | frozenset(
            str(h).lower() for h in self.header_names
        )

    def body_key_set(self) -> frozenset:
        return _NORMALIZED_BODY_KEYS | frozenset(
            _normalize_key(k) for k in self.body_keys
        )

    def pattern_names(self) -> Tuple[str, ...]:
        if self.patterns is None:
            return DEFAULT_PATTERNS
        return tuple(self.patterns)

    @classmethod
    def from_data_policy(cls, data_policy: Optional[Dict[str, Any]]) -> "RedactionPolicy":
        """Build from a `Project.data_policy` blob. Unknown keys are ignored so
        an older backend tolerates a newer policy shape."""
        if not isinstance(data_policy, dict):
            return cls()
        patterns = data_policy.get("redact_patterns")
        return cls(
            header_names=tuple(data_policy.get("redact_headers") or ()),
            body_keys=tuple(data_policy.get("redact_body_fields") or ()),
            patterns=tuple(patterns) if patterns is not None else None,
        )


DEFAULT_POLICY = RedactionPolicy()


# --------------------------------------------------------------------------
# Checksums
# --------------------------------------------------------------------------

def luhn_valid(digits: str) -> bool:
    """Standard mod-10 check used by every major card scheme."""
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        digit = ord(char) - 48
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


# Verhoeff tables — Aadhaar's 12th digit is a Verhoeff check over the first 11.
_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6), (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8), (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2), (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4), (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2), (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0), (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5), (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def verhoeff_valid(digits: str) -> bool:
    check = 0
    for index, char in enumerate(reversed(digits)):
        check = _VERHOEFF_D[check][_VERHOEFF_P[index % 8][ord(char) - 48]]
    return check == 0


# --------------------------------------------------------------------------
# Value patterns
# --------------------------------------------------------------------------

_PAN_RE = re.compile(r"(?<![\d.])(?:\d[ -]?){12,18}\d(?![\d.])")
_AADHAAR_RE = re.compile(r"(?<![\d.])\d{4}[ -]?\d{4}[ -]?\d{4}(?![\d.])")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<![\d.])\+?\d[\d ()-]{8,16}\d(?![\d.])")


def _sub_pan(text: str) -> str:
    def replace(match: re.Match) -> str:
        digits = re.sub(r"[ -]", "", match.group(0))
        if 13 <= len(digits) <= 19 and luhn_valid(digits):
            return _token("pan")
        return match.group(0)
    return _PAN_RE.sub(replace, text)


def _sub_aadhaar(text: str) -> str:
    def replace(match: re.Match) -> str:
        digits = re.sub(r"[ -]", "", match.group(0))
        if len(digits) == 12 and verhoeff_valid(digits):
            return _token("aadhaar")
        return match.group(0)
    return _AADHAAR_RE.sub(replace, text)


_PATTERN_FUNCS = {
    "pan": _sub_pan,
    "aadhaar": _sub_aadhaar,
    "jwt": lambda t: _JWT_RE.sub(_token("jwt"), t),
    "email": lambda t: _EMAIL_RE.sub(_token("email"), t),
    "phone": lambda t: _PHONE_RE.sub(_token("phone"), t),
}

# Order matters: JWT before PAN so a token's digit runs are already gone, and
# Aadhaar before PAN so a 12-digit id is not first swallowed by the PAN regex.
_PATTERN_ORDER = ("jwt", "aadhaar", "pan", "email", "phone")


def redact_text(text: Optional[str], policy: RedactionPolicy = DEFAULT_POLICY) -> Optional[str]:
    """Apply value patterns to a free-text string (error messages, logs)."""
    if not isinstance(text, str) or not text:
        return text
    enabled = set(policy.pattern_names())
    out = text
    for name in _PATTERN_ORDER:
        if name in enabled:
            out = _PATTERN_FUNCS[name](out)
    return out


# --------------------------------------------------------------------------
# Headers
# --------------------------------------------------------------------------

def redact_headers(headers: Any, policy: RedactionPolicy = DEFAULT_POLICY) -> Any:
    """Replace the value of every denylisted header. Names are preserved so the
    shape of the exchange stays visible."""
    if not isinstance(headers, dict):
        return headers
    denied = policy.header_set()
    return {
        key: (REDACTED if str(key).lower() in denied else redact_text(value, policy))
        for key, value in headers.items()
    }


# --------------------------------------------------------------------------
# Bodies
# --------------------------------------------------------------------------

def _redact_json(value: Any, denied_keys: frozenset, policy: RedactionPolicy) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, val in value.items():
            if _normalize_key(key) in denied_keys:
                out[key] = REDACTED
            else:
                out[key] = _redact_json(val, denied_keys, policy)
        return out
    if isinstance(value, list):
        return [_redact_json(item, denied_keys, policy) for item in value]
    if isinstance(value, str):
        return redact_text(value, policy)
    return value


def _redact_form(body: str, denied_keys: frozenset, policy: RedactionPolicy) -> str:
    try:
        pairs = parse_qsl(body, keep_blank_values=True)
    except ValueError:
        return redact_text(body, policy)
    if not pairs:
        return redact_text(body, policy)
    return urlencode([
        (key, REDACTED if _normalize_key(key) in denied_keys else redact_text(value, policy))
        for key, value in pairs
    ])


def redact_body(
    body: Any,
    content_type: Optional[str] = None,
    policy: RedactionPolicy = DEFAULT_POLICY,
) -> Any:
    """Redact a request or response body.

    Accepts a JSON string, an already-parsed dict/list, a form-encoded string,
    or arbitrary text, and returns the same shape it was given. Malformed JSON
    degrades to a text sweep rather than raising — a body that failed to parse
    is exactly the kind that might carry a stack trace with a token in it.
    """
    if body is None or body == "":
        return body

    denied_keys = policy.body_key_set()

    if isinstance(body, (dict, list)):
        return _redact_json(body, denied_keys, policy)

    if not isinstance(body, str):
        return body

    ctype = (content_type or "").lower()

    if "x-www-form-urlencoded" in ctype:
        return _redact_form(body, denied_keys, policy)

    # Try JSON whenever the content type says so *or* the body looks like it.
    looks_json = body.lstrip()[:1] in ("{", "[")
    if "json" in ctype or looks_json:
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            return redact_text(body, policy)
        return json.dumps(_redact_json(parsed, denied_keys, policy))

    return redact_text(body, policy)


# --------------------------------------------------------------------------
# Composite helpers used by the ingestion paths
# --------------------------------------------------------------------------

def redact_network_events(events: Any, policy: RedactionPolicy = DEFAULT_POLICY) -> Any:
    """Scrub the header maps on each captured network event."""
    if not isinstance(events, list):
        return events
    out = []
    for event in events:
        if not isinstance(event, dict):
            out.append(event)
            continue
        scrubbed = dict(event)
        for key in ("requestHeaders", "responseHeaders", "request_headers", "response_headers"):
            if key in scrubbed:
                scrubbed[key] = redact_headers(scrubbed[key], policy)
        for key in ("url", "requestBody", "responseBody", "request_body", "response_body"):
            if key in scrubbed:
                scrubbed[key] = redact_text(scrubbed[key], policy) \
                    if isinstance(scrubbed[key], str) else scrubbed[key]
        out.append(scrubbed)
    return out


def redact_steps(steps: Any, policy: RedactionPolicy = DEFAULT_POLICY) -> Any:
    """Scrub a `TestCase.steps` array before it is copied into an audit row or
    a case revision.

    Users who authored cases before `fill` supported `{{secret.X}}` have real
    passwords sitting in step values. Those values are already stored in an
    unencrypted JSON column; there is no reason to fan them out into
    `AuditLog.changes` and `TestCaseRevision.snapshot` as well.
    """
    if not isinstance(steps, list):
        return steps
    denied_keys = policy.body_key_set()
    out = []
    for step in steps:
        if not isinstance(step, dict):
            out.append(step)
            continue
        scrubbed = dict(step)
        selector = str(scrubbed.get("selector") or "")
        # A value typed into a password-ish field is a credential whatever it
        # looks like. Template tokens are safe by construction — the secret
        # itself lives in ProjectSecret, not here.
        value = scrubbed.get("value")
        if isinstance(value, str) and value and "{{" not in value:
            if _looks_like_secret_field(selector) or _normalize_key(
                str(scrubbed.get("name") or "")
            ) in denied_keys:
                scrubbed["value"] = REDACTED
            else:
                scrubbed["value"] = redact_text(value, policy)
        if isinstance(scrubbed.get("params"), (dict, list)):
            scrubbed["params"] = _redact_json(scrubbed["params"], denied_keys, policy)
        out.append(scrubbed)
    return out


def _redact_response_data(data: Any, policy: RedactionPolicy) -> Any:
    if not isinstance(data, dict):
        return data
    out = dict(data)
    if "headers" in out:
        out["headers"] = redact_headers(out["headers"], policy)
    if "body" in out:
        out["body"] = redact_body(out["body"], out.get("mimeType") or out.get("content_type"), policy)
    request = out.get("request")
    if isinstance(request, dict):
        req = dict(request)
        if "headers" in req:
            req["headers"] = redact_headers(req["headers"], policy)
        if "body" in req:
            req["body"] = redact_body(req["body"], None, policy)
        if isinstance(req.get("url"), str):
            req["url"] = redact_text(req["url"], policy)
        out["request"] = req
    if isinstance(out.get("url"), str):
        out["url"] = redact_text(out["url"], policy)
    return out


_RESULT_TEXT_FIELDS = ("error", "error_message")
_RESULT_HEADER_FIELDS = ("request_headers", "response_headers")
_RESULT_BODY_FIELDS = ("request_body", "response_body")


def _redact_case_result(result: Any, policy: RedactionPolicy) -> Any:
    if not isinstance(result, dict):
        return result
    out = dict(result)
    if "response_data" in out:
        out["response_data"] = _redact_response_data(out["response_data"], policy)
    if "network_events" in out:
        out["network_events"] = redact_network_events(out["network_events"], policy)
    for field in _RESULT_TEXT_FIELDS:
        if isinstance(out.get(field), str):
            out[field] = redact_text(out[field], policy)
    for field in _RESULT_HEADER_FIELDS:
        if field in out:
            out[field] = redact_headers(out[field], policy)
    for field in _RESULT_BODY_FIELDS:
        if field in out:
            out[field] = redact_body(out[field], None, policy)
    if isinstance(out.get("execution_log"), list):
        out["execution_log"] = _redact_json(out["execution_log"], policy.body_key_set(), policy)
    if isinstance(out.get("steps"), list):
        out["steps"] = redact_steps(out["steps"], policy)
    return out


def redact_worker_result(payload: Any, policy: RedactionPolicy = DEFAULT_POLICY) -> Any:
    """Scrub a worker result on ingestion, before anything is written to the DB.

    Applied once at each of the three ingestion entry points rather than at the
    ten individual column assignments they contain — those are duplicated
    across `result_aggregator`, `webhook_tasks` and `test_service`, and a
    per-assignment approach would need to be repeated correctly in all three
    forever.

    This deliberately applies only the *built-in* denylist and pattern set, not
    the project's extras. The worker has already applied the project-specific
    policy at capture time; the backend's job here is the universal baseline
    for the case where the worker is an older image that did not scrub at all.
    Loading the project's policy would mean a DB round-trip in the aggregator's
    hot path to re-apply rules that are almost always already applied.

    Routing and status fields are untouched — the aggregator finalizes runs
    from them.
    """
    if not isinstance(payload, dict):
        return payload
    out = _redact_case_result(payload, policy)
    for key in ("test_results", "results"):
        if isinstance(out.get(key), list):
            out[key] = [_redact_case_result(item, policy) for item in out[key]]
    return out


def redact_audit_changes(changes: Any, policy: RedactionPolicy = DEFAULT_POLICY) -> Any:
    """Scrub an `AuditLog.changes` payload.

    Audit records *that* a field changed; it has never needed the secret value
    itself, and `changes=case.model_dump()` was copying whole step arrays —
    passwords included — into a table with no encryption and no retention.

    Unlike a case revision this is lossless in purpose: nothing is ever
    restored from an audit row.
    """
    if not isinstance(changes, dict):
        return changes
    out = dict(changes)
    if isinstance(out.get("steps"), list):
        out["steps"] = redact_steps(out["steps"], policy)
    return _redact_json(out, policy.body_key_set(), policy)


_SECRET_SELECTOR_RE = re.compile(
    r"pass(word|wd)?|secret|token|api[-_]?key|otp|cvv|cvc|pin\b|card[-_]?number",
    re.IGNORECASE,
)


def _looks_like_secret_field(selector: str) -> bool:
    """`input[type=password]`, `#password`, `[name="cvv"]` and friends."""
    if not selector:
        return False
    if "type=password" in selector.replace('"', "").replace("'", "").replace(" ", ""):
        return True
    return bool(_SECRET_SELECTOR_RE.search(selector))
