/**
 * Selector-heal candidates (W5): several replacement selectors for a broken
 * step, ranked, each with the evidence behind its rank.
 *
 * Sources:
 * - deterministic strategies over the REDACTED DOM — test ids, id, role +
 *   accessible name, aria-label/placeholder/name, unique text, and a short
 *   CSS path without nth-child — for the few elements that look most like
 *   what the old selector and the step's intent describe;
 * - the LLM's suggestion, when an LLM is configured.
 *
 * Every candidate is counted against the live page by the caller (`count`);
 * only a candidate matching exactly one element is "applicable". Score =
 *   0.35 stability (test id > id > role > label > text > css path)
 * + 0.35 similarity to the old selector / intent
 * + 0.15 LLM (its own confidence, or agreement with the LLM's element)
 * + 0.15 uniqueness.
 *
 * Twin of backend/app/services/heal_candidates.py — the weights, stability
 * table, strategy names and selector forms must stay identical so a reviewer
 * sees the same evidence whichever side produced a proposal.
 */
import { buildTree, HtmlElement, normalizeSpace, textContent, walk } from './html-lite';

export type Strategy = 'test-id' | 'id' | 'role' | 'label' | 'text' | 'css-path' | 'llm';

export const STABILITY: Record<Exclude<Strategy, 'llm'>, number> = {
    'test-id': 1.0, id: 0.85, role: 0.75, label: 0.65, text: 0.5, 'css-path': 0.3,
};
export const GENERATED_ID_STABILITY = 0.35;
export const WEIGHTS = { stability: 0.35, similarity: 0.35, llm: 0.15, uniqueness: 0.15 };
/** Default LLM confidence when the model returns a bare selector. */
export const DEFAULT_LLM_CONFIDENCE = 0.6;
export const MAX_TARGETS = 3;
export const MAX_CANDIDATES = 12;
const MIN_TARGET_SIMILARITY = 0.34;

export const TEST_ID_ATTRS = ['data-testid', 'data-test-id', 'data-test', 'data-qa', 'data-cy'];

export interface Breakdown { stability: number; similarity: number; llm: number; uniqueness: number }

export interface HealCandidate {
    selector: string;
    strategy: Strategy;
    source: 'deterministic' | 'llm' | 'both';
    matches: number;
    applicable: boolean;
    score: number;
    breakdown: Breakdown;
}

export interface DraftCandidate {
    selector: string;
    strategy: Strategy;
    stability: number;
    similarity: number;
    /** Index of the target element it was derived from (deterministic only). */
    target?: number;
}

// --------------------------------------------------------------------------
// Tokens and similarity
// --------------------------------------------------------------------------

const STOP_TOKENS = new Set([
    'div', 'span', 'css', 'xpath', 'text', 'role', 'name', 'has', 'nth', 'child', 'of', 'type',
    'the', 'an', 'and', 'to', 'is', 'on', 'in', 'for', 'first', 'last', 'data', 'testid', 'test',
    'qa', 'cy', 'aria', 'label', 'id', 'class', 'click', 'element', 'visible', 'exact',
]);

/** Lower-case words of a selector/intent/attribute: split on non-alphanumerics and camelCase. */
export function tokens(s: string | null | undefined): string[] {
    if (!s) return [];
    return s
        .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
        .toLowerCase()
        .split(/[^a-z0-9]+/)
        .filter(t => t.length >= 2 && !STOP_TOKENS.has(t) && !/^\d+$/.test(t));
}

function tokenScore(a: string, b: string): number {
    if (a === b) return 1;
    if (Math.min(a.length, b.length) >= 3 && (a.startsWith(b) || b.startsWith(a))) return 0.8;
    return 0;
}

/** Mean over the query tokens of each one's best match in `features` (0..1). */
export function similarity(query: string[], features: string[]): number {
    const q = Array.from(new Set(query));
    if (!q.length || !features.length) return 0;
    let total = 0;
    for (const t of q) total += Math.max(...features.map(f => tokenScore(t, f)));
    return Math.round((total / q.length) * 1000) / 1000;
}

// --------------------------------------------------------------------------
// Element facts
// --------------------------------------------------------------------------

