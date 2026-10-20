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
#: checksum-validated or structurally unambiguous. `cloud_keys` (WP7) is a
#: family of vendor credential formats — each has a fixed, documented prefix, so
#: none of them collides with ordinary test data the way email/phone would.
DEFAULT_PATTERNS: Tuple[str, ...] = ("pan", "aadhaar", "jwt", "cloud_keys")


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

#: Vendor credential formats, as (token name, regex). Mirrored in redact.ts as
#: CLOUD_KEY_PATTERNS and reused by app/services/leak_detection.py, which turns
#: the same hits (and the markers they leave behind) into security findings.
#: The PEM rule runs to the END line, or to the end of the text when a capture
#: was truncated mid-key — a partial private key is still a private key. `\Z`,
#: not `$`: Python's `$` also matches before a final newline, which left that
#: newline behind where the JS twin's `$` consumes it (tests/vectors).
CLOUD_KEY_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("private_key", re.compile(
        r"-----BEGIN ([A-Z0-9 ]*)PRIVATE KEY-----[\s\S]*?(?:-----END \1PRIVATE KEY-----|\Z)")),
    ("aws_access_key", re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])")),
    ("github_token", re.compile(
        r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})")),
    ("slack_token", re.compile(r"(?<![A-Za-z0-9])xox[abposr]-[A-Za-z0-9-]{10,}")),
    ("stripe_key", re.compile(r"(?<![A-Za-z0-9])[sr]k_live_[A-Za-z0-9]{20,}")),
    ("google_api_key", re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])")),
    ("azure_storage_key", re.compile(r"(?<=AccountKey=)[A-Za-z0-9+/]{86}==")),
)


def _sub_cloud_keys(text: str) -> str:
    for name, pattern in CLOUD_KEY_PATTERNS:
        text = pattern.sub(_token(name), text)
    return text
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
    "cloud_keys": _sub_cloud_keys,
    "pan": _sub_pan,
    "aadhaar": _sub_aadhaar,
    "jwt": lambda t: _JWT_RE.sub(_token("jwt"), t),
    "email": lambda t: _EMAIL_RE.sub(_token("email"), t),
    "phone": lambda t: _PHONE_RE.sub(_token("phone"), t),
}

# Order matters: cloud keys first, because an AWS key id's digit run could
# otherwise be half-eaten by the PAN rule and survive unrecognisable; JWT before
# PAN so a token's digit runs are already gone; Aadhaar before PAN so a 12-digit
# id is not first swallowed by the PAN regex.
_PATTERN_ORDER = ("cloud_keys", "jwt", "aadhaar", "pan", "email", "phone")


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
# Inbound messages (test inbox emails, captured webhooks)
# --------------------------------------------------------------------------

# A URL in a message. Stops at whitespace, quotes, angle brackets and closing
# parens/brackets so a link inside prose or HTML attributes ends where it should.
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
# A path segment that is an opaque credential: long, URL-safe, and containing
# both a letter and a digit (so /blog/announcing-the-new-dashboard survives).
_PATH_TOKEN_RE = re.compile(r"(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{20,}")
# A one-time code: 6-8 digits standing alone (a sentence-ending period is
# fine; a decimal point on either side is not).
_CODE_RE = re.compile(r"(?<!\w)(?<!\d\.)\d{6,8}(?!\w)(?!\.\d)")


def _redact_url(match: re.Match) -> str:
    url = match.group(0)
    base, sep, _rest = url.partition("?")
    if not sep:
        base, sep, _rest = url.partition("#")
    scheme, _, remainder = base.partition("://")
    host, slash, path = remainder.partition("/")
    if slash:
        path = "/".join(_token("link") if _PATH_TOKEN_RE.fullmatch(seg) else seg
                        for seg in path.split("/"))
    out = f"{scheme}://{host}{slash}{path}"
    return out + (sep + _token("link") if sep else "")


