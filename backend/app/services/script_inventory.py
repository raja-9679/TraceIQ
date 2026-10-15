"""Third-party script inventory + defacement detection (WP9) — pure logic.

PCI DSS 4.0 6.4.3 asks for an inventory of every script on a payment page,
each with a written justification and a way to assure its integrity; 11.6.1
asks for detection of unauthorised changes to the page's scripts and security
headers at least weekly. This module turns one page observation into:

* script entries with a stable identity (`script_key`) and a SHA-384 in the
  same `sha384-<base64>` form an SRI attribute uses,
* a diff against the stored inventory (added / changed / removed),
* a diff of the security headers,
* defacement signals: text similarity against a baseline, forbidden keywords,
  per-element checks.

Keyword and element checks are plain case-insensitive substring tests. Never
regexes: the page under test is outsider-controlled content.
"""
from __future__ import annotations

import base64
import hashlib
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

SECURITY_HEADERS = (
    "content-security-policy", "content-security-policy-report-only",
    "strict-transport-security", "x-frame-options", "x-content-type-options",
    "referrer-policy", "permissions-policy",
)

# <script type> values a browser executes. Anything else (application/json,
# ld+json, templates, importmap…) is data and stays out of the inventory.
_JS_TYPES = {"text/javascript", "application/javascript", "module", "text/ecmascript",
             "application/ecmascript", "application/x-javascript"}

# Two-label public suffixes common enough to matter for first/third-party
# classification. Without a full PSL, "thehindu.co.in" would otherwise be
# treated as a subdomain of "co.in".
_TWO_LABEL_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "co.in", "net.in", "org.in", "gov.in", "ac.in",
    "com.au", "net.au", "org.au", "co.nz", "co.za", "com.br", "co.jp", "com.sg", "com.my",
    "com.mx", "com.tr", "com.cn", "com.hk", "co.kr", "com.ar", "co.id",
}


def sri_sha384(data: bytes) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode()


def site_of(host: str) -> str:
    """Registrable-domain approximation: last two labels, or three under a
    known two-label public suffix."""
    labels = (host or "").lower().rstrip(".").split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _TWO_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def is_third_party(script_url: str, page_url: str) -> bool:
    try:
        sh = (urlsplit(script_url).hostname or "").lower()
        ph = (urlsplit(page_url).hostname or "").lower()
    except ValueError:
        return True
    return not sh or site_of(sh) != site_of(ph)


def script_key_for(src: str) -> str:
    """scheme://host/path — the query is dropped so a cache-buster shows up as
    a change to a known script instead of a new one plus a removal."""
    p = urlsplit(src)
    return f"{p.scheme}://{(p.hostname or '').lower()}{p.path or '/'}"