const INPUT_ROLES: Record<string, string> = {
    button: 'button', submit: 'button', reset: 'button', image: 'button',
    checkbox: 'checkbox', radio: 'radio', range: 'slider', number: 'spinbutton', search: 'searchbox',
    text: 'textbox', email: 'textbox', tel: 'textbox', url: 'textbox', '': 'textbox', password: 'textbox',
};

/** Roles a role= candidate may be built on: interactive or headings. */
export const ROLE_CANDIDATE_ROLES = new Set([
    'button', 'link', 'checkbox', 'radio', 'textbox', 'searchbox', 'combobox', 'listbox', 'heading',
    'tab', 'menuitem', 'option', 'switch', 'slider', 'spinbutton', 'img',
]);
const NAME_FROM_CONTENT = new Set(['button', 'link', 'heading', 'tab', 'menuitem', 'option', 'switch', 'checkbox', 'radio']);

export function implicitRole(el: HtmlElement): string | null {
    const explicit = (el.attrs.get('role') || '').trim().split(/\s+/)[0];
    if (explicit) return explicit;
    const t = el.tag;
    if (t === 'a') return el.attrs.has('href') ? 'link' : null;
    if (t === 'button') return 'button';
    if (t === 'input') {
        const type = (el.attrs.get('type') || '').toLowerCase();
        return type in INPUT_ROLES ? INPUT_ROLES[type] : null;
    }
    if (t === 'textarea') return 'textbox';
    if (t === 'select') return el.attrs.has('multiple') ? 'listbox' : 'combobox';
    if (/^h[1-6]$/.test(t)) return 'heading';
    if (t === 'img') return (el.attrs.get('alt') || '').trim() ? 'img' : null;
    if (t === 'option') return 'option';
    return null;
}

/**
 * Element text as Playwright's text engine sees it: an input button is
 * matched by its value, not its (empty) content. Twin of `text_of` — without
 * this the TS twin never offered `text="Continue"` for
 * `<input type=submit value=Continue>` while the Python twin did.
 */
export function textOf(el: HtmlElement): string {
    if (el.tag === 'input' && ['submit', 'button', 'reset'].includes((el.attrs.get('type') || '').toLowerCase())) {
        return normalizeSpace(el.attrs.get('value') || '');
    }
    return normalizeSpace(textContent(el));
}

function byId(root: HtmlElement, id: string): HtmlElement | null {
    for (const el of walk(root)) if (el.attrs.get('id') === id) return el;
    return null;
}

/** A simplified accessible name: aria-labelledby, aria-label, <label>, content, title/placeholder/alt. */
export function accessibleName(el: HtmlElement, root: HtmlElement): string {
    const labelledby = el.attrs.get('aria-labelledby');
    if (labelledby) {
        const parts = labelledby.split(/\s+/).map(id => byId(root, id)).filter(Boolean) as HtmlElement[];
        const name = normalizeSpace(parts.map(textOf).join(' '));
        if (name) return name;
    }
    const aria = normalizeSpace(el.attrs.get('aria-label') || '');
    if (aria) return aria;
    const role = implicitRole(el) || '';
    if (['input', 'textarea', 'select'].includes(el.tag)) {
        const type = (el.attrs.get('type') || '').toLowerCase();
        if (el.tag === 'input' && ['submit', 'button', 'reset'].includes(type)) {
            return normalizeSpace(el.attrs.get('value') || '');
        }
        const id = el.attrs.get('id');
        if (id) {
            for (const l of walk(root)) {
                if (l.tag === 'label' && l.attrs.get('for') === id) return textOf(l);
            }
        }
        for (let p = el.parent; p; p = p.parent) {
            if (p.tag === 'label') return textOf(p);
        }
        return normalizeSpace(el.attrs.get('title') || el.attrs.get('placeholder') || '');
    }
    if (el.tag === 'img') return normalizeSpace(el.attrs.get('alt') || '');
    if (NAME_FROM_CONTENT.has(role)) {
        const t = textOf(el);
        if (t) return t;
    }
    return normalizeSpace(el.attrs.get('title') || '');
}

