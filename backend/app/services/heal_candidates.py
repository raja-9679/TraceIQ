"""Selector-heal candidates (W5): ranked replacement selectors with evidence,
a server-side selector matcher, and the redacted DOM snapshot store.

Twin of execution-engine/src/core/heal-candidates.ts — the strategies, the
stability table, the weights and the selector forms must stay identical, so a
reviewer sees the same evidence whether the worker (runtime heal) or the
backend (proactive heal) produced the proposal.

Score = 0.35 stability + 0.35 similarity + 0.15 LLM + 0.15 uniqueness, and only
a candidate matching exactly one element is *applicable*.

The matcher (`match_selector`) parses the snapshot with lxml (already a
dependency, via python3-saml) and implements the selector forms the
strategies emit plus the common forms an LLM suggests. lxml's own CSS support
needs the `cssselect` package, which is not installed, so CSS is matched by
the small engine below. Supported:

* CSS: type/universal, `#id`, `.class`, `[attr]`, `[attr op "v" i]` with
  = ^= $= *= ~= |=, combinators (descendant, `>`, `+`, `~`), selector lists,
  `:has-text()`, `:text()`, `:text-is()`, `:not()`, `:is()`/`:where()`,
  `:has()`, `:first-child`, `:last-child`, `:only-child`, `:nth-child(an+b)`,
  `:nth-of-type(n)`, `:first-of-type`, `:last-of-type`, `:checked`,
  `:disabled`, `:enabled`, `:empty`, `:visible` (approximated);
* Playwright engines: `text=` (quoted = exact, else case-insensitive
  substring; innermost element), `role=`/`internal:role=` with `name`,
  `level` and `include-hidden`, `xpath=` / `//…`, `id=`, `data-testid=`,
  `data-test-id=`, `data-test=`, `css=`, and `>>` chaining.

Anything else raises `UnsupportedSelector`; the accept recheck refuses such a
selector rather than guessing. Visibility and accessible names are
approximations of the browser's (no layout, no CSS cascade).
"""
from __future__ import annotations

import hashlib
import io
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

STABILITY = {
    "test-id": 1.0, "id": 0.85, "role": 0.75, "label": 0.65, "text": 0.5, "css-path": 0.3,
}
GENERATED_ID_STABILITY = 0.35
WEIGHTS = {"stability": 0.35, "similarity": 0.35, "llm": 0.15, "uniqueness": 0.15}
DEFAULT_LLM_CONFIDENCE = 0.6
MAX_TARGETS = 3
MAX_CANDIDATES = 12
_MIN_TARGET_SIMILARITY = 0.34
TEST_ID_ATTRS = ("data-testid", "data-test-id", "data-test", "data-qa", "data-cy")
STRATEGIES = ("test-id", "id", "role", "label", "text", "css-path", "llm")
#: Stored snapshot cap (bytes of UTF-8); matches the redaction cap in chars.
MAX_SNAPSHOT_BYTES = 800_000


class UnsupportedSelector(ValueError):
    """The matcher cannot evaluate this selector form."""


# ---------------------------------------------------------------------------
# Tokens and similarity
# ---------------------------------------------------------------------------

_STOP_TOKENS = frozenset({
    "div", "span", "css", "xpath", "text", "role", "name", "has", "nth", "child", "of", "type",
    "the", "an", "and", "to", "is", "on", "in", "for", "first", "last", "data", "testid", "test",
    "qa", "cy", "aria", "label", "id", "class", "click", "element", "visible", "exact",
})


def tokens(s: Optional[str]) -> List[str]:
    if not s:
        return []
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s).lower()
    return [t for t in re.split(r"[^a-z0-9]+", s)
            if len(t) >= 2 and t not in _STOP_TOKENS and not t.isdigit()]


def _token_score(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)):
        return 0.8
    return 0.0


def similarity(query: Iterable[str], features: List[str]) -> float:
    q = list(dict.fromkeys(query))
    if not q or not features:
        return 0.0
    total = sum(max(_token_score(t, f) for f in features) for t in q)
    return round(total / len(q), 3)


# ---------------------------------------------------------------------------
# Parsing and element facts
# ---------------------------------------------------------------------------

def parse_html(html: str):
    """lxml document for a snapshot (never raises; empty doc for junk)."""
    import lxml.html
    try:
        return lxml.html.document_fromstring(html or "<html></html>")
    except Exception:  # noqa: BLE001
        return lxml.html.document_fromstring("<html></html>")


def _elements(node) -> Iterable[Any]:
    for el in node.iterdescendants():
        if isinstance(el.tag, str):
            yield el


