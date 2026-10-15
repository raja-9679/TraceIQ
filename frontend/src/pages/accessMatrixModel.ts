import type { AccessMatrix, AccessMatrixInput, CellExpect, MatrixCellResult, MatrixRow, Verdict } from '@/api/accessMatrix';

export const ANONYMOUS = 'anonymous';

export const VERDICT_TONE: Record<Verdict, string> = {
    pass: 'bg-emerald-50 text-emerald-800 border-emerald-200',
    fail: 'bg-rose-50 text-rose-800 border-rose-300',
    inconclusive: 'bg-amber-50 text-amber-800 border-amber-200',
    pending: 'bg-slate-50 text-slate-500 border-slate-200',
};

export const EXPECT_TONE: Record<CellExpect, string> = {
    allow: 'bg-emerald-50 text-emerald-700',
    deny: 'bg-rose-50 text-rose-700',
    skip: 'bg-slate-50 text-slate-400',
};

export function columnKeys(personaIds: number[], includeAnonymous: boolean): string[] {
    const keys = personaIds.map(String);
    if (includeAnonymous) keys.push(ANONYMOUS);
    return keys;
}

export function newRowKey(rows: MatrixRow[]): string {
    let i = rows.length + 1;
    const taken = new Set(rows.map(r => r.key));
    while (taken.has(`row-${i}`)) i++;
    return `row-${i}`;
}

export function blankRow(rows: MatrixRow[]): MatrixRow {
    return { key: newRowKey(rows), label: '', kind: 'request', method: 'GET', url: '', cells: {} };
}

export function emptyDraft(): AccessMatrixInput {
    return { name: '', description: '', base_url: '', persona_ids: [], include_anonymous: false, rows: [], deny_statuses: null };
}

export function draftFrom(m: AccessMatrix): AccessMatrixInput {
    return {
        name: m.name, description: m.description ?? '', base_url: m.base_url ?? '',
        persona_ids: [...m.persona_ids], include_anonymous: m.include_anonymous,
        rows: JSON.parse(JSON.stringify(m.rows || [])), deny_statuses: m.deny_statuses,
    };
}

/** Drop empty strings / cells for removed columns before saving. */
export function cleanDraft(d: AccessMatrixInput): AccessMatrixInput {
    const cols = new Set(columnKeys(d.persona_ids, d.include_anonymous));
    return {
        ...d,
        base_url: d.base_url?.trim() || null,
        description: d.description?.trim() || null,
        rows: d.rows.map(r => {
            const cells: MatrixRow['cells'] = {};
            for (const [k, c] of Object.entries(r.cells || {})) if (cols.has(k)) cells[k] = c;
            const out: MatrixRow = { ...r, cells };
            if (r.kind === 'case') { delete out.url; delete out.selector; delete out.swap; out.method = 'GET'; }
            else { delete out.test_case_id; }
            if (r.kind !== 'page') delete out.selector;
            if (r.kind === 'page') out.method = 'GET';
            if (!out.selector) delete out.selector;
            return out;
        }),
    };
}

export function parseStatuses(text: string): number[] | null {
    const vals = text.split(',').map(s => parseInt(s.trim(), 10)).filter(n => Number.isFinite(n));
    return vals.length ? vals : null;
}

export function cellIndex(cells: MatrixCellResult[] | undefined): Map<string, MatrixCellResult> {
    const m = new Map<string, MatrixCellResult>();
    for (const c of cells || []) m.set(`${c.row_key}|${c.persona_key}`, c);
    return m;
}

export function columnLabel(key: string, names: Record<string, string>): string {
    return key === ANONYMOUS ? 'Anonymous' : (names[key] ?? `Persona #${key}`);
}

export function expectedText(c: Pick<MatrixCellResult, 'expect' | 'expected_status'>): string {
    return c.expected_status ? `${c.expect} (${c.expected_status})` : c.expect;
}

export function observedText(c: MatrixCellResult): string {
    const o = c.observed;
    if (!o) return c.verdict === 'pending' ? 'waiting…' : '—';
    if (o.error_kind) return o.error_kind;
    if (o.run_status) return `run ${o.run_status}`;
    const parts: string[] = [];
    if (o.status !== undefined) parts.push(String(o.status));
    if (o.element_present === true) parts.push('element visible');
    if (o.element_present === false) parts.push('element absent');
    return parts.join(' · ') || '—';
}
