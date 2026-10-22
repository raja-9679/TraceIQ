"""Journey coverage from an analytics import (WP12c, roadmap A4 / decision R6).

A project uploads a generic page-view export (CSV or JSON — no vendor format)
and gets back which of its real traffic the test cases visit:

* **Normalise** every path the same way on both sides: scheme/host, query
  string, fragment and trailing slash go; duplicate slashes collapse. A goto
  value that starts with a template token (`{{env.BASE}}/cart`) loses it.
* **Path rules** (user-editable, per project) fold concrete paths into one key
  — `/product/{id}` matches `/product/123`. Two kinds:
    - `pattern`: segment wildcards. `{name}` or `*` = exactly one segment,
      `**` = any number of segments, `*` inside a segment = any characters.
      Matched by hand-written loops (no regex at all).
    - `regex`: compiled with RE2 (linear time). Never Python `re` — a user
      pattern on Python `re` can backtrack for hours.
  First matching rule wins; the key is the rule's `name` or its pattern.
* **Coverage** = views of keys some case visits / all views. Plus the top
  uncovered keys by views, tested keys with little or no traffic, and
  per-journey (funnel) step coverage.

Parsing is defensive: size and row caps are checked before work is done,
decoding errors and JSON nesting bombs are reported, and nothing in the file
is evaluated.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 50_000
MAX_PATH_LEN = 2048
MAX_NAME_LEN = 200
MAX_RULES = 100
MAX_RULE_LEN = 300
MAX_RULE_SEGMENTS = 30
MAX_IMPORTS_KEPT = 20
DEFAULT_LOW_TRAFFIC_SHARE = 0.005   # under 0.5% of all views


class JourneyImportError(ValueError):
    """The upload or the rule set is unusable; the message says why."""


# ---------------------------------------------------------------------------
# Path normalisation
# ---------------------------------------------------------------------------

def normalize_path(raw: Any) -> Optional[str]:
    """'https://shop.example.com/Cart/?a=1#x' -> '/Cart'. None when empty.

    Case is kept (paths are case-sensitive); hosts are dropped, so traffic and
    tests are compared by path whichever environment the test targets."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = s[:MAX_PATH_LEN]
    # Leading template tokens: {{env.BASE_URL}}/cart, {{base}}{{prefix}}/x.
    while s.startswith("{{"):
        end = s.find("}}")
        if end < 0:
            break
        s = s[end + 2:].lstrip()
    for sep in ("#", "?"):
        s = s.split(sep, 1)[0]
    lower = s.lower()
    if "://" in lower[:12]:
        rest = s.split("://", 1)[1]
        slash = rest.find("/")
        s = rest[slash:] if slash >= 0 else "/"
    elif s.startswith("//"):
        rest = s[2:]
        slash = rest.find("/")
        s = rest[slash:] if slash >= 0 else "/"
    if not s.startswith("/"):
        s = "/" + s
    parts = [p for p in s.split("/") if p != ""]
    return "/" + "/".join(parts)


def _segments(path: str) -> List[str]:
    return [p for p in path.split("/") if p != ""]


# ---------------------------------------------------------------------------
# Path rules
# ---------------------------------------------------------------------------

def _wild(pat: str, text: str) -> bool:
    """`*` = any run of characters within one segment. Iterative two-pointer
    match with single backtrack point: O(len(pat) * len(text)) worst case,
    never exponential."""
    p = t = 0
    star = -1
    mark = 0
    while t < len(text):
        if p < len(pat) and pat[p] != "*" and pat[p] == text[t]:
            p += 1
            t += 1
        elif p < len(pat) and pat[p] == "*":
            star, mark = p, t
            p += 1
        elif star >= 0:
            p = star + 1
            mark += 1
            t = mark
        else:
            return False
    while p < len(pat) and pat[p] == "*":
        p += 1
    return p == len(pat)


def _segment_matches(pat: str, seg: str) -> bool:
    if pat == "*" or (pat.startswith("{") and pat.endswith("}") and len(pat) > 2):
        return True
    if "*" in pat:
        return _wild(pat, seg)
    return pat == seg


