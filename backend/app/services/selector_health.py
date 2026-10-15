"""Selector-health report (WP10): which selectors keep breaking.

Built only from data the platform already has:
- SelectorHealProposal rows (reactive/runtime/proactive heal suggestions),
- per-step evidence on results (`result_payload.step_evidence`): steps that
  failed or were runtime-healed, with the selector they used and — at capture
  level standard+ — a sanitised DOM excerpt around the target.

For each (case, selector) it counts heals and failures per ISO week, explains
why the selector looks fragile, and suggests sturdier locators (test id, ARIA
role + accessible name, stable id) when the DOM excerpt shows the target
element. Suggestions are text for a human to apply — nothing here edits a case.

Pure functions; the API module does the queries.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Tuple

MAX_ENTRIES = 200
TARGET_MARK = "data-traceiq-target"   # set by the worker's DOM excerpt (evidence.ts)
TEST_ID_ATTRS = ("data-testid", "data-test-id", "data-test", "data-qa", "data-cy")

# Generated class / id shapes: CSS-in-JS hashes, build hashes, framework ids.
_GENERATED_TOKEN = re.compile(
    r"(^|[-_])(css|sc|jsx|emotion|styled|makeStyles|Mui[A-Za-z]+)-[a-z0-9]{4,}|"
    r"[a-z]+-[0-9a-f]{5,}$|^[a-z]{1,3}[0-9]{3,}$|^:r[0-9a-z]+:$|ember[0-9]+|^[0-9a-f]{8,}$",
    re.I)


def fragility_reasons(selector: str) -> List[str]:
    s = (selector or "").strip()
    reasons: List[str] = []
    if not s:
        return reasons
    low = s.lower()
    if any(a in low for a in TEST_ID_ATTRS) or low.startswith(("role=", "getbyrole", "internal:role")):
        return reasons   # already a contract with the app, not its markup
    if low.startswith("/") or low.startswith("xpath=/"):
        if re.search(r"\[\d+\]", s) or low.startswith(("/html", "xpath=/html")):
            reasons.append("absolute XPath with positions breaks on any layout change")
        else:
            reasons.append("XPath couples the test to document structure")
    if re.search(r":nth-(child|of-type)\(|:eq\(|>> nth=", s):
        reasons.append("positional (nth-child / nth) — depends on sibling order")
    if s.count(">") + s.count(" ") >= 4 and not low.startswith(("text=", "role=")):
        reasons.append("long descendant chain — any wrapper change breaks it")
    for token in re.findall(r"[.#]([A-Za-z0-9_-]+)", s):
        if _GENERATED_TOKEN.search(token):
            reasons.append(f"generated class/id '{token}' changes between builds")
            break
    if low.startswith("text=") or "has-text(" in low:
        reasons.append("text match breaks on copy edits and translations")
    return reasons


class _Element:
    def __init__(self, tag: str, attrs: Dict[str, str]):
        self.tag = tag
        self.attrs = attrs
        self.text_parts: List[str] = []
        self.is_target = TARGET_MARK in attrs

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.text_parts)).strip()


class _TargetFinder(HTMLParser):
    """Find the element the worker marked as the step's target and collect
    its own text plus the text of <label for=...> elements."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
            "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: List[_Element] = []
        self.target: Optional[_Element] = None
        self.labels: Dict[str, List[str]] = defaultdict(list)
        self._label_for: List[Optional[str]] = []
        self.ids: Dict[str, int] = defaultdict(int)

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        el = _Element(tag.lower(), a)
        if a.get("id"):
            self.ids[a["id"]] += 1
        if el.is_target and self.target is None:
            self.target = el
        if el.tag == "label":
            self._label_for.append(a.get("for"))
        if el.tag not in self.VOID:
            self.stack.append(el)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "label" and self._label_for:
            self._label_for.pop()
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if not data.strip():
            return
        for el in self.stack:
            if el.is_target:
                el.text_parts.append(data)
        for target_id in self._label_for:
            if target_id:
                self.labels[target_id].append(data.strip())


