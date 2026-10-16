/**
 * Element-level visual baselines (WP14e): the key that tells an element
 * baseline apart from the full-page baseline of the same step.
 *
 * Twin: backend `app/services/visual_review.py:element_key` — both sides must
 * produce the same key for the same selector, or a promoted element baseline
 * is never found again. Shared vectors live in both test files.
 *
 * Normalisation is deliberately small: whitespace runs collapse to one space
 * and the spaces around `>` and `,` go, but only OUTSIDE quotes — text inside
 * `:has-text("a  b")` or `[title="x > y"]` is part of what the selector
 * matches and must not change.
 */
import { createHash } from 'crypto';

export const FULL_PAGE_KEY = '';
export const ELEMENT_KEY_RE = /^el-[0-9a-f]{12}$/;

export function normaliseSelector(selector: string): string {
    const src = String(selector ?? '').trim();
    let out = '';
    let quote: string | null = null;
    for (let i = 0; i < src.length; i++) {
        const ch = src[i];
        if (quote) {
            out += ch;
            if (ch === '\\' && i + 1 < src.length) { out += src[++i]; continue; }
            if (ch === quote) quote = null;
            continue;
        }
        if (ch === '"' || ch === "'") { quote = ch; out += ch; continue; }
        if (/\s/.test(ch)) {
            // Collapse the run; drop it entirely next to a combinator/comma.
            while (i + 1 < src.length && /\s/.test(src[i + 1])) i++;
            const next = src[i + 1];
            const prev = out[out.length - 1];
            if (next === '>' || next === ',' || prev === '>' || prev === ',') continue;
            out += ' ';
            continue;
        }
        out += ch;
    }
    return out;
}

/** `el-` + 12 hex chars of sha256(normalised selector); '' for no selector. */
export function elementKey(selector: string | null | undefined): string {
    const norm = normaliseSelector(selector ?? '');
    if (!norm) return FULL_PAGE_KEY;
    return 'el-' + createHash('sha256').update(norm, 'utf8').digest('hex').slice(0, 12);
}