def redact_message_text(text: Optional[str], policy: RedactionPolicy = DEFAULT_POLICY) -> Optional[str]:
    """Free-text redaction for messages the APP UNDER TEST sends us — the body
    of a password-reset email, a captured webhook payload rendered as text.

    Those are built to carry live credentials: the reset link IS the password,
    the six digits ARE the second factor. So on top of `redact_text` this drops
    every link's query string and fragment, masks opaque path segments
    (/reset/9f8a…), and masks standalone 6-8 digit codes. It is only used for
    previews and logs; the value a step extracts is taken from the raw message
    before redaction, which is the point of the step.
    """
    if not isinstance(text, str) or not text:
        return text
    out = redact_text(text, policy)
    out = _URL_RE.sub(_redact_url, out)
    return _CODE_RE.sub(_token("code"), out)


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
        # Serialised like the worker's JSON.stringify (compact, non-ASCII kept),
        # so re-redacting a worker-redacted body at ingestion does not
        # reformat it (tests/vectors body.json_compact_serialisation).
        return json.dumps(_redact_json(parsed, denied_keys, policy),
                          separators=(",", ":"), ensure_ascii=False)

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
    if isinstance(out.get("console_errors"), list):
        out["console_errors"] = [
            {**e, "text": redact_text(e.get("text"), policy)}
            if isinstance(e, dict) and isinstance(e.get("text"), str) else e
            for e in out["console_errors"]
        ]
    if isinstance(out.get("attempts"), list):
        out["attempts"] = [
            {**a, "error": redact_text(a["error"], policy)}
            if isinstance(a, dict) and isinstance(a.get("error"), str) else a
            for a in out["attempts"]
        ]
    if isinstance(out.get("step_dom"), dict):
        out["step_dom"] = {
            k: redact_dom(v, policy) if isinstance(v, str) else v
            for k, v in out["step_dom"].items()
        }
    if isinstance(out.get("heal_suggestions"), list):
        out["heal_suggestions"] = [
            {**h, "dom_snapshot": redact_dom(h["dom_snapshot"], policy)}
            if isinstance(h, dict) and isinstance(h.get("dom_snapshot"), str) else h
            for h in out["heal_suggestions"]
        ]
    if isinstance(out.get("step_evidence"), list):
        out["step_evidence"] = redact_step_evidence(out["step_evidence"], policy)
    if isinstance(out.get("hook_report"), dict):
        out["hook_report"] = redact_hook_report(out["hook_report"], policy)
    if isinstance(out.get("page_budgets"), list):
        out["page_budgets"] = redact_page_budgets(out["page_budgets"], policy)
    if isinstance(out.get("accessibility_results"), list):
        out["accessibility_results"] = redact_accessibility_results(
            out["accessibility_results"], policy)
    return out


def _query_names_only(raw: str) -> str:
    """A URL (or URL template) with query parameter VALUES dropped.
    Twin of `queryNamesOnly` in redact.ts."""
    s = raw.split("#", 1)[0]
    if "?" not in s:
        return s
    head, query = s.split("?", 1)
    names = list(dict.fromkeys(p.split("=", 1)[0] for p in query.split("&") if p.split("=", 1)[0]))
    return head + ("?" + "&".join(f"{n}=\u2026" for n in names) if names else "")


def redact_hook_report(report: Any, policy: RedactionPolicy = DEFAULT_POLICY) -> Any:
    """Suite setup/teardown + cleanup outcomes (`hook_report`): phase and step
    errors, cleanup URLs (query values dropped — captured ids and tokens live
    there) and cleanup errors. Twin of `redactHookReport` in redact.ts."""
    if not isinstance(report, dict):
        return report

    def phase(items: Any) -> Any:
        if not isinstance(items, list):
            return items
        out = []
        for p in items:
            if not isinstance(p, dict):
                out.append(p)
                continue
            item = dict(p)
            if isinstance(item.get("error"), str):
                item["error"] = redact_text(item["error"], policy)
            if isinstance(item.get("steps"), list):
                item["steps"] = [
                    {**st, "error": redact_text(st["error"], policy)}
                    if isinstance(st, dict) and isinstance(st.get("error"), str) else st
                    for st in item["steps"]
                ]
            out.append(item)
        return out

    out = dict(report)
    for key in ("setup", "teardown"):
        if key in out:
            out[key] = phase(out[key])
    if isinstance(out.get("cleanup"), list):
        cleaned = []
        for c in out["cleanup"]:
            if not isinstance(c, dict):
                cleaned.append(c)
                continue
            item = dict(c)
            if isinstance(item.get("url"), str):
                item["url"] = redact_text(_query_names_only(item["url"]), policy)
            if isinstance(item.get("error"), str):
                item["error"] = redact_text(item["error"], policy)
            cleaned.append(item)
        out["cleanup"] = cleaned
    return out