_IMPLICIT_ROLES = {
    "button": "button", "select": "combobox", "textarea": "textbox", "nav": "navigation",
    "h1": "heading", "h2": "heading", "h3": "heading", "h4": "heading", "h5": "heading",
    "h6": "heading", "img": "img", "dialog": "dialog", "table": "table", "form": "form",
}
_INPUT_ROLES = {
    "checkbox": "checkbox", "radio": "radio", "button": "button", "submit": "button",
    "reset": "button", "search": "searchbox", "range": "slider", "number": "spinbutton",
    "email": "textbox", "tel": "textbox", "text": "textbox", "url": "textbox", "": "textbox",
}


def _role(el: _Element) -> Optional[str]:
    if el.attrs.get("role"):
        return el.attrs["role"].split()[0]
    if el.tag == "a":
        return "link" if "href" in el.attrs else None
    if el.tag == "input":
        return _INPUT_ROLES.get(el.attrs.get("type", "").lower())
    return _IMPLICIT_ROLES.get(el.tag)


def _quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def suggest_from_dom(dom: Optional[str]) -> List[Dict[str, str]]:
    """Robust locators for the marked target in a DOM excerpt, best first."""
    if not dom or TARGET_MARK not in dom:
        return []
    finder = _TargetFinder()
    try:
        finder.feed(dom)
    except Exception:  # noqa: BLE001 — a broken excerpt yields no suggestion
        return []
    el = finder.target
    if el is None:
        return []
    out: List[Dict[str, str]] = []
    for attr in TEST_ID_ATTRS:
        if el.attrs.get(attr):
            out.append({"locator": f'[{attr}="{_quote(el.attrs[attr])}"]',
                        "reason": f"{attr} is an explicit test hook — survives restyling and copy edits"})
            break
    role = _role(el)
    name = (el.attrs.get("aria-label") or finder_label(finder, el) or el.text
            or el.attrs.get("alt") or el.attrs.get("title") or el.attrs.get("placeholder") or "")
    name = name.strip()
    if role and name and len(name) <= 80 and "[masked]" not in name:
        out.append({"locator": f'role={role}[name="{_quote(name)}"]',
                    "reason": "ARIA role + accessible name — how users and assistive tech find it"})
    elif not role and name and el.tag in ("label", "span", "p", "li", "td", "div") and len(name) <= 60:
        out.append({"locator": f'text="{_quote(name)}"',
                    "reason": "visible text (fragile to copy edits; prefer a test id)"})
    ident = el.attrs.get("id")
    if ident and finder.ids.get(ident) == 1 and not _GENERATED_TOKEN.search(ident):
        out.append({"locator": f"#{ident}" if re.match(r"^[A-Za-z][\w-]*$", ident) else f'[id="{_quote(ident)}"]',
                    "reason": "unique, human-written id"})
    if el.attrs.get("name") and el.tag in ("input", "select", "textarea"):
        out.append({"locator": f'{el.tag}[name="{_quote(el.attrs["name"])}"]',
                    "reason": "form field name — part of the form's contract"})
    return out


def finder_label(finder: _TargetFinder, el: _Element) -> Optional[str]:
    ident = el.attrs.get("id")
    if ident and finder.labels.get(ident):
        return " ".join(finder.labels[ident]).strip() or None
    return None


def _week(ts: datetime) -> str:
    monday = (ts - timedelta(days=ts.weekday())).date()
    return monday.isoformat()