def _pattern_matches(pat_segs: Sequence[str], segs: Sequence[str]) -> bool:
    """Segment pattern match with `**` = zero or more segments. Dynamic
    programming over (pattern, path) positions — bounded by
    MAX_RULE_SEGMENTS x path segments."""
    n, m = len(pat_segs), len(segs)
    # reach[j] = pattern prefix i matches path prefix j
    reach = [False] * (m + 1)
    reach[0] = True
    for i in range(n):
        ps = pat_segs[i]
        nxt = [False] * (m + 1)
        if ps == "**":
            seen = False
            for j in range(m + 1):
                seen = seen or reach[j]
                nxt[j] = seen
        else:
            for j in range(m):
                if reach[j] and _segment_matches(ps, segs[j]):
                    nxt[j + 1] = True
        reach = nxt
        if not any(reach):
            return False
    return reach[m]


@dataclass
class CompiledRule:
    key: str
    kind: str
    segs: Optional[List[str]] = None
    regex: Any = None

    def matches(self, path: str) -> bool:
        if self.kind == "regex":
            return bool(self.regex.search(path))
        return _pattern_matches(self.segs or [], _segments(path))


def _re2():
    import re2  # google-re2; linear-time, no backtracking
    return re2


def validate_rules(rules: Any) -> List[Dict[str, Any]]:
    """Clean a rule list from the API; raises JourneyImportError."""
    if rules is None:
        return []
    if not isinstance(rules, list):
        raise JourneyImportError("path_rules must be a list")
    if len(rules) > MAX_RULES:
        raise JourneyImportError(f"at most {MAX_RULES} path rules")
    out: List[Dict[str, Any]] = []
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            raise JourneyImportError(f"rule {i + 1}: must be an object")
        pattern = str(r.get("pattern") or "").strip()
        kind = str(r.get("kind") or "pattern").strip().lower()
        name = (str(r.get("name")).strip() if r.get("name") else "") or None
        if not pattern:
            raise JourneyImportError(f"rule {i + 1}: pattern is empty")
        if len(pattern) > MAX_RULE_LEN or (name and len(name) > MAX_RULE_LEN):
            raise JourneyImportError(f"rule {i + 1}: longer than {MAX_RULE_LEN} characters")
        if kind not in ("pattern", "regex"):
            raise JourneyImportError(f"rule {i + 1}: kind must be 'pattern' or 'regex'")
        if kind == "pattern":
            if not pattern.startswith("/"):
                raise JourneyImportError(f"rule {i + 1}: a path pattern starts with '/'")
            if len(_segments(pattern)) > MAX_RULE_SEGMENTS:
                raise JourneyImportError(f"rule {i + 1}: more than {MAX_RULE_SEGMENTS} segments")
            pattern = "/" + "/".join(_segments(pattern))
        else:
            _compile_regex(pattern, i)
        out.append({"pattern": pattern, "kind": kind, **({"name": name} if name else {})})
    return out


def _compile_regex(pattern: str, index: int):
    try:
        return _re2().compile(pattern)
    except ImportError:  # pragma: no cover - the image always has it
        raise JourneyImportError("regex rules need the RE2 engine, which is not installed")
    except Exception as exc:  # re2.error and unsupported syntax
        raise JourneyImportError(
            f"rule {index + 1}: not a valid RE2 expression ({exc}); "
            "backreferences and lookaround are not supported")


def compile_rules(rules: Optional[Iterable[Dict[str, Any]]]) -> List[CompiledRule]:
    """Compile stored (already validated) rules. A rule that no longer
    compiles is skipped rather than failing the whole report."""
    out: List[CompiledRule] = []
    for i, r in enumerate(rules or []):
        if not isinstance(r, dict) or not r.get("pattern"):
            continue
        key = r.get("name") or r["pattern"]
        if r.get("kind") == "regex":
            try:
                out.append(CompiledRule(key=key, kind="regex",
                                        regex=_compile_regex(r["pattern"], i)))
            except JourneyImportError:
                continue
        else:
            out.append(CompiledRule(key=key, kind="pattern", segs=_segments(r["pattern"])))
    return out


def canonical_key(path: str, rules: Sequence[CompiledRule]) -> str:
    for rule in rules:
        if rule.matches(path):
            return rule.key
    return path


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# GA4's report export names its dimension "Page path and screen class" (or
# "Page path + query string") and its metric "Views"; the API and Explorations
# call the metric "Screen page views". Headers are compared after _norm_header.
_PATH_COLS = ("path", "url", "page", "page_path", "pagepath", "page_url", "landing_page",
              "page_location", "pathname", "route", "page_path_and_screen_class",
              "page_path___query_string", "page_path___query_string_and_screen_class",
              "pagepathplusquerystring")
_VIEW_COLS = ("views", "pageviews", "page_views", "screenpageviews", "screen_page_views",
              "count", "hits", "sessions", "visits", "users")