/** ids from frameworks or with long digit/hex runs — they change per build or session. */
export function looksGenerated(value: string): boolean {
    return /\d{3,}|[0-9a-f]{8,}|^(ember|react|radix|mui|headlessui|rc-|:r)|:/i.test(value);
}

const CSS_IDENT = /^-?[A-Za-z_][A-Za-z0-9_-]*$/;
export const quoteValue = (v: string) => `"${v.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;

function elementFeatures(el: HtmlElement, root: HtmlElement): string[] {
    const own = textOf(el);
    return [
        ...tokens(el.tag),
        ...tokens(el.attrs.get('id')),
        ...tokens(el.attrs.get('class')),
        ...TEST_ID_ATTRS.flatMap(a => tokens(el.attrs.get(a))),
        ...['name', 'placeholder', 'aria-label', 'title', 'alt', 'type', 'href'].flatMap(a => tokens(el.attrs.get(a))),
        ...tokens(implicitRole(el)),
        ...(own.length <= 80 ? tokens(own) : []),
        ...tokens(accessibleName(el, root)),
    ];
}

const SKIP_TAGS = new Set(['html', 'head', 'body', 'script', 'style', 'noscript', 'template', 'title', 'meta', 'link', 'br', 'hr']);
const INTERACTIVE = new Set(['a', 'button', 'input', 'select', 'textarea', 'option', 'label']);

/** The few elements most like what the broken selector + intent describe. */
export function findTargets(root: HtmlElement, query: string[], limit = MAX_TARGETS): Array<{ el: HtmlElement; sim: number }> {
    if (!query.length) return [];
    const scored: Array<{ el: HtmlElement; sim: number; key: number }> = [];
    for (const el of walk(root)) {
        if (SKIP_TAGS.has(el.tag)) continue;
        const sim = similarity(query, elementFeatures(el, root));
        if (sim < MIN_TARGET_SIMILARITY) continue;
        // Ties go to interactive elements, then to the deeper (more specific) one.
        let depth = 0;
        for (let p = el.parent; p; p = p.parent) depth++;
        scored.push({ el, sim, key: sim + (INTERACTIVE.has(el.tag) || el.attrs.has('role') ? 0.05 : 0) + depth / 10000 });
    }
    scored.sort((a, b) => b.key - a.key);
    const out: Array<{ el: HtmlElement; sim: number }> = [];
    for (const s of scored) {
        // A wrapper of an already chosen element adds nothing.
        if (out.some(o => contains(s.el, o.el) || contains(o.el, s.el))) continue;
        out.push({ el: s.el, sim: s.sim });
        if (out.length >= limit) break;
    }
    return out;
}

function contains(outer: HtmlElement, inner: HtmlElement): boolean {
    for (let p = inner.parent; p; p = p.parent) if (p === outer) return true;
    return false;
}

// --------------------------------------------------------------------------
// Strategies — each returns zero or more selectors for one element
// --------------------------------------------------------------------------

export function testIdStrategy(el: HtmlElement): string[] {
    return TEST_ID_ATTRS.filter(a => (el.attrs.get(a) || '').trim())
        .map(a => `[${a}=${quoteValue(el.attrs.get(a)!.trim())}]`);
}

export function idStrategy(el: HtmlElement): Array<{ selector: string; generated: boolean }> {
    const id = (el.attrs.get('id') || '').trim();
    if (!id) return [];
    return [{ selector: CSS_IDENT.test(id) ? `#${id}` : `[id=${quoteValue(id)}]`, generated: looksGenerated(id) }];
}

export function roleStrategy(el: HtmlElement, root: HtmlElement): string[] {
    const role = implicitRole(el);
    if (!role || !ROLE_CANDIDATE_ROLES.has(role)) return [];
    const name = accessibleName(el, root);
    if (!name || name.length > 60) return [];
    return [`role=${role}[name=${quoteValue(name)}]`];
}

export function labelStrategy(el: HtmlElement): string[] {
    const out: string[] = [];
    const aria = normalizeSpace(el.attrs.get('aria-label') || '');
    if (aria && aria.length <= 60) out.push(`[aria-label=${quoteValue(aria)}]`);
    const ph = normalizeSpace(el.attrs.get('placeholder') || '');
    if (ph && ph.length <= 60) out.push(`[placeholder=${quoteValue(ph)}]`);
    const name = (el.attrs.get('name') || '').trim();
    if (name && name.length <= 60 && ['input', 'select', 'textarea', 'button'].includes(el.tag)) {
        out.push(`${el.tag}[name=${quoteValue(name)}]`);
    }
    return out;
}

