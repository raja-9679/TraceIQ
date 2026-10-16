import type { Tone } from '@/components/redesign/kinds';
import type { IncidentStatus, PostureState } from '@/api/trustCenter';

export const INCIDENT_STATUSES: IncidentStatus[] = ['investigating', 'identified', 'monitoring', 'resolved'];

export const INCIDENT_STATUS: Record<IncidentStatus, { label: string; tone: Tone }> = {
    investigating: { label: 'Investigating', tone: 'peach' },
    identified: { label: 'Identified', tone: 'butter' },
    monitoring: { label: 'Monitoring', tone: 'lavender' },
    resolved: { label: 'Resolved', tone: 'mint' },
};

export const POSTURE_TONE: Record<PostureState, Tone> = { good: 'mint', attention: 'butter', info: 'neutral' };

export const OVERALL: Record<string, { label: string; tone: Tone }> = {
    operational: { label: 'All systems operational', tone: 'mint' },
    degraded: { label: 'Partial outage', tone: 'butter' },
    down: { label: 'Major outage', tone: 'peach' },
    unknown: { label: 'No status data', tone: 'neutral' },
};

/** "a, b, c" -> unique trimmed names (max 20, 80 chars each, like the API). */
export function parseComponents(v: string): string[] {
    const out: string[] = [];
    for (const raw of v.split(',')) {
        const n = raw.trim().slice(0, 80);
        if (n && !out.includes(n)) out.push(n);
    }
    return out.slice(0, 20);
}

export function fmtBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** The document token lives in the URL fragment (#token), never the path or query. */
export const tokenFromHash = (hash: string) => {
    try {
        return decodeURIComponent(hash.replace(/^#/, '')).trim() || null;
    } catch {
        return null;
    }
};

export const ACCEPTED_DOCUMENTS = '.pdf,.png,.jpg,.jpeg,.docx,.xlsx,.pptx,.txt,.md';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';

/** Backend timestamps are naive UTC; mark them so the browser does not read them as local time. */
export const asUtc = (s: string) => (/[zZ]$|[+-]\d\d:\d\d$/.test(s) ? s : `${s}Z`);
