import type { CellExpect, MatrixRow, MatrixRun, MatrixRunSummary, SwapSpec, Verdict } from '@/api/accessMatrix';
import type { SecuritySettings } from '@/api/security';

// Pure logic for the access-matrix editor and results. Mirrors the rules in
// backend/app/services/access_matrix.py so the page can explain a refusal
// before the user clicks Run — the backend stays the authority.

/** Absent and skip are the same to the backend (neither executes). */
export function nextExpect(current: CellExpect | undefined): CellExpect {
    if (current === 'allow') return 'deny';
    if (current === 'deny') return 'skip';
    return 'allow';
}

/** Cycle one cell. A status means different things for allow (exact) and
 * deny (extra deny status), so it never survives a change of expectation. */
export function cycleCell(row: MatrixRow, col: string): MatrixRow {
    const next = nextExpect(row.cells[col]?.expect);
    const cells = { ...row.cells };
    if (next === 'skip') delete cells[col];
    else cells[col] = { expect: next };
    return { ...row, cells };
}

export function parseStatus(text: string): number | null {
    const n = parseInt(text.trim(), 10);
    return Number.isFinite(n) && n >= 100 && n <= 599 ? n : null;
}

export function setCellStatus(row: MatrixRow, col: string, text: string): MatrixRow {
    const cell = row.cells[col];
    if (!cell || cell.expect === 'skip') return row;
    return { ...row, cells: { ...row.cells, [col]: { ...cell, status: parseStatus(text) } } };
}

export const EXPECT_LABEL: Record<CellExpect, string> = {
    allow: 'Allow', deny: 'Deny', skip: 'Skip',
};

export const VERDICT_LABEL: Record<Verdict, string> = {
    pass: 'Pass', fail: 'Fail', inconclusive: 'Inconclusive', pending: 'Pending',
};

/** What a failed cell means: a granted DENY is a security finding, a refused
 * ALLOW is a regression — the backend counts them apart and so do we. */
export function failureKind(expect: 'allow' | 'deny', verdict: Verdict): string | null {
    if (verdict !== 'fail') return null;
    return expect === 'deny' ? 'access granted' : 'allow regression';
}

export function runIsActive(run: Pick<MatrixRun, 'status'> | undefined | null): boolean {
    return !!run && (run.status === 'pending' || run.status === 'running');
}

export function summaryParts(s: MatrixRunSummary | null | undefined): { verdict: Verdict; count: number }[] {
    if (!s) return [];
    return (['pass', 'fail', 'inconclusive', 'pending'] as Verdict[])
        .map(verdict => ({ verdict, count: s[verdict] ?? 0 }))
        .filter(p => p.count > 0);
}

export function rowTargetText(row: MatrixRow, caseName?: string): string {
    if (row.kind === 'case') return caseName ?? (row.test_case_id ? `Case #${row.test_case_id}` : 'No case chosen');
    const base = `${row.kind === 'page' ? 'GET' : row.method || 'GET'} ${row.url || '(no url)'}`;
    return row.kind === 'page' && row.selector ? `${base} [${row.selector}]` : base;
}

export function defaultSwap(ownerPersonaId: number): SwapSpec {
    return { owner_persona_id: ownerPersonaId, method: 'GET', url: '', extractor: { source: 'json', expr: 'id' }, placeholder: '{id}' };
}

export function bodyText(body: unknown): string {
    if (body === undefined || body === null) return '';
    return typeof body === 'string' ? body : JSON.stringify(body, null, 2);
}

/** JSON when it parses, the raw text otherwise, undefined when empty. */
export function parseBody(text: string): unknown {
    const t = text.trim();
    if (!t) return undefined;
    try { return JSON.parse(t); } catch { return text; }
}

const SAFE = new Set(['GET', 'HEAD', 'OPTIONS']);

export function isMutating(method?: string): boolean {
    return !SAFE.has((method || 'GET').toUpperCase());
}

export function resolveUrl(url: string, baseUrl?: string | null): string | null {
    const u = (url || '').trim();
    if (u.startsWith('/')) return baseUrl ? baseUrl.replace(/\/+$/, '') + u : null;
    return u;
}

/** Same rule as access_matrix.host_allowed: exact host or a subdomain. */
export function hostAllowed(url: string, domains: string[]): boolean {
    let host = '';
    try { host = new URL(url).hostname.toLowerCase(); } catch { return false; }
    if (!host) return false;
    return domains.some(d => {
        const dd = (d || '').trim().toLowerCase();
        return !!dd && (host === dd || host.endsWith('.' + dd));
    });
}

export interface Prerequisite { ok: boolean; text: string }

/** Preview of the backend's run-time safety gates for this draft. */
export function prerequisites(rows: MatrixRow[], baseUrl: string | null | undefined,
    sec: SecuritySettings | undefined, columns: string[]): Prerequisite[] {
    const live = rows.filter(r => columns.some(c => r.cells[c] && r.cells[c].expect !== 'skip'));
    const network = live.filter(r => r.kind !== 'case');
    if (!network.length) return [];
    const out: Prerequisite[] = [];
    const enabled = !!sec?.enabled;
    out.push({
        ok: enabled,
        text: enabled ? 'Security testing is enabled for this project'
            : 'Security testing is disabled for this project — request/page rows will be refused',
    });
    const domains = sec?.allowed_domains ?? [];
    const blocked = new Set<string>();
    for (const r of network) {
        const urls = [r.url || ''];
        if (r.swap) urls.push(r.swap.url);
        for (const raw of urls) {
            const full = resolveUrl(raw, baseUrl);
            if (full === null) blocked.add(`${raw} (relative url needs a base URL)`);
            else if (!hostAllowed(full, domains)) blocked.add(full);
        }
    }
    out.push(blocked.size
        ? { ok: false, text: `Not on the authorized domain list: ${[...blocked].join(', ')}` }
        : { ok: true, text: `All targets are on the authorized domain list (${domains.join(', ') || 'none'})` });
    const mutating = network.filter(r => isMutating(r.method) || (r.swap && isMutating(r.swap.method)));
    if (mutating.length) {
        out.push({
            ok: false,
            text: `${mutating.length} row(s) send mutating requests — tick "I am authorized" when running`,
        });
    }
    return out;
}

export function needsAttestation(rows: MatrixRow[]): boolean {
    return rows.some(r => r.kind !== 'case' && (isMutating(r.method) || (!!r.swap && isMutating(r.swap.method))));
}

/** The backend's refusal text, verbatim. FastAPI 422s carry a list of errors. */
export function errorDetail(e: unknown): string {
    const err = e as { response?: { data?: { detail?: unknown } }; message?: string };
    const d = err?.response?.data?.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map(x => (x && typeof x === 'object' && 'msg' in x ? String((x as { msg: unknown }).msg) : String(x))).join('; ');
    return err?.message || 'Unknown error';
}