export function textStrategy(el: HtmlElement): string[] {
    const text = textOf(el);
    if (!text || text.length > 50 || text.includes('[REDACTED')) return [];
    // Playwright's text engine picks the innermost element with that text.
    const childHasSame = el.children.some(c => typeof c !== 'string' && textOf(c) === text);
    if (childHasSame) return [];
    return [`text=${quoteValue(text)}`];
}

function stableClasses(el: HtmlElement): string[] {
    return (el.attrs.get('class') || '').split(/\s+/)
        .filter(c => c && CSS_IDENT.test(c) && !looksGenerated(c) && !/\d/.test(c))
        .slice(0, 2);
}

function pathSegment(el: HtmlElement): { seg: string; anchored: boolean } {
    const id = (el.attrs.get('id') || '').trim();
    if (id && CSS_IDENT.test(id) && !looksGenerated(id)) return { seg: `${el.tag}#${id}`, anchored: true };
    let seg = el.tag + stableClasses(el).map(c => `.${c}`).join('');
    if (el.tag === 'input' || el.tag === 'button') {
        const type = (el.attrs.get('type') || '').trim();
        if (type && CSS_IDENT.test(type)) seg += `[type=${quoteValue(type)}]`;
    }
    return { seg, anchored: false };
}

/** Up to four `>`-joined segments, stopping at a stable id; never nth-child. */
export function cssPathStrategy(el: HtmlElement): string[] {
    const segs: string[] = [];
    for (let cur: HtmlElement | null = el; cur && cur.tag !== '#root' && segs.length < 4; cur = cur.parent) {
        if (cur.tag === 'html' || cur.tag === 'body') break;
        const { seg, anchored } = pathSegment(cur);
        segs.unshift(seg);
        if (anchored) break;
    }
    if (segs.length < 2 && !segs[0]?.includes('#') && !segs[0]?.includes('.') && !segs[0]?.includes('[')) return [];
    return segs.length ? [segs.join(' > ')] : [];
}

