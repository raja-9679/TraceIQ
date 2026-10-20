import type { CaseOwnership, OwnershipPerson, ReviewStatus } from '@/api/caseOwnership';
import type { Tone } from '@/components/redesign/kinds';

// Non-component helpers for components/ownership/* and pages/TestReview.tsx.

export const REVIEW_LABEL: Record<ReviewStatus, { label: string; tone: Tone }> = {
    never: { label: 'Never reviewed', tone: 'lavender' },
    overdue: { label: 'Review overdue', tone: 'peach' },
    ok: { label: 'Reviewed', tone: 'mint' },
    not_required: { label: 'No review needed', tone: 'neutral' },
};

/** Backend datetimes are naive UTC. */
export function parseUtc(iso: string): Date {
    return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
}

export function shortDate(iso: string | null | undefined): string {
    if (!iso) return '—';
    return parseUtc(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

export function personLabel(p: OwnershipPerson | null | undefined): string {
    if (!p) return 'Unowned';
    const name = p.name || p.email || `User #${p.id}`;
    return p.state === 'no_access' ? `${name} (no longer has access)` : name;
}

/** One line for a case row: "Owner: Asha · Review overdue since 3 Oct 2026". */
export function reviewSummary(c: CaseOwnership, now: Date = new Date()): string {
    switch (c.review_status) {
        case 'not_required':
            return 'No periodic review for this module';
        case 'never':
            return `Never reviewed · every ${c.review_interval_days} days`;
        case 'overdue':
            return `Review overdue since ${shortDate(c.review_due_at)}`;
        default: {
            const due = c.review_due_at ? parseUtc(c.review_due_at) : null;
            const days = due ? Math.max(0, Math.ceil((due.getTime() - now.getTime()) / 86_400_000)) : null;
            return days === null ? 'Reviewed' : `Reviewed ${shortDate(c.last_reviewed_at)} · next in ${days} day${days === 1 ? '' : 's'}`;
        }
    }
}

export interface ReviewGroup {
    name: string;
    items: CaseOwnership[];
}

/** Needs-review list grouped for the page: owner gone first, then overdue, then never. */
export function groupForReview(items: CaseOwnership[]): ReviewGroup[] {
    const ownerGone = items.filter((c) => c.owner?.state === 'no_access');
    const rest = items.filter((c) => c.owner?.state !== 'no_access');
    return [
        { name: 'Owner no longer has access', items: ownerGone },
        { name: 'Review overdue', items: rest.filter((c) => c.review_status === 'overdue') },
        { name: 'Never reviewed', items: rest.filter((c) => c.review_status === 'never') },
        { name: 'Up to date', items: rest.filter((c) => c.review_status === 'ok' || c.review_status === 'not_required') },
    ].filter((g) => g.items.length > 0);
}

/** Suite setting text box → the value to store; null = inherit (key removed). */
export function intervalFromInput(text: string): number | null | 'invalid' {
    const t = text.trim();
    if (t === '') return null;
    if (!/^\d+$/.test(t)) return 'invalid';
    const n = Number(t);
    return n > 3650 ? 'invalid' : n;
}