_JOURNEY_COLS = ("journey", "funnel", "journey_name", "funnel_name", "flow")
_STEP_COLS = ("step", "step_order", "order", "position", "step_number", "funnel_step")
_FROM_COLS = ("date", "day", "date_from", "start_date", "period_start", "from")
_TO_COLS = ("date_to", "end_date", "period_end", "to")


@dataclass
class ParsedImport:
    source_format: str
    rows: List[Dict[str, Any]]           # aggregated
    row_count: int
    skipped_rows: int
    total_views: int
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


def _norm_header(h: Any) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(h or "").strip().lower()).strip("_")


def _pick(headers: Dict[str, Any], names: Sequence[str]) -> Optional[Any]:
    for n in names:
        if n in headers:
            return headers[n]
    return None


def _parse_views(raw: Any) -> Optional[int]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        v = raw
    else:
        s = str(raw).strip().replace(",", "").replace("_", "").replace(" ", "")
        if not s or len(s) > 20:
            return None
        try:
            v = float(s)
        except ValueError:
            return None
    if v != v or v < 0 or v > 1e15:   # NaN, negative, absurd
        return None
    return int(v)


def _parse_step(raw: Any) -> Optional[int]:
    v = _parse_views(raw)
    return v if v is not None and v <= 10_000 else None


def _parse_date(raw: Any) -> Optional[str]:
    if raw is None or raw == "":
        return None
    s = str(raw).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s if fmt == "%Y-%m-%d" else s[:8], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _clean_name(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    return s[:MAX_NAME_LEN] or None


def _detect_format(data: bytes, fmt: Optional[str], filename: Optional[str]) -> str:
    if fmt:
        f = fmt.strip().lower()
        if f not in ("csv", "json"):
            raise JourneyImportError("format must be 'csv' or 'json'")
        return f
    name = (filename or "").lower()
    if name.endswith(".json"):
        return "json"
    if name.endswith((".csv", ".tsv", ".txt")):
        return "csv"
    head = data[:64].lstrip(b"\xef\xbb\xbf \t\r\n")
    return "json" if head[:1] in (b"[", b"{") else "csv"


def _strip_preamble(text: str) -> str:
    """GA4 exports open with `#` comment lines (report name, property, date
    range) and a blank line before the header row; drop them."""
    lines = text.split("\n")
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith("#")):
        i += 1
    return "\n".join(lines[i:])