def build_entries(raw_scripts: Iterable[Dict[str, Any]], page_url: str,
                  external_hashes: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Normalise observed scripts.

    raw: {"src", "inline" | "inline_sha384", "integrity", "type"} — the worker
    sends `inline_sha384` (hashed at capture) rather than inline source, so a
    token embedded in an inline config script never leaves the worker.
    external_hashes: absolute script URL -> sha384 of the body as fetched.
    """
    external_hashes = external_hashes or {}
    out: Dict[str, Dict[str, Any]] = {}
    for s in raw_scripts:
        stype = (s.get("type") or "").strip().lower()
        if stype and stype not in _JS_TYPES:
            continue
        src = (s.get("src") or "").strip()
        if src:
            if src.lower().startswith(("data:", "blob:", "javascript:")):
                key = "embedded:" + sri_sha384(src.encode())
                entry = {"script_key": key, "kind": "inline", "src": src[:200], "sha384": key.split(":", 1)[1],
                         "integrity": None, "third_party": False}
            else:
                absolute = urljoin(page_url, src)
                entry = {"script_key": script_key_for(absolute), "kind": "external", "src": absolute[:2000],
                         "sha384": external_hashes.get(absolute), "integrity": (s.get("integrity") or None),
                         "third_party": is_third_party(absolute, page_url)}
        elif s.get("inline_sha384"):
            digest = str(s["inline_sha384"])[:120]
            entry = {"script_key": "inline:" + digest, "kind": "inline", "src": None, "sha384": digest,
                     "integrity": None, "third_party": False}
        else:
            body = (s.get("inline") or "")
            if not body.strip():
                continue
            digest = sri_sha384(body.encode())
            entry = {"script_key": "inline:" + digest, "kind": "inline", "src": None, "sha384": digest,
                     "integrity": None, "third_party": False}
        out.setdefault(entry["script_key"], entry)
    return list(out.values())


def diff_inventory(known: Iterable[Dict[str, Any]], current: Iterable[Dict[str, Any]]
                   ) -> Dict[str, List[Dict[str, Any]]]:
    """known: stored scripts that were present last time ({script_key, sha384, src}).
    A script is *changed* when its content hash or exact URL moved; a script
    whose body could not be hashed this time is compared by URL only."""
    known_by = {k["script_key"]: k for k in known}
    cur_by = {c["script_key"]: c for c in current}
    added = [c for k, c in cur_by.items() if k not in known_by]
    removed = [k for key, k in known_by.items() if key not in cur_by]
    changed = []
    for key, c in cur_by.items():
        k = known_by.get(key)
        if not k:
            continue
        hash_moved = bool(c.get("sha384") and k.get("sha384") and c["sha384"] != k["sha384"])
        url_moved = bool(c.get("src") and k.get("src") and c["src"] != k["src"])
        if hash_moved or url_moved:
            changed.append({**c, "previous_sha384": k.get("sha384"), "previous_src": k.get("src")})
    return {"added": added, "changed": changed, "removed": removed}


def security_headers(headers: Dict[str, str]) -> Dict[str, str]:
    low = {k.lower(): v for k, v in (headers or {}).items()}
    return {h: low[h] for h in SECURITY_HEADERS if h in low}


def diff_headers(old: Optional[Dict[str, str]], new: Dict[str, str]) -> List[Dict[str, Any]]:
    if old is None:
        return []
    out = []
    for h in SECURITY_HEADERS:
        a, b = old.get(h), new.get(h)
        if a != b:
            out.append({"header": h, "before": a, "after": b,
                        "change": "added" if a is None else "removed" if b is None else "changed"})
    return out


# ---------------------------------------------------------------------------
# Defacement
# ---------------------------------------------------------------------------

def normalise_text(text: str) -> str:
    """Lower-case, digits folded to 0 (dates, counters and prices churn
    constantly and are not defacement), whitespace collapsed."""
    out = []
    for ch in (text or "").lower():
        out.append("0" if ch.isdigit() else ch)
    return " ".join("".join(out).split())


def keyword_hits(text: str, keywords: Iterable[str]) -> List[str]:
    low = (text or "").lower()
    return [k for k in dict.fromkeys(k.strip() for k in keywords or [] if k and k.strip())
            if k.lower() in low]


def evaluate_element_checks(checks: Iterable[Dict[str, Any]], element_texts: Dict[str, Optional[str]]
                            ) -> List[Dict[str, Any]]:
    """Failures only. element_texts: selector -> innerText (None = no match)."""
    failures = []
    for c in checks or []:
        sel = (c.get("selector") or "").strip()
        if not sel:
            continue
        text = element_texts.get(sel)
        if text is None:
            failures.append({"selector": sel, "reason": "element not found"})
            continue
        low = text.lower()
        must = (c.get("must_contain") or "").strip()
        if must and must.lower() not in low:
            failures.append({"selector": sel, "reason": f"does not contain {must!r}"})
        for bad in c.get("must_not_contain") or []:
            if bad and bad.strip() and bad.strip().lower() in low:
                failures.append({"selector": sel, "reason": f"contains forbidden {bad.strip()!r}"})
    return failures


def defacement_signals(page: Any, text: str, element_texts: Dict[str, Optional[str]]
                       ) -> Tuple[Optional[float], List[str], List[Dict[str, Any]]]:
    from app.services.estate_checks import text_similarity
    norm = normalise_text(text)
    sim = None
    if page.content_baseline is not None:
        sim = round(text_similarity(page.content_baseline, norm), 3)
    return sim, keyword_hits(text, page.forbidden_keywords or []), \
        evaluate_element_checks(page.element_checks or [], element_texts)