/** Rough stability of a selector by its form, for the LLM's suggestion. */
export function classifySelector(selector: string): number {
    const s = selector.trim();
    if (TEST_ID_ATTRS.some(a => s.includes(a)) || /^data-test(id)?=/.test(s)) return STABILITY['test-id'];
    if (/^#[\w-]+$/.test(s) || /^id=/.test(s)) return looksGenerated(s) ? GENERATED_ID_STABILITY : STABILITY.id;
    if (/^(internal:)?role=/.test(s)) return STABILITY.role;
    if (/\[(aria-label|placeholder|name)\s*=/.test(s)) return STABILITY.label;
    if (/^text=|:has-text\(|:text(-is)?\(/.test(s)) return STABILITY.text;
    return 0.4;
}

/** Deterministic drafts for the DOM, best targets first. */
export function deterministicCandidates(html: string, oldSelector: string, intent?: string | null): DraftCandidate[] {
    const root = buildTree(html);
    const query = [...tokens(oldSelector), ...tokens(intent)];
    const drafts: DraftCandidate[] = [];
    findTargets(root, query).forEach(({ el, sim }, target) => {
        for (const sel of testIdStrategy(el)) drafts.push({ selector: sel, strategy: 'test-id', stability: STABILITY['test-id'], similarity: sim, target });
        for (const { selector, generated } of idStrategy(el)) {
            drafts.push({ selector, strategy: 'id', stability: generated ? GENERATED_ID_STABILITY : STABILITY.id, similarity: sim, target });
        }
        for (const sel of roleStrategy(el, root)) drafts.push({ selector: sel, strategy: 'role', stability: STABILITY.role, similarity: sim, target });
        for (const sel of labelStrategy(el)) drafts.push({ selector: sel, strategy: 'label', stability: STABILITY.label, similarity: sim, target });
        for (const sel of textStrategy(el)) drafts.push({ selector: sel, strategy: 'text', stability: STABILITY.text, similarity: sim, target });
        for (const sel of cssPathStrategy(el)) drafts.push({ selector: sel, strategy: 'css-path', stability: STABILITY['css-path'], similarity: sim, target });
    });
    return drafts;
}

// --------------------------------------------------------------------------
// Scoring and ranking
// --------------------------------------------------------------------------

const round3 = (n: number) => Math.round(n * 1000) / 1000;

export function scoreCandidate(b: Breakdown): number {
    return round3(WEIGHTS.stability * b.stability + WEIGHTS.similarity * b.similarity
        + WEIGHTS.llm * b.llm + WEIGHTS.uniqueness * b.uniqueness);
}

/** Applicable first, then score, then stability, then selector (stable order). */
export function compareCandidates(a: HealCandidate, b: HealCandidate): number {
    if (a.applicable !== b.applicable) return a.applicable ? -1 : 1;
    if (b.score !== a.score) return b.score - a.score;
    if (b.breakdown.stability !== a.breakdown.stability) return b.breakdown.stability - a.breakdown.stability;
    return a.selector < b.selector ? -1 : a.selector > b.selector ? 1 : 0;
}

export interface RankInput {
    /** Redacted DOM of the page at failure time. */
    html: string;
    oldSelector: string;
    intent?: string | null;
    llmSelector?: string | null;
    llmConfidence?: number | null;
    /** Matches of a selector in the live page (0 on error). */
    count: (selector: string) => Promise<number>;
    /** Whether two uniquely-matching selectors point at the same element. */
    sameElement?: (a: string, b: string) => Promise<boolean>;
}

export async function rankHealCandidates(input: RankInput): Promise<HealCandidate[]> {
    const old = (input.oldSelector || '').trim();
    const llmSel = (input.llmSelector || '').trim();
    const drafts = deterministicCandidates(input.html, old, input.intent);
    const query = [...tokens(old), ...tokens(input.intent)];

    const bySelector = new Map<string, DraftCandidate & { fromLlm: boolean }>();
    for (const d of drafts) {
        if (d.selector === old || bySelector.has(d.selector)) continue;
        bySelector.set(d.selector, { ...d, fromLlm: false });
    }
    if (llmSel && llmSel !== old) {
        const existing = bySelector.get(llmSel);
        if (existing) existing.fromLlm = true;
        else bySelector.set(llmSel, {
            selector: llmSel, strategy: 'llm', stability: classifySelector(llmSel),
            similarity: similarity(query, tokens(llmSel)), fromLlm: true,
        });
    }

    const entries = Array.from(bySelector.values()).slice(0, MAX_CANDIDATES);
    const counts = await Promise.all(entries.map(e => input.count(e.selector).catch(() => 0)));
    const llmConf = input.llmConfidence ?? DEFAULT_LLM_CONFIDENCE;
    const llmUnique = llmSel && bySelector.has(llmSel) ? counts[entries.findIndex(e => e.selector === llmSel)] === 1 : false;

    const out: HealCandidate[] = [];
    for (let i = 0; i < entries.length; i++) {
        const e = entries[i];
        const matches = counts[i] || 0;
        let llm = 0;
        if (e.fromLlm) llm = llmConf;
        else if (llmUnique && matches === 1 && input.sameElement) {
            llm = (await input.sameElement(e.selector, llmSel).catch(() => false)) ? llmConf : 0;
        }
        const breakdown: Breakdown = {
            stability: round3(e.stability), similarity: round3(e.similarity), llm: round3(llm),
            uniqueness: matches === 1 ? 1 : 0,
        };
        out.push({
            selector: e.selector,
            strategy: e.strategy,
            source: e.fromLlm ? (e.strategy === 'llm' ? 'llm' : 'both') : 'deterministic',
            matches,
            applicable: matches === 1,
            score: scoreCandidate(breakdown),
            breakdown,
        });
    }
    return out.sort(compareCandidates);
}

/** The candidate to apply: the best one that matches exactly one element. */
export function chooseCandidate(ranked: HealCandidate[]): HealCandidate | null {
    return ranked.find(c => c.applicable) || null;
}