def _records_from_csv(text: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    text = _strip_preamble(text)
    first = text.split("\n", 1)[0]
    delim = max((",", "\t", ";"), key=first.count)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    try:
        header = next(reader)
    except StopIteration:
        raise JourneyImportError("the file is empty")
    except csv.Error as exc:
        raise JourneyImportError(f"not readable as CSV ({exc})")
    keys = [_norm_header(h) for h in header]
    records: List[Dict[str, Any]] = []
    try:
        for row in reader:
            if not any(c.strip() for c in row):
                continue
            if len(records) >= MAX_IMPORT_ROWS:
                raise JourneyImportError(
                    f"more than {MAX_IMPORT_ROWS} data rows; aggregate or split the export")
            records.append({k: (row[i] if i < len(row) else None) for i, k in enumerate(keys)})
    except csv.Error as exc:
        raise JourneyImportError(f"not readable as CSV ({exc})")
    return records, keys


def _records_from_json(text: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    try:
        doc = json.loads(text)
    except RecursionError:
        raise JourneyImportError("JSON is nested too deeply")
    except ValueError as exc:
        raise JourneyImportError(f"not valid JSON ({exc})")
    meta: Dict[str, Any] = {}
    if isinstance(doc, dict):
        meta = doc
        doc = doc.get("rows") if "rows" in doc else doc.get("data")
    if not isinstance(doc, list):
        raise JourneyImportError("JSON must be a list of rows, or an object with a 'rows' list")
    if len(doc) > MAX_IMPORT_ROWS:
        raise JourneyImportError(
            f"more than {MAX_IMPORT_ROWS} data rows; aggregate or split the export")
    records = []
    for item in doc:
        if isinstance(item, dict):
            records.append({_norm_header(k): v for k, v in item.items()
                            if not isinstance(v, (dict, list))})
        else:
            records.append({})
    return records, meta


def parse_import(data: bytes, fmt: Optional[str] = None,
                 filename: Optional[str] = None) -> ParsedImport:
    if len(data) > MAX_IMPORT_BYTES:
        raise JourneyImportError(f"file is larger than {MAX_IMPORT_BYTES // (1024 * 1024)} MB")
    if not data.strip():
        raise JourneyImportError("the file is empty")
    source_format = _detect_format(data, fmt, filename)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise JourneyImportError("the file is not UTF-8 text")
    if "\x00" in text:
        raise JourneyImportError("the file contains NUL bytes; not a text export")

    warnings: List[str] = []
    meta: Dict[str, Any] = {}
    if source_format == "csv":
        records, keys = _records_from_csv(text)
        header_keys = set(keys)
    else:
        records, meta = _records_from_json(text)
        header_keys = set().union(*[r.keys() for r in records]) if records else set()

    path_col = next((c for c in _PATH_COLS if c in header_keys), None)
    if not path_col:
        raise JourneyImportError(
            "no path column; name one of: " + ", ".join(_PATH_COLS[:6]))
    views_col = next((c for c in _VIEW_COLS if c in header_keys), None)
    if not views_col:
        warnings.append("no views column: every row counts as 1 view")
    journey_col = next((c for c in _JOURNEY_COLS if c in header_keys), None)
    step_col = next((c for c in _STEP_COLS if c in header_keys), None)
    from_col = next((c for c in _FROM_COLS if c in header_keys), None)
    to_col = next((c for c in _TO_COLS if c in header_keys), None)

    agg: Dict[Tuple[str, Optional[str], Optional[int]], int] = {}
    skipped = 0
    dates: List[str] = []
    for rec in records:
        path = normalize_path(rec.get(path_col))
        views = _parse_views(rec.get(views_col)) if views_col else 1
        if not path or views is None:
            skipped += 1
            continue
        journey = _clean_name(rec.get(journey_col)) if journey_col else None
        step = _parse_step(rec.get(step_col)) if step_col else None
        key = (path, journey, step if journey else None)
        agg[key] = agg.get(key, 0) + views
        for col in (from_col, to_col):
            if col:
                d = _parse_date(rec.get(col))
                if d:
                    dates.append(d)
    if not agg:
        raise JourneyImportError("no usable rows (each row needs a path and a non-negative view count)")
    if skipped:
        warnings.append(f"{skipped} row(s) skipped: missing path or unreadable views")

    period_start = _parse_date(meta.get("period_start") or meta.get("date_from")) or (
        min(dates) if dates else None)
    period_end = _parse_date(meta.get("period_end") or meta.get("date_to")) or (
        max(dates) if dates else None)
    rows = [{"path": p, "views": v, **({"journey": j} if j else {}),
             **({"step": s} if s is not None else {})}
            for (p, j, s), v in agg.items()]
    rows.sort(key=lambda r: -r["views"])
    # Page totals come from page rows; journey rows are funnel steps that may
    # repeat the same views, so they only count when the file has no page rows.
    page_rows = [r for r in rows if "journey" not in r]
    total = sum(r["views"] for r in (page_rows or rows))
    return ParsedImport(source_format=source_format, rows=rows, row_count=len(records),
                        skipped_rows=skipped, total_views=total,
                        period_start=period_start, period_end=period_end, warnings=warnings)


# ---------------------------------------------------------------------------
# What the cases visit
# ---------------------------------------------------------------------------

def goto_urls(steps: Any) -> List[str]:
    """goto targets of a case — the same thing `GET /apps/{id}/surface` lists
    as routes_covered."""
    out = []
    for step in steps or []:
        if isinstance(step, dict) and step.get("type") == "goto" and step.get("value"):
            out.append(str(step["value"]))
    return out


def covered_keys(cases: Iterable[Tuple[int, Any]],
                 rules: Sequence[CompiledRule]) -> Dict[str, List[int]]:
    """{canonical key: [case ids that visit it]} from (case_id, steps) pairs."""
    out: Dict[str, List[int]] = {}
    for case_id, steps in cases:
        for url in goto_urls(steps):
            path = normalize_path(url)
            if not path:
                continue
            ids = out.setdefault(canonical_key(path, rules), [])
            if case_id not in ids:
                ids.append(case_id)
    return out


# ---------------------------------------------------------------------------
# Coverage report
# ---------------------------------------------------------------------------

def _pct(part: float, whole: float) -> Optional[float]:
    return round(100.0 * part / whole, 1) if whole else None


def compute_coverage(rows: Sequence[Dict[str, Any]], covered: Dict[str, List[int]],
                     rules: Sequence[CompiledRule], *, top: int = 50,
                     low_traffic_share: float = DEFAULT_LOW_TRAFFIC_SHARE) -> Dict[str, Any]:
    page_rows = [r for r in rows if not r.get("journey")]
    source = page_rows or list(rows)
    traffic: Dict[str, int] = {}
    members: Dict[str, set] = {}
    for r in source:
        key = canonical_key(r["path"], rules)
        traffic[key] = traffic.get(key, 0) + int(r.get("views") or 0)
        members.setdefault(key, set()).add(r["path"])
    total = sum(traffic.values())
    covered_views = sum(v for k, v in traffic.items() if k in covered)

    uncovered = sorted(((k, v) for k, v in traffic.items() if k not in covered),
                       key=lambda kv: -kv[1])
    threshold = total * low_traffic_share
    low = sorted(((k, traffic.get(k, 0)) for k in covered if traffic.get(k, 0) <= threshold),
                 key=lambda kv: kv[1])

    def entry(k: str, v: int) -> Dict[str, Any]:
        return {"key": k, "views": v, "share_pct": _pct(v, total),
                "paths": len(members.get(k, ())), "case_ids": covered.get(k, [])}

    journeys = _journey_coverage([r for r in rows if r.get("journey")], covered, rules)
    return {
        "total_views": total,
        "covered_views": covered_views,
        "coverage_pct": _pct(covered_views, total),
        "distinct_keys": len(traffic),
        "covered_keys": sum(1 for k in traffic if k in covered),
        "tested_keys": len(covered),
        "top_uncovered": [entry(k, v) for k, v in uncovered[:top]],
        "covered_low_traffic": [entry(k, v) for k, v in low[:top]],
        "covered": sorted((entry(k, traffic.get(k, 0)) for k in covered),
                          key=lambda e: -e["views"])[:top],
        "low_traffic_share_pct": round(low_traffic_share * 100, 2),
        "journeys": journeys,
    }


def _journey_coverage(rows: Sequence[Dict[str, Any]], covered: Dict[str, List[int]],
                      rules: Sequence[CompiledRule]) -> List[Dict[str, Any]]:
    by_journey: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for r in rows:
        key = canonical_key(r["path"], rules)
        steps = by_journey.setdefault(r["journey"], {})
        s = steps.setdefault(key, {"key": key, "step": r.get("step"), "views": 0})
        s["views"] += int(r.get("views") or 0)
        if r.get("step") is not None and (s["step"] is None or r["step"] < s["step"]):
            s["step"] = r["step"]
    out = []
    for name, steps in by_journey.items():
        ordered = sorted(steps.values(), key=lambda s: (s["step"] is None, s["step"] or 0, -s["views"]))
        for s in ordered:
            s["covered"] = s["key"] in covered
            s["case_ids"] = covered.get(s["key"], [])
        views = sum(s["views"] for s in ordered)
        cov_views = sum(s["views"] for s in ordered if s["covered"])
        gap = next((s for s in ordered if not s["covered"]), None)
        out.append({
            "journey": name,
            "steps": ordered,
            "step_count": len(ordered),
            "covered_steps": sum(1 for s in ordered if s["covered"]),
            "coverage_pct": _pct(sum(1 for s in ordered if s["covered"]), len(ordered)),
            "weighted_coverage_pct": _pct(cov_views, views),
            "views": views,
            "first_gap": gap["key"] if gap else None,
        })
    out.sort(key=lambda j: -j["views"])
    return out


def journey_traffic(rows: Sequence[Dict[str, Any]], journey: str,
                    covered: Dict[str, List[int]], rules: Sequence[CompiledRule]
                    ) -> Optional[Dict[str, Any]]:
    """One named journey's traffic + coverage from an import, or None."""
    for j in _journey_coverage([r for r in rows if r.get("journey") == journey], covered, rules):
        return {k: j[k] for k in ("views", "coverage_pct", "weighted_coverage_pct",
                                   "step_count", "covered_steps", "first_gap")}
    return None


def keys_traffic(rows: Sequence[Dict[str, Any]], keys: Iterable[str],
                 rules: Sequence[CompiledRule]) -> int:
    """Total page views of the given canonical keys (page rows only)."""
    wanted = set(keys)
    page_rows = [r for r in rows if not r.get("journey")] or list(rows)
    return sum(int(r.get("views") or 0) for r in page_rows
               if canonical_key(r["path"], rules) in wanted)
