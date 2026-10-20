/**
 * A small, forgiving HTML tokenizer and tree builder.
 *
 * Exists for two jobs that must not depend on a browser: redacting a DOM
 * before it reaches an LLM (`redactDom` in redact.ts) and deriving selector
 * candidates from that redacted DOM (heal-candidates.ts). It is not a spec
 * HTML parser — no implicit <tbody>, no adoption agency — but it never throws,
 * keeps raw-text elements (script/style/textarea/title) intact as single
 * tokens, and closes unbalanced tags the way a reader would expect. The
 * backend twin parses with lxml, so small structural differences between the
 * two are tolerated by design (the runtime match counts come from the live
 * page, and the accept-time recheck from the backend's own parse).
 */

export type Attr = [string, string | null];

export type Token =
    | { kind: 'start'; tag: string; attrs: Attr[]; selfClosing: boolean }
    | { kind: 'end'; tag: string }
    | { kind: 'text'; text: string }
    | { kind: 'comment' }
    | { kind: 'doctype' };

export const VOID_ELEMENTS = new Set([
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta',
    'param', 'source', 'track', 'wbr',
]);

/** Elements whose content is not markup: read verbatim up to the close tag. */
export const RAW_TEXT_ELEMENTS = new Set(['script', 'style', 'textarea', 'title', 'xmp', 'noscript']);

const NAMED_ENTITIES: Record<string, string> = {
    amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', '#39': "'",
};

export function decodeEntities(s: string): string {
    if (!s || s.indexOf('&') < 0) return s;
    return s.replace(/&(#x[0-9a-fA-F]+|#\d+|[a-zA-Z]+\d*);/g, (m, body: string) => {
        if (body[0] === '#') {
            const code = body[1] === 'x' || body[1] === 'X' ? parseInt(body.slice(2), 16) : parseInt(body.slice(1), 10);
            if (!Number.isFinite(code) || code <= 0 || code > 0x10ffff) return m;
            try { return String.fromCodePoint(code); } catch { return m; }
        }
        const v = NAMED_ENTITIES[body.toLowerCase()];
        return v === undefined ? m : v;
    });
}

export const escapeText = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
export const escapeAttr = (s: string) => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;');

const isNameStart = (c: string) => /[A-Za-z]/.test(c);
const isSpace = (c: string) => c === ' ' || c === '\n' || c === '\t' || c === '\r' || c === '\f';

export function tokenize(html: string, opts: { xml?: boolean } = {}): Token[] {
    const xml = !!opts.xml;
    const fold = (s: string) => (xml ? s : s.toLowerCase());
    const out: Token[] = [];
    const n = html.length;
    let i = 0;
    let text = '';
    const flushText = () => {
        if (text) out.push({ kind: 'text', text: decodeEntities(text) });
        text = '';
    };
    while (i < n) {
        const c = html[i];
        if (c !== '<') { text += c; i++; continue; }
        if (html.startsWith('<!--', i)) {
            flushText();
            const end = html.indexOf('-->', i + 4);
            i = end < 0 ? n : end + 3;
            out.push({ kind: 'comment' });
            continue;
        }
        if (html[i + 1] === '!' || html[i + 1] === '?') {
            flushText();
            const end = html.indexOf('>', i + 2);
            // Only a real <!DOCTYPE> is a doctype; <![CDATA[…]]>, <?xml …?> and
            // other declarations are dropped like comments (Python's
            // HTMLParser twin does the same — redactDom used to emit a
            // <!DOCTYPE html> for each of them).
            const doctype = /^<!doctype/i.test(html.slice(i, i + 9));
            i = end < 0 ? n : end + 1;
            out.push(doctype ? { kind: 'doctype' } : { kind: 'comment' });
            continue;
        }
        if (html[i + 1] === '/' && isNameStart(html[i + 2] || '')) {
            flushText();
            let j = i + 2;
            while (j < n && !isSpace(html[j]) && html[j] !== '>' && html[j] !== '/') j++;
            const tag = fold(html.slice(i + 2, j));
            const end = html.indexOf('>', j);
            i = end < 0 ? n : end + 1;
            out.push({ kind: 'end', tag });
            continue;
        }
        if (!isNameStart(html[i + 1] || '')) { text += c; i++; continue; }

        flushText();
        let j = i + 1;
        while (j < n && !isSpace(html[j]) && html[j] !== '>' && html[j] !== '/') j++;
        const tag = fold(html.slice(i + 1, j));
        const attrs: Attr[] = [];
        let selfClosing = false;
        while (j < n) {
            while (j < n && isSpace(html[j])) j++;
            if (j >= n) break;
            if (html[j] === '>') { j++; break; }
            if (html[j] === '/') {
                if (html[j + 1] === '>') { selfClosing = true; j += 2; break; }
                j++; continue;
            }
            let k = j;
            while (k < n && !isSpace(html[k]) && html[k] !== '=' && html[k] !== '>' && !(html[k] === '/' && html[k + 1] === '>')) k++;
            const name = fold(html.slice(j, k));
            j = k;
            while (j < n && isSpace(html[j])) j++;
            let value: string | null = null;
            if (html[j] === '=') {
                j++;
                while (j < n && isSpace(html[j])) j++;
                const q = html[j];
                if (q === '"' || q === "'") {
                    const end = html.indexOf(q, j + 1);
                    value = html.slice(j + 1, end < 0 ? n : end);
                    j = end < 0 ? n : end + 1;
                } else {
                    let e = j;
                    while (e < n && !isSpace(html[e]) && html[e] !== '>') e++;
                    value = html.slice(j, e);
                    j = e;
                }
                value = decodeEntities(value);
            }
            if (name && !attrs.some(a => a[0] === name)) attrs.push([name, value]);
        }
        i = j;
        out.push({ kind: 'start', tag, attrs, selfClosing });
        if (!xml && RAW_TEXT_ELEMENTS.has(tag) && !selfClosing) {
            const re = new RegExp(`</${tag}[\\s>/]`, 'i');
            const rest = html.slice(i);
            const m = re.exec(rest);
            const body = m ? rest.slice(0, m.index) : rest;
            if (body) out.push({ kind: 'text', text: tag === 'script' || tag === 'style' ? body : decodeEntities(body) });
            if (m) {
                const end = html.indexOf('>', i + m.index);
                i = end < 0 ? n : end + 1;
                out.push({ kind: 'end', tag });
            } else {
                i = n;
            }
        }
    }
    flushText();
    return out;
}