def _text_field(value: Any, policy: RedactionPolicy) -> Any:
    return redact_text(value, policy) if isinstance(value, str) else value


def _text_fields(item: Dict[str, Any], fields: Sequence[str], policy: RedactionPolicy) -> Dict[str, Any]:
    """`item` with each PRESENT field scrubbed. Absent fields stay absent: the
    JS twin assigns `undefined`, which JSON drops, so adding a None here made
    the two disagree on the shape of the result."""
    out = dict(item)
    for field in fields:
        if field in out:
            out[field] = _text_field(out[field], policy)
    return out


def redact_page_budgets(items: Any, policy: RedactionPolicy = DEFAULT_POLICY) -> Any:
    """`check-page-budget` outcomes (WP10): page URL, failure text, SEO check
    values/details. Twin of `redactPageBudgets` in redact.ts."""
    if not isinstance(items, list):
        return items
    out = []
    for b in items:
        if not isinstance(b, dict):
            out.append(b)
            continue
        item = _text_fields(b, ("url", "failure"), policy)
        if isinstance(b.get("seo"), list):
            item["seo"] = [
                _text_fields(c, ("value", "detail"), policy) if isinstance(c, dict) else c
                for c in b["seo"]
            ]
        out.append(item)
    return out


# --------------------------------------------------------------------------
# Accessibility results (WP15d) — twin of the block in redact.ts
# --------------------------------------------------------------------------

#: Attributes whose VALUE a selector may keep: they name or type an element
#: rather than carry what it shows. href/src/alt/title/value/placeholder and
#: aria-label are page content (or URLs with tokens) and lose their value.
A11Y_SAFE_ATTRS = frozenset((
    "id", "class", "type", "role", "name", "for", "lang", "dir",
    "data-testid", "data-test", "data-test-id", "data-qa", "data-cy",
))
_A11Y_ATTR_VALUE_MAX = 64
_A11Y_TARGET_MAX = 300
_A11Y_TARGETS_PER_RULE = 25
_A11Y_RULES_PER_CHECK = 100
_A11Y_HELP_PREFIX = "https://dequeuniversity.com/"
# One attribute selector: [name], [name=v], [name="v"], [name~='v' i]. Fixed
# pattern over a capped string — no author-supplied regex runs here.
_A11Y_ATTR_RE = re.compile(
    r"""\[\s*([A-Za-z_][-A-Za-z0-9_:.]*)\s*(?:([~|^$*]?=)\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^\]\s'"]+)\s*([iIsS])?\s*)?\]""")


def _a11y_attr(match: "re.Match") -> str:
    name, op, value = match.group(1), match.group(2), match.group(3)
    if not op:
        return match.group(0)
    bare = value[1:-1] if value[:1] in ("'", '"') else value
    if name.lower() in A11Y_SAFE_ATTRS and len(bare) <= _A11Y_ATTR_VALUE_MAX:
        return match.group(0)
    return f"[{name}]"


def redact_a11y_target(selector: Any, policy: RedactionPolicy = DEFAULT_POLICY) -> str:
    """An axe target selector with nothing in it but what addresses the
    element. Twin of `redactA11yTarget` in redact.ts."""
    if not isinstance(selector, str):
        return ""
    out = _A11Y_ATTR_RE.sub(_a11y_attr, selector[:500])
    return (redact_text(out, policy) or "")[:_A11Y_TARGET_MAX]


def a11y_help_url(raw: Any) -> Optional[str]:
    """The rule's reference page, only on Deque's site, query dropped."""
    if not isinstance(raw, str) or not raw.startswith(_A11Y_HELP_PREFIX):
        return None
    return raw.split("?", 1)[0].split("#", 1)[0][:300]


def _a11y_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, min(int(value), 1_000_000))


