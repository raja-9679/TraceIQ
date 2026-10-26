// Form helpers for the run-test step ("Run another test"). Mirrors the
// /api/step-types catalog entry and app/services/case_calls.py.

export const RUN_TEST = 'run-test';

export interface OverrideRow { key: string; value: string }

/** The case id a run-test step names, or null. */
export function calledCaseId(params: Record<string, unknown> | undefined): number | null {
    const raw = params?.case_id;
    const n = typeof raw === 'number' ? raw : typeof raw === 'string' ? parseInt(raw, 10) : NaN;
    return Number.isInteger(n) && n > 0 ? n : null;
}

/** `{{ env.X }}` or `env.X` → `env.X` (what the backend stores as the key). */
export function normaliseKey(key: string): string {
    const k = key.trim();
    return k.startsWith('{{') && k.endsWith('}}') ? k.slice(2, -2).trim() : k;
}

const KEY = /^(?:(?:env|secret|data)\.)?[A-Za-z0-9_-]{1,100}$/;

export const isValidKey = (key: string): boolean => KEY.test(normaliseKey(key));

export function overridesToRows(raw: unknown): OverrideRow[] {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return [];
    return Object.entries(raw as Record<string, unknown>).map(([key, value]) => ({ key, value: String(value ?? '') }));
}

/** Rows → the params.overrides object; blank keys are dropped, undefined when empty. */
export function rowsToOverrides(rows: OverrideRow[]): Record<string, string> | undefined {
    const out: Record<string, string> = {};
    for (const r of rows) {
        const k = normaliseKey(r.key);
        if (k) out[k] = r.value;
    }
    return Object.keys(out).length ? out : undefined;
}

/** `{{env.X}}`-style tokens used in a case's steps — offered as override keys. */
export function tokensIn(steps: unknown): string[] {
    const found = new Set<string>();
    const walk = (v: unknown) => {
        if (typeof v === 'string') {
            for (const m of v.matchAll(/\{\{\s*((?:env|secret|data)\.[A-Za-z0-9_-]+)\s*\}\}/g)) found.add(m[1]);
        } else if (Array.isArray(v)) v.forEach(walk);
        else if (v && typeof v === 'object') Object.values(v).forEach(walk);
    };
    walk(steps);
    return [...found].sort();
}