def _norm(s: Optional[str]) -> str:
    return re.sub(r"[\s ]+", " ", s or "").strip()


def _attr(el, name: str) -> str:
    return el.get(name) or ""


_NON_TEXT = frozenset({"script", "style", "template", "head"})


def text_of(el) -> str:
    """Element text as Playwright sees it: input buttons use their value."""
    if el.tag == "input" and _attr(el, "type").lower() in ("submit", "button", "reset"):
        return _norm(_attr(el, "value"))
    parts: List[str] = []

    def rec(node):
        if node.text and node.tag not in _NON_TEXT:
            parts.append(node.text)
        for child in node:
            if isinstance(child.tag, str) and child.tag not in _NON_TEXT:
                rec(child)
            if child.tail:
                parts.append(child.tail)
    rec(el)
    return _norm("".join(parts))


_INPUT_ROLES = {
    "button": "button", "submit": "button", "reset": "button", "image": "button",
    "checkbox": "checkbox", "radio": "radio", "range": "slider", "number": "spinbutton",
    "search": "searchbox", "text": "textbox", "email": "textbox", "tel": "textbox",
    "url": "textbox", "": "textbox", "password": "textbox",
}
ROLE_CANDIDATE_ROLES = frozenset({
    "button", "link", "checkbox", "radio", "textbox", "searchbox", "combobox", "listbox",
    "heading", "tab", "menuitem", "option", "switch", "slider", "spinbutton", "img",
})
_NAME_FROM_CONTENT = frozenset({
    "button", "link", "heading", "tab", "menuitem", "option", "switch", "checkbox", "radio",
})


def implicit_role(el) -> Optional[str]:
    explicit = _attr(el, "role").strip().split()
    if explicit:
        return explicit[0]
    t = el.tag
    if t == "a":
        return "link" if el.get("href") is not None else None
    if t == "button":
        return "button"
    if t == "input":
        return _INPUT_ROLES.get(_attr(el, "type").lower())
    if t == "textarea":
        return "textbox"
    if t == "select":
        return "listbox" if el.get("multiple") is not None else "combobox"
    if re.fullmatch(r"h[1-6]", t):
        return "heading"
    if t == "img":
        return "img" if _attr(el, "alt").strip() else None
    if t == "option":
        return "option"
    return None


def accessible_name(el, root) -> str:
    """Simplified accname — twin of heal-candidates.ts:accessibleName."""
    labelledby = _attr(el, "aria-labelledby")
    if labelledby:
        found = []
        for ref in labelledby.split():
            hits = [e for e in _elements(root) if e.get("id") == ref]
            if hits:
                found.append(text_of(hits[0]))
        name = _norm(" ".join(found))
        if name:
            return name
    aria = _norm(_attr(el, "aria-label"))
    if aria:
        return aria
    role = implicit_role(el) or ""
    if el.tag in ("input", "textarea", "select"):
        if el.tag == "input" and _attr(el, "type").lower() in ("submit", "button", "reset"):
            return _norm(_attr(el, "value"))
        el_id = el.get("id")
        if el_id:
            for lab in _elements(root):
                if lab.tag == "label" and lab.get("for") == el_id:
                    return text_of(lab)
        for anc in el.iterancestors():
            if anc.tag == "label":
                return text_of(anc)
        return _norm(_attr(el, "title") or _attr(el, "placeholder"))
    if el.tag == "img":
        return _norm(_attr(el, "alt"))
    if role in _NAME_FROM_CONTENT:
        t = text_of(el)
        if t:
            return t
    return _norm(_attr(el, "title"))


def looks_generated(value: str) -> bool:
    return bool(re.search(r"\d{3,}|[0-9a-f]{8,}|^(ember|react|radix|mui|headlessui|rc-|:r)|:",
                          value or "", re.IGNORECASE))


_CSS_IDENT = re.compile(r"^-?[A-Za-z_][A-Za-z0-9_-]*$")


