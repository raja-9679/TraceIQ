#!/usr/bin/env python3
"""Write a Markdown copy of every guide page, next to its HTML.

    export_md.py <repo root>          (run.sh calls this after every capture)

frontend/public/docs/<group>/<page>.html  ->  <group>/<page>.md, same folder,
so the picture links (../media/...) are the same. Each copy ends with the
page's video script: every line the walkthrough says, in order, read from
the page's Playwright capture spec (e2e/docs-capture/pages/<group>-<page>.spec.ts),
which is linked too. The HTML is the source; edit that and re-run this.

Only the elements the guide pages use are converted. The menu, table of
contents and pager are filled in by docs.js when the page is viewed, so they
have no Markdown form.
"""
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

VOID = {"img", "source", "br", "meta", "link", "input", "hr"}
SKIP_CLASSES = {"toc", "sidenav", "pager", "crumbs", "footer-note", "icon"}


class Node:
    def __init__(self, tag: str, attrs: dict, parent: "Node | None"):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list = []

    @property
    def cls(self) -> set:
        return set((self.attrs.get("class") or "").split())


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.cur = Node("root", {}, None)

    def handle_starttag(self, tag, attrs):
        n = Node(tag, dict(attrs), self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def esc(s: str) -> str:
    """Plain text -> Markdown text: keep <n>/<str> visible, don't start lists."""
    return s.replace("\\", "\\\\").replace("<", "&lt;").replace(">", "&gt;")


def inline(n) -> str:
    if isinstance(n, str):
        return esc(n)
    if n.cls & SKIP_CLASSES or n.tag in ("script", "style"):
        return ""
    inner = "".join(inline(c) for c in n.children)
    if n.tag in ("strong", "b"):
        return f"**{inner.strip()}**" if inner.strip() else ""
    if n.tag in ("em", "i"):
        return f"*{inner.strip()}*" if inner.strip() else ""
    if n.tag in ("code", "kbd"):
        raw = "".join(c if isinstance(c, str) else "" for c in n.children)
        return f"`{raw}`"
    if n.tag == "a":
        return f"[{inner}]({n.attrs.get('href', '')})"
    if n.tag == "br":
        return " "
    return inner


def text_of(n) -> str:
    return re.sub(r"\s+", " ", inline(n)).strip()


def blocks(n, out: list[str], quote: str = "", depth: int = 0) -> None:
    """Render block-level children of n into out (a list of lines)."""
    def emit(line: str = "") -> None:
        out.append((quote + line).rstrip() if (line or quote) else "")

    run: list = []        # pending inline content

    def flush_run():
        t = re.sub(r"\s+", " ", "".join(inline(c) for c in run)).strip()
        run.clear()
        if t:
            emit(t)
            emit()

    for c in n.children:
        if isinstance(c, str) or c.tag in ("strong", "b", "em", "i", "code", "kbd", "a", "br") \
                or (c.tag == "span" and not c.cls & {"num"}):
            run.append(c)
            continue
        flush_run()
        if c.cls & SKIP_CLASSES or c.tag in ("script", "style", "header", "aside", "nav"):
            continue
        tag = c.tag
        if tag in ("h1", "h2", "h3", "h4"):
            if text_of(c):
                emit("#" * int(tag[1]) + " " + text_of(c))
                emit()
        elif tag == "p":
            t = text_of(c)
            if t:
                emit(t)
                emit()
        elif tag in ("ul", "ol"):
            i = 0
            for li in c.children:
                if isinstance(li, Node) and li.tag == "li":
                    i += 1
                    emit(("  " * depth) + (f"{i}. " if tag == "ol" else "- ") + text_of(li))
            emit()
        elif tag == "table":
            rows = [[text_of(td).replace("|", "\\|") for td in tr.children
                     if isinstance(td, Node) and td.tag in ("td", "th")]
                    for tr in walk(c, "tr")]
            if rows:
                w = max(len(r) for r in rows)
                rows = [r + [""] * (w - len(r)) for r in rows]
                emit("| " + " | ".join(rows[0]) + " |")
                emit("|" + "---|" * w)
                for r in rows[1:]:
                    emit("| " + " | ".join(r) + " |")
                emit()
        elif tag == "img":
            emit(f"![{esc(c.attrs.get('alt', ''))}]({c.attrs.get('src', '')})")
            emit()
        elif tag == "video":
            mp4 = next((s for s in walk(c, "source") if s.attrs.get("type") == "video/mp4"), None)
            if mp4 is not None:
                emit(f"[▶ Watch the video]({mp4.attrs.get('src', '')})")
                emit()
        elif tag == "figcaption":
            t = text_of(c)
            if t:
                emit(f"*{t}*")
                emit()
        elif tag == "div" and "part" in c.cls:
            # A numbered section: "① The four tiles" -> "## 1. The four tiles".
            num = next((text_of(s) for s in walk(c, "span") if "num" in s.cls), "")
            h = next((s for s in c.children if isinstance(s, Node) and s.tag[:1] == "h"), None)
            if h is not None:
                emit("#" * int(h.tag[1]) + f" {num}. " + text_of(h))
                emit()
        elif tag == "div" and "meta-row" in c.cls:
            emit(" · ".join(text_of(s) for s in c.children if isinstance(s, Node)))
            emit()
        elif tag == "div" and c.cls & {"callout", "in-short", "mini"}:
            title = next((s for s in walk(c, "*") if s.cls & {"label", "title"} or s.tag == "h4"), None)
            sub: list[str] = []
            if title is not None:
                sub.append(f"**{text_of(title).strip('*')}**")
                sub.append("")
                title.children = []
            blocks(c, sub, "", depth)
            while sub and sub[-1] == "":
                sub.pop()
            for line in sub:
                emit("> " + line if line else ">")
            emit()
        else:
            blocks(c, out, quote, depth)
    flush_run()


def walk(n: Node, tag: str):
    for c in n.children:
        if isinstance(c, Node):
            if tag == "*" or c.tag == tag:
                yield c
            yield from walk(c, tag)


def convert(html: str) -> str:
    t = Tree()
    t.feed(html)
    main = next(walk(t.root, "main"))
    out: list[str] = []
    blocks(main, out)
    md = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"


def _js_string(src: str, i: int) -> tuple[str, int]:
    """Read the JS string literal starting at src[i] ('...' or "..." or `...`)."""
    q, j, out = src[i], i + 1, []
    while src[j] != q:
        if src[j] == "\\":
            j += 1
        out.append(src[j])
        j += 1
    return "".join(out), j + 1


def narration(spec: str) -> list[str]:
    """Every spoken caption in a capture spec, in order (`say` wins over the text)."""
    lines = []
    for m in re.finditer(r"caption\(\s*p\s*,\s*", spec):
        i = m.end()
        if spec[i] not in "'\"`":
            continue
        text, i = _js_string(spec, i)
        say = re.match(r"\s*,\s*\{[^}]*?say:\s*", spec[i:i + 800], re.S)
        if say:
            text, _ = _js_string(spec, i + say.end())
        if text:
            lines.append(text)
    return lines


def main() -> None:
    root = Path(sys.argv[1])
    docs = root / "frontend/public/docs"
    for html_file in sorted(docs.glob("*.html")) + sorted(docs.glob("*/*.html")):
        group, page = html_file.parent.name, html_file.stem
        md = convert(html_file.read_text())
        spec = root / f"e2e/docs-capture/pages/{group}-{page}.spec.ts"
        if spec.exists():
            rel = (Path("../../../../e2e/docs-capture/pages") / spec.name).as_posix()
            said = narration(spec.read_text())
            md += ("\n## Video script\n\nWhat the walkthrough says, in order. The video and every "
                   f"picture on this page are made by the Playwright script [`{spec.name}`]({rel}).\n\n"
                   + "\n".join(f"{n}. {esc(s)}" for n, s in enumerate(said, 1)) + "\n")
        out = html_file.with_suffix(".md")
        out.write_text(f"<!-- Generated from {html_file.name} by e2e/docs-capture/export_md.py. "
                       "Edit the HTML, not this file. -->\n\n" + md)
        print(out.relative_to(root))


if __name__ == "__main__":
    main()