def redact_accessibility_results(items: Any, policy: RedactionPolicy = DEFAULT_POLICY) -> Any:
    """`check-accessibility` outcomes (WP15d), rebuilt from a whitelist: page
    URL redacted, selectors reduced by `redact_a11y_target`, help URLs pinned
    to Deque. Anything else axe reports (the element's HTML, the failure
    summary — both page text) is dropped. Twin of `redactAccessibilityResults`."""
    if not isinstance(items, list):
        return items
    out = []
    for item in items[:50]:
        if not isinstance(item, dict):
            continue
        violations = []
        for v in (item.get("violations") or [])[:_A11Y_RULES_PER_CHECK] \
                if isinstance(item.get("violations"), list) else []:
            if not isinstance(v, dict):
                continue
            targets = []
            for t in (v.get("targets") or [])[:_A11Y_TARGETS_PER_RULE] \
                    if isinstance(v.get("targets"), list) else []:
                if isinstance(t, dict):
                    targets.append({"selector": redact_a11y_target(t.get("selector"), policy),
                                    "nodes": _a11y_int(t.get("nodes")) or 1})
            violations.append({
                "id": str(v.get("id") or "")[:80],
                "impact": str(v.get("impact") or "")[:20],
                "help_url": a11y_help_url(v.get("help_url")),
                "node_count": _a11y_int(v.get("node_count")),
                "targets": targets,
            })
        out.append({
            "step_id": str(item.get("step_id") or "")[:100] or None,
            "url": _text_field(item.get("url"), policy) if isinstance(item.get("url"), str) else None,
            "violations": violations,
        })
    return out


def redact_crawl_report(report: Any, policy: RedactionPolicy = DEFAULT_POLICY) -> Any:
    """Site-crawl report (WP10): every URL, redirect hops included, plus error
    and detail text. Twin of `redactCrawlReport` in redact.ts."""
    if not isinstance(report, dict):
        return report
    out = _text_fields(report, ("error",), policy)
    if isinstance(report.get("pages"), list):
        out["pages"] = [
            _text_fields(p, ("url", "error"), policy) if isinstance(p, dict) else p
            for p in report["pages"]
        ]
    if isinstance(report.get("issues"), list):
        issues = []
        for i in report["issues"]:
            if not isinstance(i, dict):
                issues.append(i)
                continue
            item = _text_fields(i, ("page_url", "target_url", "detail"), policy)
            if isinstance(i.get("redirect_chain"), list):
                item["redirect_chain"] = [
                    _text_fields(h, ("url",), policy) if isinstance(h, dict) else h
                    for h in i["redirect_chain"]
                ]
            issues.append(item)
        out["issues"] = issues
    return out


def redact_step_evidence(entries: Any, policy: RedactionPolicy = DEFAULT_POLICY) -> Any:
    """Per-step evidence: error, selector, DOM excerpt, console text and
    request URLs. Ids, statuses, status codes and artifact keys are routing
    data and stay as they are. Twin of `redactStepEvidence` in redact.ts."""
    if not isinstance(entries, list):
        return entries
    out = []
    for e in entries:
        if not isinstance(e, dict):
            out.append(e)
            continue
        item = dict(e)
        for field in ("error", "selector", "dom"):
            if isinstance(item.get(field), str):
                item[field] = redact_text(item[field], policy)
        if isinstance(item.get("console"), list):
            item["console"] = [
                {**c, "text": redact_text(c["text"], policy)}
                if isinstance(c, dict) and isinstance(c.get("text"), str) else c
                for c in item["console"]
            ]
        if isinstance(item.get("requests"), list):
            item["requests"] = [
                {**r, "url": redact_text(r["url"], policy)}
                if isinstance(r, dict) and isinstance(r.get("url"), str) else r
                for r in item["requests"]
            ]
        out.append(item)
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


_OBSERVATION_TEXT_FIELDS = ("url", "final_url", "error", "swapped_id", "owner_url")


def redact_access_observation(obs: Any, policy: RedactionPolicy = DEFAULT_POLICY) -> Any:
    """Scrub an access-matrix cell observation (twin of `redactAccessObservation`
    in redact.ts). It carries the response headers and a body excerpt of a
    request made AS a persona — exactly where a session token gets echoed back.
    """
    if not isinstance(obs, dict):
        return obs
    out = dict(obs)
    if "headers" in out:
        out["headers"] = redact_headers(out["headers"], policy)
    if "owner_headers" in out:
        out["owner_headers"] = redact_headers(out["owner_headers"], policy)
    if "body_excerpt" in out:
        out["body_excerpt"] = redact_body(out["body_excerpt"], out.get("content_type"), policy)
    for field in _OBSERVATION_TEXT_FIELDS:
        if isinstance(out.get(field), str):
            out[field] = redact_text(out[field], policy)
    return out


