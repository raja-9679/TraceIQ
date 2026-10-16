import type { Tone } from '@/components/redesign/kinds';
import type { Control, ControlStatus, SourceStatus } from '@/api/compliance';

// Pure helpers for the compliance pages (WP13a). Kept out of the component
// files so those export only components (fast-refresh lint).

export const STATUS_META: Record<ControlStatus, { label: string; tone: Tone }> = {
    passing: { label: 'Passing', tone: 'mint' },
    failing: { label: 'Failing', tone: 'peach' },
    stale: { label: 'Stale', tone: 'butter' },
    excepted: { label: 'Excepted', tone: 'lavender' },
    not_assessed: { label: 'Not assessed', tone: 'neutral' },
};

export const STATUS_ORDER: ControlStatus[] = ['failing', 'stale', 'not_assessed', 'excepted', 'passing'];

export const SOURCE_META: Record<SourceStatus, { label: string; tone: Tone }> = {
    pass: { label: 'Pass', tone: 'mint' },
    fail: { label: 'Fail', tone: 'peach' },
    unknown: { label: 'Unknown', tone: 'neutral' },
};

export const EXCEPTION_TONE: Record<string, Tone> = {
    pending: 'butter', approved: 'lavender', rejected: 'neutral', revoked: 'neutral', expired: 'neutral',
};

export interface ControlFilters { framework: string; status: string; owner: string }

export const NO_FILTERS: ControlFilters = { framework: '', status: '', owner: '' };

/** owner: '' = anyone, 'none' = unowned, otherwise a user id. */
export function filterControls(controls: Control[], f: ControlFilters): Control[] {
    return controls.filter((c) =>
        (!f.framework || c.framework === f.framework)
        && (!f.status || c.status === f.status)
        && (!f.owner || (f.owner === 'none' ? c.owner_id === null : String(c.owner_id) === f.owner)));
}

/** Failing first, then stale, so the list reads as a to-do list. */
export function sortControls(controls: Control[]): Control[] {
    const rank = (s: ControlStatus) => STATUS_ORDER.indexOf(s);
    return [...controls].sort((a, b) => rank(a.status) - rank(b.status)
        || a.framework.localeCompare(b.framework) || a.reference.localeCompare(b.reference, undefined, { numeric: true })
        || a.id - b.id);
}

export function fmtDate(value: string | null | undefined): string {
    if (!value) return '—';
    const d = new Date(value.endsWith('Z') || value.includes('+') ? value : `${value}Z`);
    return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}

export function fmtDay(value: string | null | undefined): string {
    if (!value) return '—';
    const d = new Date(value.endsWith('Z') || value.includes('+') ? value : `${value}Z`);
    return Number.isNaN(d.getTime()) ? value : d.toISOString().slice(0, 10);
}

export function fmtBytes(n: number | null | undefined): string {
    if (n === null || n === undefined) return '—';
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** yyyy-mm-dd (a date input's value) → the start / end of that UTC day. */
export function dayStart(day: string): string { return `${day}T00:00:00Z`; }
export function dayEnd(day: string): string { return `${day}T23:59:59Z`; }

export function isoDay(d: Date): string { return d.toISOString().slice(0, 10); }

/** Default evidence-pack period: the last 90 days up to today. */
export function defaultPeriod(now: Date = new Date()): { start: string; end: string } {
    const start = new Date(now.getTime() - 90 * 86400_000);
    return { start: isoDay(start), end: isoDay(now) };
}

/** The earliest expiry worth asking for: tomorrow. */
export function minExceptionDay(now: Date = new Date()): string {
    return isoDay(new Date(now.getTime() + 86400_000));
}

/** The furthest expiry an exception may ask for (365 days, server-enforced). */
export function maxExceptionDay(now: Date = new Date()): string {
    return isoDay(new Date(now.getTime() + 364 * 86400_000));
}

const HISTORY_LABELS: Record<string, string> = {
    create: 'Created', update: 'Edited', delete: 'Deleted',
    evidence_add: 'Evidence added', evidence_delete: 'Evidence removed', evidence_download: 'Evidence downloaded',
    exception_request: 'Exception requested', exception_approve: 'Exception approved',
    exception_reject: 'Exception rejected', exception_revoke: 'Exception revoked',
};

export function historyLabel(action: string): string {
    return HISTORY_LABELS[action] ?? action.replace(/_/g, ' ');
}

/** Human description of a source's parameters, e.g. "days: 90". */
export function paramsText(params: Record<string, unknown>): string {
    return Object.entries(params).map(([k, v]) => `${k.replace(/_/g, ' ')}: ${Array.isArray(v) ? v.join(', ') : String(v)}`).join(' · ');
}