def aggregate(heals: Iterable[Dict[str, Any]], failures: Iterable[Dict[str, Any]],
              cases: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Report entries, worst first.

    heals:    {test_case_id, step_id, old_selector, new_selector, status, confidence, created_at}
    failures: {test_case_id, step_id, selector, status ('failed'|'healed'), run_id,
               created_at, dom, dom_scope}
    cases:    {case_id: {name, suite_id}}
    """
    groups: Dict[Tuple[int, str], Dict[str, Any]] = {}

    def group(case_id, selector) -> Dict[str, Any]:
        key = (case_id, selector)
        if key not in groups:
            meta = cases.get(case_id) or {}
            groups[key] = {
                "test_case_id": case_id, "case_name": meta.get("name"),
                "suite_id": meta.get("suite_id"), "selector": selector,
                "step_ids": set(), "heal_count": 0, "failure_count": 0,
                "runtime_healed_count": 0, "weeks": defaultdict(lambda: {"heals": 0, "failures": 0}),
                "first_seen": None, "last_seen": None, "run_ids": set(),
                "heal_candidates": [], "doms": [],
            }
        return groups[key]

    def seen(g, ts):
        if ts is None:
            return
        g["first_seen"] = ts if g["first_seen"] is None or ts < g["first_seen"] else g["first_seen"]
        g["last_seen"] = ts if g["last_seen"] is None or ts > g["last_seen"] else g["last_seen"]

    for h in heals:
        sel = (h.get("old_selector") or "").strip()
        if not sel or not h.get("test_case_id"):
            continue
        g = group(h["test_case_id"], sel)
        g["heal_count"] += 1
        if h.get("step_id"):
            g["step_ids"].add(h["step_id"])
        ts = h.get("created_at")
        if ts:
            g["weeks"][_week(ts)]["heals"] += 1
        seen(g, ts)
        g["heal_candidates"].append(h)

    for f in failures:
        sel = (f.get("selector") or "").strip()
        if not sel or not f.get("test_case_id"):
            continue
        g = group(f["test_case_id"], sel)
        if f.get("status") == "healed":
            g["runtime_healed_count"] += 1
        else:
            g["failure_count"] += 1
        if f.get("step_id"):
            g["step_ids"].add(f["step_id"])
        if f.get("run_id"):
            g["run_ids"].add(f["run_id"])
        ts = f.get("created_at")
        if ts:
            g["weeks"][_week(ts)]["failures"] += 1
        seen(g, ts)
        if f.get("dom") and f.get("dom_scope") == "target":
            g["doms"].append((ts or datetime.min, f["dom"]))

    out = []
    for g in groups.values():
        suggestions: List[Dict[str, str]] = []
        seen_loc = set()
        # Newest DOM first: it shows the element as it is now.
        for _, dom in sorted(g["doms"], key=lambda x: x[0], reverse=True)[:3]:
            for s in suggest_from_dom(dom):
                if s["locator"] not in seen_loc:
                    seen_loc.add(s["locator"])
                    suggestions.append({**s, "source": "dom"})
        for h in sorted(g["heal_candidates"], key=lambda h: h.get("created_at") or datetime.min,
                        reverse=True):
            new = (h.get("new_selector") or "").strip()
            if new and new not in seen_loc and not fragility_reasons(new):
                seen_loc.add(new)
                suggestions.append({"locator": new, "source": "heal",
                                    "reason": f"heal proposal ({h.get('status') or 'pending'}) "
                                              "that is not itself fragile"})
        latest_heal = max(g["heal_candidates"], key=lambda h: h.get("created_at") or datetime.min,
                          default=None)
        total = g["heal_count"] + g["failure_count"] + g["runtime_healed_count"]
        out.append({
            "test_case_id": g["test_case_id"], "case_name": g["case_name"],
            "suite_id": g["suite_id"], "selector": g["selector"],
            "step_ids": sorted(g["step_ids"]),
            "heal_count": g["heal_count"], "failure_count": g["failure_count"],
            "runtime_healed_count": g["runtime_healed_count"], "total": total,
            "first_seen": g["first_seen"], "last_seen": g["last_seen"],
            "recent_run_ids": sorted(g["run_ids"], reverse=True)[:5],
            "weeks": [{"week": w, **c} for w, c in sorted(g["weeks"].items())],
            "fragility": fragility_reasons(g["selector"]),
            "latest_heal": ({"new_selector": latest_heal.get("new_selector"),
                             "status": latest_heal.get("status"),
                             "confidence": latest_heal.get("confidence")} if latest_heal else None),
            "suggestions": suggestions[:5],
        })
    out.sort(key=lambda e: (e["total"], e["last_seen"] or datetime.min), reverse=True)
    return out[:MAX_ENTRIES]


def failures_from_payload(payload: Any, test_case_id: Optional[int], run_id: int,
                          created_at: datetime) -> List[Dict[str, Any]]:
    """Failed / runtime-healed steps that used a selector, from one result."""
    if not isinstance(payload, dict) or not test_case_id:
        return []
    out = []
    for e in payload.get("step_evidence") or []:
        if not isinstance(e, dict) or e.get("status") not in ("failed", "healed"):
            continue
        if not isinstance(e.get("selector"), str) or not e["selector"].strip():
            continue
        out.append({"test_case_id": test_case_id, "step_id": e.get("step_id"),
                    "selector": e["selector"], "status": e["status"], "run_id": run_id,
                    "created_at": created_at, "dom": e.get("dom"),
                    "dom_scope": e.get("dom_scope")})
    return out