def _redact_name_value_list(items: Any, denied: frozenset, policy: RedactionPolicy,
                            always_sensitive: bool = False) -> Any:
    """A HAR `[{name, value}]` list. Cookie lists pass `always_sensitive`:
    cookies are named whatever the app likes, yet every value is a session
    credential. Twin of `redactNameValueList` in redact.ts."""
    if not isinstance(items, list):
        return items
    headers = policy.header_set()
    out = []
    for item in items:
        if not isinstance(item, dict):
            out.append(item)
            continue
        name = str(item.get("name") or "").lower()
        sensitive = always_sensitive or name in headers or _normalize_key(name) in denied
        out.append({**item, "value": REDACTED if sensitive else redact_text(item.get("value"), policy)})
    return out


def redact_har(har: Any, policy: RedactionPolicy = DEFAULT_POLICY) -> Any:
    """Scrub a parsed HAR, keeping its structure (it still opens in any HAR
    viewer and still replays). Request/response headers by name, every
    cookie value, query values by name, URLs and bodies by pattern. Used on
    upload for `har-replay` (WP15d). Twin of `redactHar` in redact.ts."""
    if not isinstance(har, dict) or not isinstance(har.get("log"), dict) \
            or not isinstance(har["log"].get("entries"), list):
        return har
    denied = policy.body_key_set()
    entries = []
    for entry in har["log"]["entries"]:
        if not isinstance(entry, dict):
            entries.append(entry)
            continue
        out = dict(entry)
        if isinstance(out.get("request"), dict):
            req = dict(out["request"])
            # Only lists that exist: an absent one stays absent (JS twin).
            for key, always in (("headers", False), ("cookies", True), ("queryString", False)):
                if key in req:
                    req[key] = _redact_name_value_list(req[key], denied, policy, always)
            if isinstance(req.get("url"), str):
                req["url"] = redact_text(req["url"], policy)
            post = req.get("postData")
            if isinstance(post, dict) and isinstance(post.get("text"), str):
                req["postData"] = {**post, "text": redact_body(post["text"], post.get("mimeType"), policy)}
            out["request"] = req
        if isinstance(out.get("response"), dict):
            res = dict(out["response"])
            for key, always in (("headers", False), ("cookies", True)):
                if key in res:
                    res[key] = _redact_name_value_list(res[key], denied, policy, always)
            content = res.get("content")
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                res["content"] = {**content, "text": redact_body(content["text"], content.get("mimeType"), policy)}
            out["response"] = res
        entries.append(out)
    return {**har, "log": {**har["log"], "entries": entries}}


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


# --------------------------------------------------------------------------
# DOM snapshots (selector heal) — twin of redact.ts:redactDom
# --------------------------------------------------------------------------

#: Cap on a redacted snapshot; the LLM prompt takes far less.
MAX_DOM_SNAPSHOT_CHARS = 200_000
_MAX_DOM_TEXT_CHARS = 200
_MAX_DOM_ATTR_CHARS = 200
_MAX_DOM_CLASSES = 12

_SECRET_NAME_RE = re.compile(
    r"passw(or)?d|passwd|\bpwd\b|secret|token|api[-_]?key|\botp\b|\bcvv\b|\bcvc\b|\bpin\b"
    r"|card[-_]?number|\bssn\b|csrf|xsrf|credential|private[-_]?key",
    re.IGNORECASE,
)
_SECRET_AUTOCOMPLETE_RE = re.compile(r"^(current-password|new-password|one-time-code|cc-.*)$", re.IGNORECASE)
_VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
})
_DROPPED_DOM_ELEMENTS = frozenset({"meta", "link", "base"})
_EMPTIED_DOM_ELEMENTS = frozenset({"script", "style", "noscript", "template", "textarea", "xmp"})
_URL_ATTRS = frozenset({"href", "src", "action", "formaction", "poster", "cite", "background"})
_DROPPED_ATTRS = frozenset({"srcdoc", "srcset", "nonce", "integrity", "content", "ping"})
_LABEL_INPUT_TYPES = frozenset({"submit", "button", "reset"})
_MASKED_KEEP_ATTRS = frozenset({
    "id", "class", "role", "name", "type", "for",
    "data-testid", "data-test", "data-test-id", "data-qa", "data-cy",
})
_HIDDEN_STYLE_RE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)


