import type { Tone } from '@/components/redesign/kinds';
import type { DigestResult, PortfolioRow, ShareLink, Verdict } from '@/api/reports';

export const fmtPct = (v: number | null | undefined) => (v == null ? '—' : `${v}%`);

/** "+3.1 pts", "-2", "no change"; "—" when there is nothing to compare with. */
export function fmtDelta(v: number | null | undefined, unit = ''): string {
    if (v == null) return '—';
    if (v === 0) return 'no change';
    return `${v > 0 ? '+' : ''}${v}${unit}`;
}

/** Colour of a change. For failures, going up is bad. */
export function deltaTone(v: number | null | undefined, upIsGood = true): Tone {
    if (v == null || v === 0) return 'neutral';
    return (v > 0) === upIsGood ? 'mint' : 'peach';
}

export const VERDICTS: Record<Verdict, { label: string; tone: Tone }> = {
    ready: { label: 'Ready', tone: 'mint' },
    not_ready: { label: 'Not ready', tone: 'peach' },
    insufficient_data: { label: 'Not enough data', tone: 'butter' },
};

export const verdictOf = (v: Verdict | null | undefined) =>
    v ? VERDICTS[v] : { label: 'Unknown', tone: 'neutral' as Tone };

export type PortfolioKey =
    | 'project_name' | 'pass_rate_7d' | 'pass_rate_30d' | 'runs_7d' | 'last_run'
    | 'verdict' | 'flaky_count' | 'open_findings';

const VERDICT_ORDER: Record<string, number> = { not_ready: 0, insufficient_data: 1, ready: 2 };

function sortValue(r: PortfolioRow, key: PortfolioKey): string | number | null {
    switch (key) {
        case 'project_name': return r.project_name.toLowerCase();
        case 'last_run': return r.last_run?.created_at ?? null;
        case 'verdict': return r.verdict ? VERDICT_ORDER[r.verdict] : null;
        default: return r[key];
    }
}

/** Sorted copy; rows without a value always sink to the bottom. */
export function sortPortfolio(rows: PortfolioRow[], key: PortfolioKey, dir: 'asc' | 'desc'): PortfolioRow[] {
    const sign = dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
        const x = sortValue(a, key), y = sortValue(b, key);
        if (x == null && y == null) return a.project_name.localeCompare(b.project_name);
        if (x == null) return 1;
        if (y == null) return -1;
        if (x < y) return -sign;
        if (x > y) return sign;
        return a.project_name.localeCompare(b.project_name);
    });
}

export const EXPIRY_OPTIONS = [1, 7, 30, 90] as const;

export const SHARE_KIND_LABELS: Record<ShareLink['kind'], string> = {
    run_report: 'Run report',
    project_summary: 'Project summary',
    workspace_summary: 'Workspace summary',
    poc_report: 'PoC report',
    trust_document: 'Trust document',
};

export const SHARE_STATE: Record<ShareLink['state'], { label: string; tone: Tone }> = {
    active: { label: 'Active', tone: 'mint' },
    expired: { label: 'Expired', tone: 'neutral' },
    revoked: { label: 'Revoked', tone: 'neutral' },
};

/** Absolute URL for a share path, from the app's own origin. */
export const shareUrl = (path: string, origin: string = window.location.origin) =>
    `${origin.replace(/\/$/, '')}${path}`;

const SKIP_LABELS: Record<string, string> = {
    no_access: 'no longer have access',
    inactive: 'inactive accounts',
    opted_out: 'turned this project off',
    invalid_address: 'invalid addresses',
};

/** One line about the last delivery, for the schedule list. */
export function describeDigestResult(r: DigestResult | null | undefined): string | null {
    if (!r || !r.at) return null;
    const parts: string[] = [];
    if (r.channels_sent?.length) parts.push(`sent to ${r.channels_sent.join(', ')}`);
    if (r.emailed) parts.push(`${r.emailed} email${r.emailed === 1 ? '' : 's'}`);
    for (const [k, n] of Object.entries(r.skipped || {})) {
        if (n) parts.push(`${n} skipped (${SKIP_LABELS[k] ?? k})`);
    }
    parts.push(...(r.errors || []));
    return parts.length ? parts.join(' · ') : 'nothing was sent';
}