// --------------------------------------------------------------------------
// Tree
// --------------------------------------------------------------------------

export interface HtmlElement {
    tag: string;
    attrs: Map<string, string>;
    children: Array<HtmlElement | string>;
    parent: HtmlElement | null;
}

export function buildTree(html: string): HtmlElement {
    const root: HtmlElement = { tag: '#root', attrs: new Map(), children: [], parent: null };
    const stack: HtmlElement[] = [root];
    for (const tok of tokenize(html)) {
        const top = stack[stack.length - 1];
        if (tok.kind === 'text') {
            top.children.push(tok.text);
        } else if (tok.kind === 'start') {
            const el: HtmlElement = {
                tag: tok.tag,
                attrs: new Map(tok.attrs.map(([k, v]) => [k, v ?? ''])),
                children: [],
                parent: top,
            };
            top.children.push(el);
            if (!VOID_ELEMENTS.has(tok.tag) && !tok.selfClosing) stack.push(el);
        } else if (tok.kind === 'end') {
            for (let d = stack.length - 1; d > 0; d--) {
                if (stack[d].tag === tok.tag) { stack.length = d; break; }
            }
        }
    }
    return root;
}

export function* walk(el: HtmlElement): Generator<HtmlElement> {
    for (const c of el.children) {
        if (typeof c !== 'string') {
            yield c;
            yield* walk(c);
        }
    }
}

const NON_TEXT = new Set(['script', 'style', 'template', 'head']);

export function textContent(el: HtmlElement): string {
    let out = '';
    for (const c of el.children) {
        if (typeof c === 'string') out += c;
        else if (!NON_TEXT.has(c.tag)) out += textContent(c);
    }
    return out;
}

export const normalizeSpace = (s: string) => s.replace(/[\s ]+/g, ' ').trim();

// --------------------------------------------------------------------------
// Compound-selector matching, for redaction mask selectors only
// --------------------------------------------------------------------------

interface Compound {
    tag: string | null;
    ids: string[];
    classes: string[];
    attrs: Array<{ name: string; op: string | null; value: string }>;
}

/**
 * Parse the LAST compound of each comma-separated selector and drop
 * pseudo-classes. Matching only that compound against an element matches a
 * superset of what the full selector would — which is the safe direction for
 * redaction (it can over-mask, never under-mask). Returns null for a selector
 * this cannot read at all, which callers treat as "mask nothing extra".
 */
export function parseMaskSelector(selector: string): Compound[] | null {
    const out: Compound[] = [];
    for (const part of selector.split(',')) {
        const segs = part.trim().split(/\s*[\s>+~]\s*/).filter(Boolean);
        if (!segs.length) continue;
        let s = segs[segs.length - 1].replace(/::?[a-zA-Z-]+(\([^)]*\))?/g, '');
        const c: Compound = { tag: null, ids: [], classes: [], attrs: [] };
        const tagM = /^([a-zA-Z][a-zA-Z0-9-]*|\*)/.exec(s);
        if (tagM) { if (tagM[1] !== '*') c.tag = tagM[1].toLowerCase(); s = s.slice(tagM[1].length); }
        const re = /#([\w-]+)|\.([\w-]+)|\[\s*([\w:-]+)\s*(?:([~|^$*]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]+))\s*[is]?)?\s*\]/gy;
        let m: RegExpExecArray | null;
        let consumed = 0;
        while ((m = re.exec(s)) !== null) {
            consumed = re.lastIndex;
            if (m[1]) c.ids.push(m[1]);
            else if (m[2]) c.classes.push(m[2]);
            else c.attrs.push({ name: m[3].toLowerCase(), op: m[4] || null, value: m[5] ?? m[6] ?? m[7] ?? '' });
        }
        if (consumed !== s.length) return null;
        if (!c.tag && !c.ids.length && !c.classes.length && !c.attrs.length) return null;
        out.push(c);
    }
    return out.length ? out : null;
}

export function matchesCompound(tag: string, attrs: Map<string, string>, c: Compound): boolean {
    if (c.tag && c.tag !== tag) return false;
    if (c.ids.some(id => attrs.get('id') !== id)) return false;
    const classes = new Set((attrs.get('class') || '').split(/\s+/).filter(Boolean));
    if (c.classes.some(cl => !classes.has(cl))) return false;
    for (const a of c.attrs) {
        const v = attrs.get(a.name);
        if (v === undefined) return false;
        switch (a.op) {
            case null: break;
            case '=': if (v !== a.value) return false; break;
            case '^=': if (!v.startsWith(a.value)) return false; break;
            case '$=': if (!v.endsWith(a.value)) return false; break;
            case '*=': if (!v.includes(a.value)) return false; break;
            case '~=': if (!v.split(/\s+/).includes(a.value)) return false; break;
            case '|=': if (v !== a.value && !v.startsWith(a.value + '-')) return false; break;
            default: return false;
        }
    }
    return true;
}