def _secret_name(value: Optional[str], denied: frozenset) -> bool:
    if not value:
        return False
    return bool(_SECRET_NAME_RE.search(value)) or _normalize_key(value) in denied


def _is_sensitive_element(attrs: Dict[str, str], denied: frozenset) -> bool:
    if (attrs.get("type") or "").lower() == "password":
        return True
    if _SECRET_AUTOCOMPLETE_RE.match(attrs.get("autocomplete") or ""):
        return True
    for name in ("id", "name", "autocomplete", "data-testid", "data-test", "data-qa", "aria-label"):
        if _secret_name(attrs.get(name), denied):
            return True
    return any(c and _SECRET_NAME_RE.search(c) for c in (attrs.get("class") or "").split())


def _parse_mask_selector(selector: str):
    """Last compound of each comma part, pseudo-classes dropped — matches a
    superset of the real selector, which is the safe direction for masking.
    None when unreadable. Twin of html-lite.ts:parseMaskSelector."""
    out = []
    for part in selector.split(","):
        segs = [s for s in re.split(r"\s*[\s>+~]\s*", part.strip()) if s]
        if not segs:
            continue
        s = re.sub(r"::?[a-zA-Z-]+(\([^)]*\))?", "", segs[-1])
        comp = {"tag": None, "ids": [], "classes": [], "attrs": []}
        m = re.match(r"([a-zA-Z][a-zA-Z0-9-]*|\*)", s)
        if m:
            if m.group(1) != "*":
                comp["tag"] = m.group(1).lower()
            s = s[len(m.group(1)):]
        pos = 0
        rx = re.compile(r"#([\w-]+)|\.([\w-]+)|\[\s*([\w:-]+)\s*(?:([~|^$*]?=)\s*(?:\"([^\"]*)\"|'([^']*)'|([^\]\s]+))\s*[is]?)?\s*\]")
        while pos < len(s):
            m = rx.match(s, pos)
            if not m:
                return None
            pos = m.end()
            if m.group(1):
                comp["ids"].append(m.group(1))
            elif m.group(2):
                comp["classes"].append(m.group(2))
            else:
                value = m.group(5) if m.group(5) is not None else (m.group(6) if m.group(6) is not None else (m.group(7) or ""))
                comp["attrs"].append((m.group(3).lower(), m.group(4), value))
        if not (comp["tag"] or comp["ids"] or comp["classes"] or comp["attrs"]):
            return None
        out.append(comp)
    return out or None


def _matches_compound(tag: str, attrs: Dict[str, str], comp) -> bool:
    if comp["tag"] and comp["tag"] != tag:
        return False
    if any(attrs.get("id") != i for i in comp["ids"]):
        return False
    classes = set((attrs.get("class") or "").split())
    if any(c not in classes for c in comp["classes"]):
        return False
    for name, op, value in comp["attrs"]:
        v = attrs.get(name)
        if v is None:
            return False
        if op is None:
            continue
        ok = {
            "=": v == value,
            "^=": v.startswith(value),
            "$=": v.endswith(value),
            "*=": value in v,
            "~=": value in v.split(),
            "|=": v == value or v.startswith(value + "-"),
        }.get(op, False)
        if not ok:
            return False
    return True


def _escape_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;")


