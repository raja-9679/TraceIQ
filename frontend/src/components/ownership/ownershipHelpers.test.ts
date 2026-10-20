import { describe, expect, it } from 'vitest';
import type { CaseOwnership } from '@/api/caseOwnership';
import { groupForReview, intervalFromInput, personLabel, reviewSummary } from './ownershipHelpers';

const base: CaseOwnership = {
    case_id: 1, name: 'checkout', test_suite_id: 3, project_id: 9, priority: null,
    owner: null, backup_owner: null, last_reviewed_at: null, last_reviewed_by: null,
    review_interval_days: 30, review_status: 'never', review_due_at: null,
};

describe('ownership helpers', () => {
    it('labels owners, flagging one who lost access', () => {
        expect(personLabel(null)).toBe('Unowned');
        expect(personLabel({ id: 2, name: 'Asha', email: 'a@x', state: 'ok' })).toBe('Asha');
        expect(personLabel({ id: 2, name: null, email: 'a@x', state: 'no_access' })).toBe('a@x (no longer has access)');
    });

    it('summarises each review status', () => {
        expect(reviewSummary(base)).toBe('Never reviewed · every 30 days');
        expect(reviewSummary({ ...base, review_status: 'not_required' })).toMatch(/No periodic review/);
        expect(reviewSummary({ ...base, review_status: 'overdue', review_due_at: '2026-10-01T00:00:00' })).toMatch(/^Review overdue since/);
        const ok = { ...base, review_status: 'ok' as const, last_reviewed_at: '2026-10-10T00:00:00', review_due_at: '2026-11-09T00:00:00' };
        expect(reviewSummary(ok, new Date('2026-10-20T00:00:00Z'))).toMatch(/next in 20 days$/);
    });

    it('groups the list: owner gone, overdue, never, up to date', () => {
        const gone = { ...base, case_id: 2, review_status: 'ok' as const, owner: { id: 5, name: 'B', email: null, state: 'no_access' as const } };
        const overdue = { ...base, case_id: 3, review_status: 'overdue' as const };
        const ok = { ...base, case_id: 4, review_status: 'ok' as const };
        const groups = groupForReview([base, gone, overdue, ok]);
        expect(groups.map((g) => g.name)).toEqual(['Owner no longer has access', 'Review overdue', 'Never reviewed', 'Up to date']);
        expect(groups[0].items.map((c) => c.case_id)).toEqual([2]);
        expect(groupForReview([])).toEqual([]);
    });

    it('parses the interval box', () => {
        expect(intervalFromInput('')).toBeNull();
        expect(intervalFromInput(' 45 ')).toBe(45);
        expect(intervalFromInput('0')).toBe(0);
        expect(intervalFromInput('-1')).toBe('invalid');
        expect(intervalFromInput('1.5')).toBe('invalid');
        expect(intervalFromInput('4000')).toBe('invalid');
    });
});
