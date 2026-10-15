"""Acceptance-criteria extraction and ambiguity detection for imported tickets.

Pure functions, no I/O — the tracker clients hand us the issue description and
the generation endpoint consumes the result.

Real tickets put acceptance criteria in many shapes, and this has to cope with
all of the common ones because a criterion we fail to find is a test nobody
proposes:

* Gherkin blocks (``Scenario:`` + Given/When/Then/And/But), with or without the
  ``Scenario:`` line, bulleted or not. One scenario = one criterion.
* An "Acceptance Criteria" section — a markdown heading, a Jira wiki ``h3.``
  heading, a bold line, or a plain ``Acceptance criteria:`` line — followed by
  bullets, numbers, checkboxes or plain lines. Indented sub-bullets fold into
  their parent so a criterion and its detail are proposed together.
* Checklists (``- [ ]`` / ``[x]``) anywhere in the description.
* ``AC1: ...`` / ``AC-2 - ...`` prefixed lines anywhere.

Ambiguity is deliberately conservative in the other direction: a criterion is
flagged rather than generated when it has no observable outcome or leans on a
vague word ("fast", "user-friendly", "should work"). A generated test for
"the page should be fast" asserts something arbitrary and goes green forever,
which is worse than no test — so the reviewer sees the flag and either rewrites
the criterion or forces generation.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Atlassian Document Format → markdown-ish text
# ---------------------------------------------------------------------------


def adf_to_text(node: Any) -> str:
    """Flatten a Jira Cloud v3 ADF document to markdown-flavoured text.

    The shapes the criteria parser looks for survive: headings keep their
    level as ``#``s, bullets become ``- ``, ordered items ``1. ``, task items
    ``- [ ]``/``- [x]``. A plain string (Jira Server/DC, REST v2) passes
    through unchanged."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    lines: List[str] = []
    _adf_block(node, lines, indent=0)
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _adf_inline(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    t = node.get("type")
    attrs = node.get("attrs") or {}
    if t == "text":
        return node.get("text", "")
    if t == "hardBreak":
        return "\n"
    if t == "mention":
        return attrs.get("text") or ""
    if t == "emoji":
        return attrs.get("text") or attrs.get("shortName") or ""
    if t in ("inlineCard", "blockCard"):
        return attrs.get("url") or ""
    if t == "status":
        return attrs.get("text") or ""
    if t == "date":
        return str(attrs.get("timestamp") or "")
    return "".join(_adf_inline(c) for c in node.get("content") or [])


def _adf_block(node: dict, out: List[str], indent: int) -> None:
    t = node.get("type")
    content = node.get("content") or []
    pad = "  " * indent
    if t == "doc":
        for c in content:
            _adf_block(c, out, indent)
        return
    if t == "heading":
        level = int((node.get("attrs") or {}).get("level") or 2)
        out.append("#" * max(1, min(level, 6)) + " " + "".join(_adf_inline(c) for c in content))
        return
    if t == "paragraph":
        text = "".join(_adf_inline(c) for c in content)
        for line in text.split("\n"):
            out.append(pad + line if line else "")
        return
    if t in ("bulletList", "orderedList", "taskList"):
        n = 1
        for item in content:
            if not isinstance(item, dict):
                continue
            if t == "bulletList":
                marker = "- "
            elif t == "orderedList":
                marker = f"{n}. "
                n += 1
            else:
                state = ((item.get("attrs") or {}).get("state") or "TODO").upper()
                marker = "- [x] " if state == "DONE" else "- [ ] "
            _adf_list_item(item, out, indent, marker)
        return
    if t in ("listItem", "taskItem"):
        _adf_list_item(node, out, indent, "- ")
        return
    if t == "codeBlock":
        text = "".join(_adf_inline(c) for c in content)
        out.extend(pad + line for line in text.split("\n"))
        return
    if t in ("blockquote", "panel", "expand", "nestedExpand", "layoutSection", "layoutColumn"):
        for c in content:
            _adf_block(c, out, indent)
        return
    if t == "table":
        for row in content:
            cells = []
            for cell in (row or {}).get("content") or []:
                sub: List[str] = []
                for c in (cell or {}).get("content") or []:
                    _adf_block(c, sub, 0)
                cells.append(" ".join(s.strip() for s in sub if s.strip()))
            out.append("| " + " | ".join(cells) + " |")
        return
    if t == "rule":
        out.append("")
        return
    text = _adf_inline(node)
    if text:
        out.append(pad + text)


def _adf_list_item(item: dict, out: List[str], indent: int, marker: str) -> None:
    pad = "  " * indent
    first = True
    for c in item.get("content") or []:
        if not isinstance(c, dict):
            continue
        if c.get("type") in ("bulletList", "orderedList", "taskList"):
            _adf_block(c, out, indent + 1)
            continue
        if c.get("type") == "paragraph" or c.get("type") == "text":
            text = (_adf_inline(c) if c.get("type") == "text"
                    else "".join(_adf_inline(x) for x in c.get("content") or []))
            text = text.replace("\n", " ").strip()
            if first:
                out.append(pad + marker + text)
                first = False
            elif text:
                out.append(pad + "  " + text)
            continue
        _adf_block(c, out, indent + 1)
    if first:
        # taskItem carries inline content directly.
        text = "".join(_adf_inline(c) for c in item.get("content") or []).strip()
        out.append(pad + marker + text)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_GHERKIN = re.compile(r"^(given|when|then|and|but)\b\s*:?\s*(.*)$", re.I)
_SCENARIO = re.compile(r"^(scenario outline|scenario|example)\s*:\s*(.*)$", re.I)
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_WIKI_HEADING = re.compile(r"^\s*h[1-6]\.\s+(.*)$", re.I)
_BOLD_LINE = re.compile(r"^\s*(\*\*|__|\*)(.+?)\1\s*:?\s*$")
_COLON_LINE = re.compile(r"^\s*([A-Za-z][\w /&()'-]{1,58}):\s*(.*)$")
_CHECKBOX = re.compile(r"^(\s*)(?:[-*+]\s+)?\[( |x|X)\]\s+(.*)$")
_BULLET = re.compile(r"^(\s*)(?:[-*+•◦]|\d{1,3}[.)]|\(\d{1,3}\)|[a-z][.)])\s+(.*)$")
_AC_PREFIX = re.compile(r"^\s*(?:[-*+]\s+)?AC\s*[-#]?\s*\d+\s*[:.)\-]\s*(.*)$", re.I)
_JIRA_ICON = re.compile(r"^\s*\((?:/|x|i|!|\?|y|n|on|off)\)\s*", re.I)
_AC_TITLE = re.compile(
    r"^(acceptance\s+criteri(?:a|on)|acceptance\s+tests?|acs?|a\.c\.|"
    r"definition\s+of\s+done|dod|success\s+criteria)$", re.I)


def _strip_markup(text: str) -> str:
    text = _JIRA_ICON.sub("", text)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", text)
    text = re.sub(r"\{\{(.+?)\}\}", r"\1", text)       # Jira wiki monospace
    text = re.sub(r"</?(?:p|span|div|b|i|em|strong|u|li|ul|ol|code)\b[^>]{0,80}>", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def _heading_text(line: str) -> Optional[str]:
    """The text of a heading-looking line, or None."""
    for rx in (_MD_HEADING, _WIKI_HEADING):
        m = rx.match(line)
        if m:
            return _strip_markup(m.group(1)).rstrip(":").strip()
    m = _BOLD_LINE.match(line)
    if m:
        return _strip_markup(m.group(2)).rstrip(":").strip()
    return None


def _is_ac_title(text: str) -> bool:
    return bool(_AC_TITLE.match(_strip_markup(text).rstrip(":").strip()))


def _indent_of(raw: str) -> int:
    return len(raw) - len(raw.lstrip(" \t"))


def parse_acceptance_criteria(description: Optional[str]) -> List[Dict[str, Any]]:
    """Extract acceptance criteria from a ticket description.

    Returns ``[{"text": str, "kind": "gherkin"|"list"|"checklist"|"text"}]`` in
    document order, de-duplicated. An empty list means "nothing recognisable";
    the caller decides whether to fall back to the ticket title."""
    if not description:
        return []
    text = description.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)

    out: List[Dict[str, Any]] = []
    in_ac = False
    # Gherkin accumulator: lines of the current scenario, and whether a Then
    # has been seen (a Given after a Then starts the next scenario).
    gherkin: List[str] = []
    seen_then = False
    # Current list item in the AC section, for folding sub-bullets.
    last_item: Optional[Dict[str, Any]] = None
    last_indent = 0

    def close_gherkin() -> None:
        nonlocal gherkin, seen_then
        steps = [g for g in gherkin if not _SCENARIO.match(g)]
        if steps:
            out.append({"text": "\n".join(gherkin), "kind": "gherkin"})
        gherkin, seen_then = [], False

    def add(t: str, kind: str, indent: int = 0) -> None:
        nonlocal last_item, last_indent
        t = _strip_markup(t)
        if len(t) < 3:
            return
        item = {"text": t, "kind": kind}
        out.append(item)
        last_item, last_indent = item, indent

    for raw in text.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("|") and gherkin:
            # Example tables belong to the scenario they follow.
            gherkin.append(stripped)
            continue

        # Headings switch the AC section on/off.
        heading = _heading_text(line)
        if heading is None and not _BULLET.match(line) and _is_ac_title(stripped):
            heading = stripped   # a bare "Acceptance Criteria" line
        if heading is None:
            m = _COLON_LINE.match(stripped)
            if m and not _BULLET.match(line) and not _GHERKIN.match(stripped) \
                    and not _SCENARIO.match(stripped) and _is_ac_title(m.group(1)):
                close_gherkin()
                in_ac, last_item = True, None
                if m.group(2).strip():
                    add(m.group(2), "text")
                continue
        if heading is not None:
            close_gherkin()
            in_ac, last_item = _is_ac_title(heading), None
            continue

        # Gherkin wins wherever it appears, bulleted or not.
        body = stripped
        bm = _BULLET.match(line)
        if bm and not _CHECKBOX.match(line):
            body = bm.group(2).strip()
        body = _strip_markup(body)
        sm = _SCENARIO.match(body)
        if sm:
            close_gherkin()
            gherkin = [body]
            continue
        gm = _GHERKIN.match(body)
        if gm:
            kw = gm.group(1).lower()
            if kw in ("and", "but") and not gherkin:
                # A stray "And ..." with nothing to continue is ordinary prose.
                pass
            else:
                if kw == "given" and seen_then:
                    close_gherkin()
                if kw == "then":
                    seen_then = True
                gherkin.append(body[0].upper() + body[1:])
                continue
        if gherkin:
            close_gherkin()

        acm = _AC_PREFIX.match(stripped)
        if acm:
            add(acm.group(1), "list", _indent_of(raw))
            continue

        cm = _CHECKBOX.match(line)
        if cm:
            add(cm.group(3), "checklist", len(cm.group(1)))
            continue

        if not in_ac:
            continue

        if bm:
            indent = len(bm.group(1))
            if last_item is not None and indent > last_indent and last_item["kind"] in ("list", "checklist"):
                # Sub-bullet: detail of the criterion above it.
                last_item["text"] = last_item["text"].rstrip(".;:") + "; " + _strip_markup(bm.group(2))
                continue
            add(bm.group(2), "list", indent)
            continue

        if last_item is not None and _indent_of(raw) > last_indent and last_item["kind"] == "list":
            last_item["text"] += " " + _strip_markup(stripped)   # wrapped continuation
            continue
        add(stripped, "text", _indent_of(raw))

    close_gherkin()

    seen: set = set()
    deduped = []
    for c in out:
        key = re.sub(r"\W+", " ", c["text"].lower()).strip()
        if key and key not in seen:
            seen.add(key)
            deduped.append(c)
    return deduped


# ---------------------------------------------------------------------------
# Ambiguity
# ---------------------------------------------------------------------------

# Words that make a criterion untestable as written. Performance words are
# forgiven when the sentence also states a measurable bound ("within 2s").
_PERF_VAGUE = ("fast", "faster", "quick", "quickly", "slow", "responsive", "snappy",
               "efficient", "efficiently", "performant", "instant", "instantly")
_VAGUE = _PERF_VAGUE + (
    "user-friendly", "user friendly", "intuitive", "intuitively", "easy", "easily",
    "simple", "simply", "seamless", "seamlessly", "smooth", "smoothly", "nice",
    "pretty", "beautiful", "clean", "modern", "elegant", "properly", "correctly",
    "appropriate", "appropriately", "as expected", "as appropriate", "should work",
    "works as expected", "works fine", "works well", "work well", "work properly",
    "works properly", "robust", "flexible", "scalable", "reasonable", "reasonably",
    "adequate", "sufficient", "optimal", "optimised", "optimized", "better",
    "improved", "improve", "enhance", "enhanced", "good ux", "great ux",
    "good experience", "user experience", "look good", "looks good", "etc",
    "and so on", "if possible", "where possible", "as needed", "tbd", "tbc",
)
_VAGUE_RX = [(w, re.compile(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", re.I)) for w in _VAGUE]

_MEASURABLE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:ms|milliseconds?|s|secs?|seconds?|minutes?|mins?|%|percent|"
    r"kb|mb|gb|px|items?|rows?|results?|requests?|rps|users?|attempts?|times?|characters?|chars?)\b",
    re.I)

# Evidence that the criterion names something a test could check.
_OBSERVABLE = re.compile(
    r"\b(see|sees|seen|shown|show|shows|display|displays|displayed|visible|hidden|"
    r"appear|appears|appeared|disappear|disappears|redirect|redirects|redirected|"
    r"navigate|navigates|navigated|land|lands|message|error|warning|toast|banner|"
    r"alert|returns?|returned|respond|responds|response|status|code|receive|receives|"
    r"received|email|sms|notification|notified|sent|send|sends|saved|save|saves|"
    r"stored|persist|persists|created|creates|deleted|deletes|removed|updated|"
    r"updates|changed|changes|enabled|disabled|contains?|equals?|matches|count|"
    r"total|listed|lists?|download|downloads|downloaded|upload|uploaded|exported|"
    r"logged|log in|log out|login|logout|sign in|sign out|signed|added|adds|cannot|"
    r"can't|can not|must not|prevent|prevents|prevented|block|blocks|blocked|reject|"
    r"rejects|rejected|accept|accepts|accepted|valid|invalid|validation|required|"
    r"highlight|highlighted|button|page|field|link|tab|modal|dialog|form|label|"
    r"title|heading|text|value|price|amount|balance|sorted|filtered|selected|"
    r"checked|unchecked|focus|focused|expires?|expired|locked|unlocked|charged|"
    r"refund|refunded|paid|pay|pays|purchase|purchases|buy|buys|submit|submits|"
    r"submitted|order|cart|checkout|"
    # Capability statements ("guests can pay", "admins are able to export")
    # are tested by doing the thing.
    r"can|cannot|able to|allowed to|permitted to)\b", re.I)


def detect_ambiguity(text: str) -> Dict[str, Any]:
    """``{"ambiguous": bool, "reasons": [str]}`` for one criterion."""
    reasons: List[str] = []
    t = (text or "").strip()
    words = re.findall(r"[A-Za-z0-9']+", t)
    if len(words) < 3:
        return {"ambiguous": True, "reasons": ["too short to test"]}

    measurable = bool(_MEASURABLE.search(t))
    for word, rx in _VAGUE_RX:
        if rx.search(t):
            if measurable and word in _PERF_VAGUE:
                continue
            reasons.append(f'vague term "{word}"')
    # "works as expected" also matches "as expected"; keep the longest phrase.
    reasons = [r for r in reasons
               if not any(r != o and r[len('vague term "'):-1] in o for o in reasons)]

    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    is_gherkin = any(_GHERKIN.match(ln) for ln in lines)
    if is_gherkin:
        outcome = []
        in_then = False
        for ln in lines:
            m = _GHERKIN.match(ln)
            if not m:
                continue
            kw = m.group(1).lower()
            if kw == "then":
                in_then = True
            elif kw in ("given", "when"):
                in_then = False
            if in_then:
                outcome.append(m.group(2))
        if not outcome:
            reasons.append("no Then step (no observable outcome)")
    elif not (_OBSERVABLE.search(t) or measurable or re.search(r"[\"“'`].+?[\"”'`]", t)):
        reasons.append("no observable outcome")
    return {"ambiguous": bool(reasons), "reasons": reasons}


def analyse(description: Optional[str]) -> List[Dict[str, Any]]:
    """Parse + flag in one go: the shape stored on `Requirement.acceptance_criteria`."""
    out = []
    for i, c in enumerate(parse_acceptance_criteria(description)):
        amb = detect_ambiguity(c["text"])
        out.append({"index": i, "text": c["text"], "kind": c["kind"],
                    "ambiguous": amb["ambiguous"], "reasons": amb["reasons"]})
    return out