def redact_dom(
    html: Optional[str],
    policy: RedactionPolicy = DEFAULT_POLICY,
    mask_selectors: Sequence[str] = (),
    max_chars: int = MAX_DOM_SNAPSHOT_CHARS,
) -> str:
    """Redact a page's HTML before it reaches an LLM or the heal snapshot store.

    Keeps what selectors are built from (tags, ids, classes, roles, aria-*,
    data-testid, names, placeholders, short visible text) and drops comments,
    <meta>/<link>/<base>, hidden inputs, script/style/noscript/template/
    textarea contents, input values (bar submit/button labels), text inside
    credential-looking elements and project mask selectors, event handlers,
    inline styles (a hidden marker survives), data: URIs, URL queries and
    attribute values over 200 chars; value patterns are scrubbed from the rest.
    Twin of execution-engine/src/core/redact.ts:redactDom.
    """
    from html.parser import HTMLParser

    if not isinstance(html, str) or not html:
        return ""
    denied = policy.body_key_set()
    masks = []
    for sel in mask_selectors or ():
        parsed = _parse_mask_selector(sel) if isinstance(sel, str) else None
        if parsed:
            masks.extend(parsed)

    out: list = []
    size = [0]
    # Open elements: (tag, suppress_text, inside_masked, emptied).
    stack: list = []

    def emit(s: str):
        out.append(s)
        size[0] += len(s)

    def ends_with_space() -> bool:
        return bool(out) and out[-1].endswith(" ")

    class _P(HTMLParser):
        def handle_decl(self, decl):
            emit("<!DOCTYPE html>")

        def handle_comment(self, data):
            pass

        def handle_data(self, data):
            if stack and stack[-1][1]:
                return
            text = re.sub(r"[\s\u00a0]+", " ", data)
            if not text.strip():
                if text and not ends_with_space():
                    emit(" ")
                return
            if len(text) > _MAX_DOM_TEXT_CHARS:
                text = text[:_MAX_DOM_TEXT_CHARS] + "…"
            emit(_escape_text(redact_text(text, policy)))

        def handle_endtag(self, tag):
            if stack and stack[-1][3] and stack[-1][0] != tag:
                return
            if tag in _DROPPED_DOM_ELEMENTS or tag in _VOID_ELEMENTS:
                return
            tags = [t[0] for t in stack]
            if tag not in tags:
                return
            at = len(tags) - 1 - tags[::-1].index(tag)
            del stack[at:]
            emit(f"</{tag}>")

        def handle_startendtag(self, tag, attrs):
            self._start(tag, attrs, True)

        def handle_starttag(self, tag, attrs):
            self._start(tag, attrs, False)

        def _start(self, tag, attr_list, self_closing):
            if stack and stack[-1][3]:
                return  # markup inside <noscript>/<template> is not rendered
            attrs = {}
            for k, v in attr_list:
                attrs.setdefault(k, v if v is not None else "")
            typ = (attrs.get("type") or "").lower()
            if tag in _DROPPED_DOM_ELEMENTS or (tag == "input" and typ == "hidden"):
                return
            in_masked = bool(stack) and stack[-1][2]
            masked = in_masked or any(_matches_compound(tag, attrs, c) for c in masks)
            sensitive = _is_sensitive_element(attrs, denied)
            parts = []
            seen = set()
            for name, raw in attr_list:
                if name in seen:
                    continue
                seen.add(name)
                value = raw if raw is not None else ""
                if masked and name not in _MASKED_KEEP_ATTRS:
                    continue
                if name.startswith("on") or name in _DROPPED_ATTRS:
                    continue
                if name == "value" and not (tag == "input" and typ in _LABEL_INPUT_TYPES and not sensitive):
                    continue
                if name == "style":
                    if not _HIDDEN_STYLE_RE.search(value):
                        continue
                    value = "display:none"
                elif name != "type" and _secret_name(re.sub(r"^data-", "", name), denied):
                    continue
                elif re.match(r"^\s*data:", value, re.IGNORECASE):
                    value = "data:[REDACTED]"
                elif name in _URL_ATTRS:
                    value = re.split(r"[?#]", value, maxsplit=1)[0]
                elif name == "class":
                    value = " ".join(value.split()[:_MAX_DOM_CLASSES])
                if len(value) > _MAX_DOM_ATTR_CHARS:
                    continue
                value = redact_text(value, policy)
                parts.append(name if raw is None and name != "value" else f'{name}="{_escape_attr(value)}"')
            void = tag in _VOID_ELEMENTS
            emit(f"<{tag}{(' ' + ' '.join(parts)) if parts else ''}{'/' if self_closing and not void else ''}>")
            if void or self_closing:
                return
            suppress = (bool(stack) and stack[-1][1]) or masked or sensitive or tag in _EMPTIED_DOM_ELEMENTS
            stack.append((tag, suppress, masked, tag in ("noscript", "template", "xmp")))

    parser = _P(convert_charrefs=True)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — malformed markup: keep what was emitted
        pass
    text = "".join(out)
    return text[:max_chars]
