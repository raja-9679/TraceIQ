// Wording for the notifications list and the result list's small labels.
import type { NotificationItem, ResultSummary } from '@/api/collaboration';

/** One sentence per notification; an unknown kind (a newer backend) still reads sensibly. */
export function notificationText(n: Pick<NotificationItem, 'kind' | 'actor' | 'test_name'>): string {
    const who = n.actor?.name || 'Someone';
    const test = `“${n.test_name}”`;
    switch (n.kind) {
        case 'assigned': return `${who} asked you to look at ${test}`;
        case 'comment': return `${who} commented on ${test}`;
        case 'assignment_done': return `${who} marked ${test} as done`;
        default: return `${who} updated ${test}`;
    }
}

/** Where a notification or an assignment leads: the run, opened on that result. */
export const resultLink = (runId: number, resultId: number): string => `/runs/${runId}?result=${resultId}`;

/** "Sam · 2 comments" under a test in the run's list; nothing when there is nothing to say. */
export function summaryLabel(s: ResultSummary | undefined): string | null {
    if (!s) return null;
    const parts: string[] = [];
    if (s.assignee) parts.push(s.status === 'done' ? `${s.assignee.name} (done)` : s.assignee.name);
    if (s.comments > 0) parts.push(`${s.comments} comment${s.comments === 1 ? '' : 's'}`);
    return parts.length ? parts.join(' · ') : null;
}

/** Shown on the bell: nothing at zero, "99+" beyond two digits. */
export function countLabel(unread: number): string | null {
    if (!unread || unread < 1) return null;
    return unread > 99 ? '99+' : String(unread);
}
