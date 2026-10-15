import type { FindingStatus } from '@/api/security';

// Mirrors MANUAL_TRANSITIONS in backend/app/services/finding_lifecycle.py.
// verified_fixed never appears as a target: only a rescan can verify a fix.
export const FINDING_TRANSITIONS: Record<FindingStatus, FindingStatus[]> = {
    open: ['in_progress', 'fixed_pending_verification', 'accepted_risk', 'false_positive'],
    in_progress: ['open', 'fixed_pending_verification', 'accepted_risk', 'false_positive'],
    fixed_pending_verification: ['open', 'in_progress'],
    verified_fixed: ['open'],
    accepted_risk: ['open', 'in_progress'],
    false_positive: ['open'],
};

export const FINDING_STATUS_LABEL: Record<FindingStatus, string> = {
    open: 'Open',
    in_progress: 'In progress',
    fixed_pending_verification: 'Fixed — awaiting retest',
    verified_fixed: 'Verified fixed',
    accepted_risk: 'Accepted risk',
    false_positive: 'False positive',
};

export const FINDING_STATUS_TONE: Record<FindingStatus, string> = {
    open: 'text-rose-600',
    in_progress: 'text-amber-600',
    fixed_pending_verification: 'text-sky-600',
    verified_fixed: 'text-emerald-600',
    accepted_risk: 'text-violet-600',
    false_positive: 'text-slate-400',
};

// Pre-WP7 rows and older clients used acknowledged/resolved.
export function normalizeStatus(s?: string | null): FindingStatus {
    if (s === 'acknowledged') return 'in_progress';
    if (s === 'resolved') return 'verified_fixed';
    return (s as FindingStatus) || 'open';
}

export function isClosed(s?: string | null): boolean {
    const n = normalizeStatus(s);
    return n === 'verified_fixed' || n === 'false_positive';
}

export function toDateInput(iso?: string | null): string {
    return iso ? iso.slice(0, 10) : '';
}

export function fromDateInput(value: string): string | null {
    return value ? `${value}T00:00:00` : null;
}
