"""Failure fingerprinting for triage/de-duplication (PLATFORM_VISION.md §5).

Turns a raw error message into a stable *signature* so identical root causes
across many runs collapse into one triageable cluster (Sentry-style). The
message is normalized — volatile bits (numbers, ids, uuids, urls, quoted
selectors/values, timestamps, memory addresses) are masked — then hashed. Also
classifies the failure into a coarse category for filtering.

Pure functions only, so the rules are unit-tested without a DB.
"""
import hashlib
import re
from typing import Optional, Tuple

# Volatile substrings to mask before hashing (order matters).
_MASKS = [
    (re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"), "<uuid>"),
    (re.compile(r"https?://[^\s'\"]+"), "<url>"),
    (re.compile(r"\([^)]*\)"), " "),                     # drop parentheticals (volatile detail)
    (re.compile(r"(?:[A-Za-z]:)?(?:/[\w.\-]+){2,}/?"), "<path>"),  # file paths
    (re.compile(r"0x[0-9a-fA-F]+"), "<addr>"),
    (re.compile(r'"[^"]*"'), "<str>"),
    (re.compile(r"'[^']*'"), "<str>"),
    (re.compile(r"\b[0-9a-fA-F]{12,}\b"), "<hex>"),
    (re.compile(r"\b\d+(?:\.\d+)?(?:ms|s|px)?\b"), "<n>"),
    (re.compile(r"\s+"), " "),
]

# Category detection (checked against the ORIGINAL message, first match wins).
# Network requires explicit protocol/status context — bare 3-digit numbers used
# to false-match here (e.g. a diffRatio value), splitting/miscategorizing.
_CATEGORIES = [
    ("selector", re.compile(r"waiting for locator|waitforselector|strict mode violation|no element|not found|failed to find element|locator resolved to", re.I)),
    ("assertion", re.compile(r"expect\(|toBe|toHave|toEqual|assertion|diffratio|visual regression|expected .* received", re.I)),
    ("timeout", re.compile(r"timeout|timed out|exceeded", re.I)),
    ("network", re.compile(r"net::|ECONN|ENOTFOUND|fetch failed|request failed|status code|http \d{3}|status \d{3}", re.I)),
    ("navigation", re.compile(r"page\.goto|navigat|page crash|ERR_ABORTED", re.I)),
]


def classify(error_message: Optional[str]) -> str:
    msg = error_message or ""
    for name, rx in _CATEGORIES:
        if rx.search(msg):
            return name
    return "other"


def _normalize(error_message: str) -> str:
    # Use the first meaningful line — Playwright errors put the call/expectation
    # on line 1 and volatile context below.
    first_line = (error_message or "").strip().splitlines()[0] if error_message else ""
    text = first_line
    for rx, repl in _MASKS:
        text = rx.sub(repl, text)
    return text.strip()[:300]


def compute_signature(error_message: Optional[str], test_name: Optional[str] = None) -> Tuple[str, str, str]:
    """Return (signature, title, category).

    `signature` is a stable 16-char hash of the normalized error (plus category,
    so different failure *kinds* with coincidentally-similar text don't merge).
    `title` is the human-readable normalized error. `category` is the coarse kind.
    """
    category = classify(error_message)
    normalized = _normalize(error_message or "") or "(no error message)"
    basis = f"{category}|{normalized}"
    signature = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
    return signature, normalized, category
