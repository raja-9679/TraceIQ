import type { RlsStatus } from '@/api/rls';

export function mechanismLabel(kind: RlsStatus['mechanism'], role: string | null): string {
    switch (kind) {
        case 'role': return `requests switch into the restricted role ${role ?? ''}`.trim();
        case 'force': return 'FORCE ROW LEVEL SECURITY on the app user (restricted role unavailable)';
        case 'unavailable': return 'cannot enforce on this database';
        default: return 'not installed — run the database migrations';
    }
}

export function violationSummary(v: RlsStatus['violations']): string {
    const since = v.since ? ` since ${new Date(v.since * 1000).toLocaleString()}` : '';
    const where = v.source === 'process' ? ' (this API process only — Redis unavailable)' : '';
    if (v.total === 0) return `No out-of-scope rows seen by log mode${since}${where}.`;
    return `${v.total} out-of-scope row(s) seen by log mode${since}${where} — fix or explain these before enforcing.`;
}