def quote_value(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _features(el, root) -> List[str]:
    own = text_of(el)
    out = tokens(el.tag) + tokens(el.get("id")) + tokens(el.get("class"))
    for a in TEST_ID_ATTRS:
        out += tokens(el.get(a))
    for a in ("name", "placeholder", "aria-label", "title", "alt", "type", "href"):
        out += tokens(el.get(a))
    out += tokens(implicit_role(el))
    if len(own) <= 80:
        out += tokens(own)
    out += tokens(accessible_name(el, root))
    return out


_SKIP_TAGS = frozenset({"html", "head", "body", "script", "style", "noscript", "template",
                        "title", "meta", "link", "br", "hr"})
_INTERACTIVE = frozenset({"a", "button", "input", "select", "textarea", "option", "label"})


def find_targets(root, query: List[str], limit: int = MAX_TARGETS) -> List[Tuple[Any, float]]:
    if not query:
        return []
    scored = []
    for order, el in enumerate(_elements(root)):
        if el.tag in _SKIP_TAGS:
            continue
        sim = similarity(query, _features(el, root))
        if sim < _MIN_TARGET_SIMILARITY:
            continue
        depth = sum(1 for _ in el.iterancestors())
        bonus = 0.05 if (el.tag in _INTERACTIVE or el.get("role") is not None) else 0.0
        scored.append((sim + bonus + depth / 10000, order, el, sim))
    scored.sort(key=lambda s: (-s[0], s[1]))
    out: List[Tuple[Any, float]] = []
    for _, _, el, sim in scored:
        if any(_contains(el, o) or _contains(o, el) for o, _ in out):
            continue
        out.append((el, sim))
        if len(out) >= limit:
            break
    return out


def _contains(outer, inner) -> bool:
    return any(a is outer for a in inner.iterancestors())


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

def test_id_strategy(el) -> List[str]:
    return [f"[{a}={quote_value(_attr(el, a).strip())}]" for a in TEST_ID_ATTRS if _attr(el, a).strip()]


def id_strategy(el) -> List[Tuple[str, bool]]:
    el_id = _attr(el, "id").strip()
    if not el_id:
        return []
    sel = f"#{el_id}" if _CSS_IDENT.match(el_id) else f"[id={quote_value(el_id)}]"
    return [(sel, looks_generated(el_id))]


def role_strategy(el, root) -> List[str]:
    role = implicit_role(el)
    if not role or role not in ROLE_CANDIDATE_ROLES:
        return []
    name = accessible_name(el, root)
    if not name or len(name) > 60:
        return []
    return [f"role={role}[name={quote_value(name)}]"]


def label_strategy(el) -> List[str]:
    out = []
    aria = _norm(_attr(el, "aria-label"))
    if aria and len(aria) <= 60:
        out.append(f"[aria-label={quote_value(aria)}]")
    ph = _norm(_attr(el, "placeholder"))
    if ph and len(ph) <= 60:
        out.append(f"[placeholder={quote_value(ph)}]")
    name = _attr(el, "name").strip()
    if name and len(name) <= 60 and el.tag in ("input", "select", "textarea", "button"):
        out.append(f"{el.tag}[name={quote_value(name)}]")
    return out


def text_strategy(el) -> List[str]:
    text = text_of(el)
    if not text or len(text) > 50 or "[REDACTED" in text:
        return []
    if any(isinstance(c.tag, str) and text_of(c) == text for c in el):
        return []
    return [f"text={quote_value(text)}"]


def _stable_classes(el) -> List[str]:
    return [c for c in _attr(el, "class").split()
            if _CSS_IDENT.match(c) and not looks_generated(c) and not re.search(r"\d", c)][:2]


def _path_segment(el) -> Tuple[str, bool]:
    el_id = _attr(el, "id").strip()
    if el_id and _CSS_IDENT.match(el_id) and not looks_generated(el_id):
        return f"{el.tag}#{el_id}", True
    seg = el.tag + "".join(f".{c}" for c in _stable_classes(el))
    if el.tag in ("input", "button"):
        typ = _attr(el, "type").strip()
        if typ and _CSS_IDENT.match(typ):
            seg += f"[type={quote_value(typ)}]"
    return seg, False


def css_path_strategy(el) -> List[str]:
    segs: List[str] = []
    cur = el
    while cur is not None and isinstance(cur.tag, str) and len(segs) < 4:
        if cur.tag in ("html", "body"):
            break
        seg, anchored = _path_segment(cur)
        segs.insert(0, seg)
        if anchored:
            break
        cur = cur.getparent()
    if len(segs) < 2 and segs and not any(ch in segs[0] for ch in "#.["):
        return []
    return [" > ".join(segs)] if segs else []


def classify_selector(selector: str) -> float:
    s = (selector or "").strip()
    if any(a in s for a in TEST_ID_ATTRS) or re.match(r"^data-test(id)?=", s):
        return STABILITY["test-id"]
    if re.fullmatch(r"#[\w-]+", s) or s.startswith("id="):
        return GENERATED_ID_STABILITY if looks_generated(s) else STABILITY["id"]
    if re.match(r"^(internal:)?role=", s):
        return STABILITY["role"]
    if re.search(r"\[(aria-label|placeholder|name)\s*=", s):
        return STABILITY["label"]
    if re.search(r"^text=|:has-text\(|:text(-is)?\(", s):
        return STABILITY["text"]
    return 0.4


def deterministic_candidates(root, old_selector: str, intent: Optional[str]) -> List[Dict[str, Any]]:
    query = tokens(old_selector) + tokens(intent)
    drafts: List[Dict[str, Any]] = []
    for target, (el, sim) in enumerate(find_targets(root, query)):
        def add(sel, strategy, stability):
            drafts.append({"selector": sel, "strategy": strategy, "stability": stability,
                           "similarity": sim, "target": target, "element": el})
        for sel in test_id_strategy(el):
            add(sel, "test-id", STABILITY["test-id"])
        for sel, generated in id_strategy(el):
            add(sel, "id", GENERATED_ID_STABILITY if generated else STABILITY["id"])
        for sel in role_strategy(el, root):
            add(sel, "role", STABILITY["role"])
        for sel in label_strategy(el):
            add(sel, "label", STABILITY["label"])
        for sel in text_strategy(el):
            add(sel, "text", STABILITY["text"])
        for sel in css_path_strategy(el):
            add(sel, "css-path", STABILITY["css-path"])
    return drafts


# ---------------------------------------------------------------------------
# Scoring and ranking
# ---------------------------------------------------------------------------

def score_candidate(b: Dict[str, float]) -> float:
    return round(sum(WEIGHTS[k] * b[k] for k in WEIGHTS), 3)


def _rank_key(c: Dict[str, Any]):
    return (0 if c["applicable"] else 1, -c["score"], -c["breakdown"]["stability"], c["selector"])


def rank_candidates(
    html: str,
    old_selector: str,
    intent: Optional[str] = None,
    llm_selector: Optional[str] = None,
    llm_confidence: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Ranked candidates for a broken selector against a (redacted) snapshot.
    Match counts come from `match_selector` over the same snapshot."""
    root = parse_html(html)
    old = (old_selector or "").strip()
    llm_sel = (llm_selector or "").strip()
    query = tokens(old) + tokens(intent)
    by_sel: Dict[str, Dict[str, Any]] = {}
    for d in deterministic_candidates(root, old, intent):
        if d["selector"] == old or d["selector"] in by_sel:
            continue
        by_sel[d["selector"]] = {**d, "from_llm": False}
    if llm_sel and llm_sel != old:
        if llm_sel in by_sel:
            by_sel[llm_sel]["from_llm"] = True
        else:
            by_sel[llm_sel] = {"selector": llm_sel, "strategy": "llm",
                               "stability": classify_selector(llm_sel),
                               "similarity": similarity(query, tokens(llm_sel)), "from_llm": True}
    entries = list(by_sel.values())[:MAX_CANDIDATES]

    matched: Dict[str, List[Any]] = {}
    for e in entries:
        try:
            matched[e["selector"]] = match_selector(root, e["selector"])
        except UnsupportedSelector:
            matched[e["selector"]] = []
    conf = DEFAULT_LLM_CONFIDENCE if llm_confidence is None else float(llm_confidence)
    llm_hits = matched.get(llm_sel) or []
    llm_el = llm_hits[0] if len(llm_hits) == 1 else None

    out = []
    for e in entries:
        hits = matched[e["selector"]]
        n = len(hits)
        if e["from_llm"]:
            llm = conf
        elif llm_el is not None and n == 1 and hits[0] is llm_el:
            llm = conf
        else:
            llm = 0.0
        breakdown = {"stability": round(e["stability"], 3), "similarity": round(e["similarity"], 3),
                     "llm": round(llm, 3), "uniqueness": 1 if n == 1 else 0}
        out.append({
            "selector": e["selector"],
            "strategy": e["strategy"],
            "source": ("llm" if e["strategy"] == "llm" else "both") if e["from_llm"] else "deterministic",
            "matches": n,
            "applicable": n == 1,
            "score": score_candidate(breakdown),
            "breakdown": breakdown,
        })
    return sorted(out, key=_rank_key)


def choose_candidate(ranked: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return next((c for c in ranked if c.get("applicable")), None)


def sanitize_candidates(raw: Any) -> List[Dict[str, Any]]:
    """Worker-supplied candidates, reduced to the known shape (the worker is
    not trusted to send only what the UI expects)."""
    out: List[Dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for c in raw[:MAX_CANDIDATES]:
        if not isinstance(c, dict) or not isinstance(c.get("selector"), str) or not c["selector"].strip():
            continue
        b = c.get("breakdown") if isinstance(c.get("breakdown"), dict) else {}

        def num(v, lo=0.0, hi=1.0):
            try:
                return max(lo, min(hi, round(float(v), 3)))
            except (TypeError, ValueError):
                return 0.0
        try:
            matches = max(0, int(c.get("matches") or 0))
        except (TypeError, ValueError):
            matches = 0
        out.append({
            "selector": c["selector"][:500],
            "strategy": c.get("strategy") if c.get("strategy") in STRATEGIES else "llm",
            "source": c.get("source") if c.get("source") in ("deterministic", "llm", "both") else "llm",
            "matches": matches,
            "applicable": matches == 1,
            "score": num(c.get("score")),
            "breakdown": {k: num(b.get(k)) for k in WEIGHTS},
        })
    return sorted(out, key=_rank_key)


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------

def _split_top(s: str, sep: str) -> List[str]:
    """Split on `sep` outside quotes, brackets and parentheses."""
    parts, buf, depth, quote, i = [], [], 0, None, 0
    while i < len(s):
        ch = s[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and i + 1 < len(s):
                buf.append(s[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth -= 1
            buf.append(ch)
        elif depth == 0 and s.startswith(sep, i):
            parts.append("".join(buf))
            buf = []
            i += len(sep)
            continue
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return re.sub(r"\\(.)", r"\1", v[1:-1])
    return v


_ENGINE_RE = re.compile(r"^(css|xpath|text|role|internal:role|id|data-testid|data-test-id|data-test)\s*=", re.I)


def match_selector(root, selector: str) -> List[Any]:
    """Elements of the parsed snapshot `root` that `selector` matches, in
    document order. Raises UnsupportedSelector for forms outside the subset."""
    selector = (selector or "").strip()
    if not selector:
        raise UnsupportedSelector("empty selector")
    scopes = [root]
    for part in _split_top(selector, ">>"):
        part = part.strip()
        if not part:
            raise UnsupportedSelector("empty chain part")
        found: List[Any] = []
        seen = set()
        for scope in scopes:
            for el in _match_part(root, scope, part):
                if id(el) not in seen:
                    seen.add(id(el))
                    found.append(el)
        order = {id(e): i for i, e in enumerate(_elements(root))}
        found.sort(key=lambda e: order.get(id(e), 0))
        scopes = found
    return scopes


def count_matches(html: str, selector: str) -> int:
    return len(match_selector(parse_html(html), selector))


def _match_part(root, scope, part: str) -> List[Any]:
    m = _ENGINE_RE.match(part)
    engine, body = (m.group(1).lower(), part[m.end():].strip()) if m else (None, part)
    if engine is None:
        if part.startswith("//") or part.startswith("..") or part.startswith("(//"):
            engine = "xpath"
        elif part[0] in "\"'":
            engine, body = "text", part
        else:
            engine = "css"
    if engine == "xpath":
        return _match_xpath(root, scope, body)
    if engine == "text":
        return _match_text_engine(scope, body)
    if engine in ("role", "internal:role"):
        return _match_role(root, scope, body)
    if engine == "id":
        return [e for e in _elements(scope) if e.get("id") == _unquote(body)]
    if engine in ("data-testid", "data-test-id", "data-test"):
        return [e for e in _elements(scope) if e.get(engine) == _unquote(body)]
    sel_list = [_parse_complex(s) for s in _split_top(body, ",")]
    return [e for e in _elements(scope) if any(_match_complex(e, c) for c in sel_list)]


def _match_xpath(root, scope, expr: str) -> List[Any]:
    from lxml import etree
    if scope is not root and expr.startswith("/"):
        expr = "." + expr
    try:
        res = (root if scope is root else scope).xpath(expr)
    except (etree.XPathError, TypeError, ValueError) as exc:
        raise UnsupportedSelector(f"bad xpath: {exc}") from exc
    if not isinstance(res, list):
        raise UnsupportedSelector("xpath does not select elements")
    return [e for e in res if hasattr(e, "tag") and isinstance(e.tag, str)]


def _text_matcher(body: str):
    body = body.strip()
    if body.startswith("/"):
        raise UnsupportedSelector("regex text selectors are not supported")
    if body and body[0] in "\"'":
        want = _norm(_unquote(body))
        return lambda t: t == want
    want = _norm(body).lower()
    return lambda t: want in t.lower()


def _innermost(candidates: Iterable[Any], pred) -> List[Any]:
    out = []
    for el in candidates:
        if el.tag in _NON_TEXT or not pred(text_of(el)):
            continue
        if any(isinstance(c.tag, str) and c.tag not in _NON_TEXT and pred(text_of(c)) for c in el):
            continue
        out.append(el)
    return out


def _match_text_engine(scope, body: str) -> List[Any]:
    return _innermost(_elements(scope), _text_matcher(body))


def _is_hidden(el) -> bool:
    node = el
    while node is not None and isinstance(node.tag, str):
        if node.get("hidden") is not None or node.get("aria-hidden") == "true":
            return True
        if re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", node.get("style") or "", re.I):
            return True
        if node.tag in ("template", "head", "script", "style", "noscript"):
            return True
        if node.tag == "input" and _attr(node, "type").lower() == "hidden":
            return True
        node = node.getparent()
    return False


def _match_role(root, scope, body: str) -> List[Any]:
    m = re.match(r"^([a-zA-Z]+)\s*(.*)$", body.strip(), re.S)
    if not m:
        raise UnsupportedSelector("role selector needs a role")
    role, rest = m.group(1).lower(), m.group(2).strip()
    name_pred = None
    level = None
    include_hidden = False
    for attr in re.findall(r"\[([^\]]*)\]", rest):
        am = re.match(r"^\s*([\w-]+)\s*(?:=\s*(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|[\w.+-]+)\s*([is])?)?\s*$", attr)
        if not am:
            raise UnsupportedSelector(f"role attribute not understood: [{attr}]")
        key, raw, flag = am.group(1), am.group(2), am.group(3)
        if key == "name" and raw is not None:
            if raw[0] in "\"'":
                want = _norm(_unquote(raw))
                if flag == "i":
                    name_pred = (lambda w: lambda n: w.lower() in n.lower())(want)
                else:
                    name_pred = (lambda w: lambda n: n == w)(want)
            else:
                want = _norm(raw)
                name_pred = (lambda w: lambda n: w.lower() in n.lower())(want)
        elif key == "level" and raw is not None:
            level = int(_unquote(raw))
        elif key == "include-hidden":
            include_hidden = raw is None or _unquote(raw) == "true"
        else:
            raise UnsupportedSelector(f"role attribute '{key}' is not supported")
    if re.sub(r"\[[^\]]*\]", "", rest).strip():
        raise UnsupportedSelector("trailing text in role selector")
    out = []
    for el in _elements(scope):
        if implicit_role(el) != role:
            continue
        if not include_hidden and _is_hidden(el):
            continue
        if level is not None:
            lv = int(el.tag[1]) if re.fullmatch(r"h[1-6]", el.tag) else int(_attr(el, "aria-level") or 0)
            if lv != level:
                continue
        if name_pred is not None and not name_pred(accessible_name(el, root)):
            continue
        out.append(el)
    return out


# --- CSS ------------------------------------------------------------------

def _read_ident(s: str, i: int) -> Tuple[str, int]:
    out = []
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            out.append(s[i + 1])
            i += 2
        elif ch.isalnum() or ch in "-_" or ord(ch) > 127:
            out.append(ch)
            i += 1
        else:
            break
    return "".join(out), i


def _read_balanced(s: str, i: int, close: str) -> Tuple[str, int]:
    """Read from s[i] (just after the opener) to the matching `close`."""
    opener = "(" if close == ")" else "["
    depth, quote, start = 1, None, i
    while i < len(s):
        ch = s[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == opener:
            depth += 1
        elif ch == close:
            depth -= 1
            if depth == 0:
                return s[start:i], i + 1
        i += 1
    raise UnsupportedSelector("unbalanced selector")


def _parse_compound(s: str) -> Dict[str, Any]:
    comp: Dict[str, Any] = {"tag": None, "ids": [], "classes": [], "attrs": [], "pseudos": []}
    i = 0
    if s.startswith("*"):
        i = 1
    elif s and (s[0].isalpha() or s[0] == "\\"):
        tag, i = _read_ident(s, 0)
        comp["tag"] = tag.lower()
    while i < len(s):
        ch = s[i]
        if ch == "#":
            v, i = _read_ident(s, i + 1)
            if not v:
                raise UnsupportedSelector("bad #id")
            comp["ids"].append(v)
        elif ch == ".":
            v, i = _read_ident(s, i + 1)
            if not v:
                raise UnsupportedSelector("bad .class")
            comp["classes"].append(v)
        elif ch == "[":
            inner, i = _read_balanced(s, i + 1, "]")
            am = re.match(
                r"^\s*([\w:-]+)\s*(?:([~|^$*]?=)\s*(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|[^\s\"']+)\s*([iIsS])?)?\s*$",
                inner)
            if not am:
                raise UnsupportedSelector(f"attribute selector not understood: [{inner}]")
            value = _unquote(am.group(3)) if am.group(3) is not None else None
            comp["attrs"].append((am.group(1).lower(), am.group(2), value, (am.group(4) or "").lower() == "i"))
        elif ch == ":":
            if s.startswith("::", i):
                raise UnsupportedSelector("pseudo-elements are not supported")
            name, j = _read_ident(s, i + 1)
            arg = None
            if j < len(s) and s[j] == "(":
                arg, j = _read_balanced(s, j + 1, ")")
            comp["pseudos"].append(_compile_pseudo(name.lower(), arg))
            i = j
        else:
            raise UnsupportedSelector(f"unexpected '{ch}' in selector")
    return comp


def _parse_complex(s: str) -> List[Tuple[Optional[str], Dict[str, Any]]]:
    """[(combinator, compound)] left to right; the first combinator is None."""
    s = s.strip()
    if not s:
        raise UnsupportedSelector("empty selector")
    out: List[Tuple[Optional[str], Dict[str, Any]]] = []
    i, comb = 0, None
    while i < len(s):
        while i < len(s) and s[i].isspace():
            i += 1
            if out and comb is None:
                comb = " "
        if i >= len(s):
            break
        if s[i] in ">+~":
            comb = s[i]
            i += 1
            continue
        start, depth, quote = i, 0, None
        while i < len(s):
            ch = s[i]
            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
            elif ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            elif depth == 0 and (ch.isspace() or ch in ">+~"):
                break
            elif ch == "\\":
                i += 1
            i += 1
        out.append((comb if out else None, _parse_compound(s[start:i])))
        comb = None
    if not out:
        raise UnsupportedSelector("empty selector")
    return out


def _nth(expr: str):
    e = expr.replace(" ", "").lower()
    if e == "odd":
        return 2, 1
    if e == "even":
        return 2, 0
    m = re.fullmatch(r"([+-]?\d*)n([+-]\d+)?", e)
    if m:
        a = m.group(1)
        a = 1 if a in ("", "+") else (-1 if a == "-" else int(a))
        return a, int(m.group(2) or 0)
    if re.fullmatch(r"[+-]?\d+", e):
        return 0, int(e)
    raise UnsupportedSelector(f"nth expression not understood: {expr}")


def _nth_ok(pos: int, ab) -> bool:
    a, b = ab
    if a == 0:
        return pos == b
    return (pos - b) % a == 0 and (pos - b) // a >= 0


def _siblings(el) -> List[Any]:
    parent = el.getparent()
    if parent is None:
        return [el]
    return [c for c in parent if isinstance(c.tag, str)]


def _compile_pseudo(name: str, arg: Optional[str]):
    if name == "has-text":
        want = _norm(_unquote(arg or "")).lower()
        return lambda el: want in text_of(el).lower()
    if name == "text":
        pred = _text_matcher(arg or "")
        return lambda el: bool(_innermost([el], pred))
    if name == "text-is":
        want = _norm(_unquote(arg or ""))
        return lambda el: text_of(el) == want
    if name == "visible":
        return lambda el: not _is_hidden(el)
    if name in ("not", "is", "where", "matches"):
        alts = [_parse_complex(p) for p in _split_top(arg or "", ",")]
        if name == "not":
            return lambda el: not any(_match_complex(el, c) for c in alts)
        return lambda el: any(_match_complex(el, c) for c in alts)
    if name == "has":
        rel = (arg or "").strip()
        child_only = rel.startswith(">")
        complex_ = _parse_complex(rel[1:] if child_only else rel)
        if child_only:
            return lambda el: any(isinstance(c.tag, str) and _match_complex(c, complex_) for c in el)
        return lambda el: any(_match_complex(d, complex_) for d in _elements(el))
    if name == "first-child":
        return lambda el: _siblings(el)[0] is el
    if name == "last-child":
        return lambda el: _siblings(el)[-1] is el
    if name == "only-child":
        return lambda el: len(_siblings(el)) == 1
    if name == "nth-child":
        ab = _nth(arg or "")
        return lambda el: _nth_ok(_siblings(el).index(el) + 1, ab)
    if name in ("nth-of-type", "first-of-type", "last-of-type"):
        ab = _nth(arg or "1") if name == "nth-of-type" else None

        def of_type(el):
            same = [s for s in _siblings(el) if s.tag == el.tag]
            if name == "first-of-type":
                return same[0] is el
            if name == "last-of-type":
                return same[-1] is el
            return _nth_ok(same.index(el) + 1, ab)
        return of_type
    if name == "checked":
        return lambda el: el.get("checked") is not None or el.get("selected") is not None
    if name == "disabled":
        return lambda el: el.get("disabled") is not None
    if name == "enabled":
        return lambda el: el.get("disabled") is None
    if name == "empty":
        return lambda el: len(el) == 0 and not (el.text or "")
    raise UnsupportedSelector(f"pseudo-class :{name} is not supported")


def _match_compound(el, comp) -> bool:
    if comp["tag"] and el.tag != comp["tag"]:
        return False
    if any(el.get("id") != i for i in comp["ids"]):
        return False
    if comp["classes"]:
        have = set(_attr(el, "class").split())
        if any(c not in have for c in comp["classes"]):
            return False
    for name, op, value, ci in comp["attrs"]:
        v = el.get(name)
        if v is None:
            return False
        if op is None:
            continue
        a, b = (v.lower(), value.lower()) if ci else (v, value)
        ok = {
            "=": a == b, "^=": bool(b) and a.startswith(b), "$=": bool(b) and a.endswith(b),
            "*=": bool(b) and b in a, "~=": b in a.split(), "|=": a == b or a.startswith(b + "-"),
        }[op]
        if not ok:
            return False
    return all(p(el) for p in comp["pseudos"])


def _match_complex(el, complex_) -> bool:
    def match_from(node, idx) -> bool:
        comb, comp = complex_[idx]
        if not _match_compound(node, comp):
            return False
        if idx == 0:
            return True
        prev_comb = comb
        if prev_comb == ">":
            parent = node.getparent()
            return parent is not None and isinstance(parent.tag, str) and match_from(parent, idx - 1)
        if prev_comb == " ":
            return any(match_from(a, idx - 1) for a in node.iterancestors() if isinstance(a.tag, str))
        if prev_comb == "+":
            prev = node.getprevious()
            while prev is not None and not isinstance(prev.tag, str):
                prev = prev.getprevious()
            return prev is not None and match_from(prev, idx - 1)
        if prev_comb == "~":
            return any(match_from(p, idx - 1) for p in node.itersiblings(preceding=True)
                       if isinstance(p.tag, str))
        return False
    return match_from(el, len(complex_) - 1)


# ---------------------------------------------------------------------------
# Snapshot store + accept-time recheck
# ---------------------------------------------------------------------------

def snapshot_key(run_id: int, test_case_id: int, step_id: str, html: str) -> str:
    """Under the run's prefix, so deleting/purging the run's artifacts takes it too."""
    safe_step = re.sub(r"[^A-Za-z0-9_-]+", "_", step_id or "step")[:60] or "step"
    digest = hashlib.sha256(html.encode("utf-8", "replace")).hexdigest()[:12]
    return f"runs/{int(run_id)}/heal/{int(test_case_id)}-{safe_step}-{digest}.html"


def store_snapshot(run_id: Optional[int], test_case_id: int, step_id: str, html: str) -> Optional[str]:
    """Upload an (already redacted) snapshot; None when there is nothing to
    store or the object store is unavailable (the proposal then just has no
    recheck evidence)."""
    if not run_id or not html:
        return None
    data = html.encode("utf-8", "replace")[:MAX_SNAPSHOT_BYTES]
    key = snapshot_key(run_id, test_case_id, step_id, html)
    try:
        from app.core.storage import object_store
        object_store.upload_fileobj(io.BytesIO(data), key, "text/html; charset=utf-8")
        return key
    except Exception as exc:  # noqa: BLE001
        print(f"[Heal] Could not store DOM snapshot {key}: {exc}")
        return None


def load_snapshot(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    try:
        from app.core.storage import object_store
        return object_store.get_object_bytes(key, max_bytes=MAX_SNAPSHOT_BYTES).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        print(f"[Heal] Could not load DOM snapshot {key}: {exc}")
        return None


def recheck(html: Optional[str], selector: str) -> Dict[str, Any]:
    """Re-evaluate `selector` against a stored snapshot.

    status: unique | not_unique | no_match | unsupported | unavailable. Only
    `unique` lets an accept through when a snapshot exists.
    """
    if html is None:
        return {"status": "unavailable", "matches": None, "selector": selector,
                "detail": "No DOM snapshot is stored for this proposal; uniqueness was not rechecked."}
    try:
        n = count_matches(html, selector)
    except UnsupportedSelector as exc:
        return {"status": "unsupported", "matches": None, "selector": selector,
                "detail": f"The selector cannot be checked against the stored DOM ({exc}). "
                          "Pick another candidate or edit the step in the test builder."}
    if n == 1:
        return {"status": "unique", "matches": 1, "selector": selector,
                "detail": "Matches exactly one element in the stored DOM snapshot."}
    if n == 0:
        return {"status": "no_match", "matches": 0, "selector": selector,
                "detail": "Matches no element in the stored DOM snapshot."}
    return {"status": "not_unique", "matches": n, "selector": selector,
            "detail": f"Matches {n} elements in the stored DOM snapshot; a heal must match exactly one."}
