import { describe, expect, it } from 'vitest';
import type { CycleItem, Progress } from '@/api/cycles';
import { automatedRunPlan, barSegments, cycleBadge, dueText, filterItems, itemLink, NO_FILTER, progressText } from './model';

const item = (over: Partial<CycleItem>): CycleItem => ({
    case_id: 1, name: 'Pay by card', kind: 'automated', priority: null, suite_id: 2, suite_name: 'Checkout',
    root_suite_id: 2, assignee: null, status: 'not_run', run_id: null, result_id: null, tested_at: null,
    tested_by: null, configurations: 0, ...over,
});

const progress = (counts: Partial<Progress['counts']>): Progress => {
    const c = { passed: 0, failed: 0, blocked: 0, skipped: 0, not_run: 0, ...counts };
    const total = Object.values(c).reduce((a, b) => a + b, 0);
    return { total, executed: total - c.not_run, counts: c, percent_executed: null, percent_passed: null };
};

describe('cycle progress', () => {
    it('keeps a tiny share visible in the bar', () => {
        const segs = barSegments(progress({ passed: 199, failed: 1 }));
        expect(segs.map((s) => s.state)).toEqual(['passed', 'failed']);
        expect(segs[1].width).toBe(2);
    });
    it('has no bar and says so for an empty cycle', () => {
        expect(barSegments(progress({}))).toEqual([]);
        expect(progressText(progress({}))).toBe('No cases yet');
        expect(progressText(progress({ passed: 2, failed: 1, not_run: 1 }))).toBe('3 of 4 tested · 2 passed');
    });
    it('badges a cycle by state, due date and sign-off', () => {
        expect(cycleBadge({ status: 'open', overdue: false, signoff: null }).label).toBe('Open');
        expect(cycleBadge({ status: 'open', overdue: true, signoff: null }).label).toBe('Overdue');
        const signoff = { id: 1, verdict: 'approve' as const, note: null, signed_by: null, signed_at: '' };
        expect(cycleBadge({ status: 'closed', overdue: false, signoff }).label).toBe('Closed · approved');
        expect(cycleBadge({ status: 'closed', overdue: true, signoff: null }).label).toBe('Closed');
    });
});

describe('filtering cases', () => {
    const sam = { id: 7, name: 'Sam' };
    const items = [
        item({ case_id: 1, status: 'passed', assignee: sam }),
        item({ case_id: 2, status: 'failed', assignee: sam, kind: 'manual', name: 'VAT on invoice', suite_name: 'Invoices' }),
        item({ case_id: 3, status: 'not_run' }),
    ];
    const ids = (f: Partial<typeof NO_FILTER>, me: number | null = 7) => filterItems(items, { ...NO_FILTER, ...f }, me).map((i) => i.case_id);
    it('remaining means anything that has not passed', () => expect(ids({ state: 'remaining' })).toEqual([2, 3]));
    it('mine needs a signed-in person', () => {
        expect(ids({ who: 'mine' })).toEqual([1, 2]);
        expect(ids({ who: 'mine' }, null)).toEqual([]);
    });
    it('filters by person, unassigned, kind and text', () => {
        expect(ids({ who: 7 })).toEqual([1, 2]);
        expect(ids({ who: 'unassigned' })).toEqual([3]);
        expect(ids({ kind: 'manual' })).toEqual([2]);
        expect(ids({ text: ' invoices ' })).toEqual([2]);
    });
});

describe('automated run plan', () => {
    it('groups by top-level suite and leaves manual and passed cases out', () => {
        const plan = automatedRunPlan([
            item({ case_id: 1, root_suite_id: 2 }),
            item({ case_id: 2, root_suite_id: 9, status: 'failed' }),
            item({ case_id: 3, root_suite_id: 2, status: 'passed' }),
            item({ case_id: 4, root_suite_id: 2, kind: 'manual' }),
            item({ case_id: 5, root_suite_id: null }),
        ], { scope: 'remaining', mineOnly: false, me: null });
        expect(plan.map((g) => [g.suiteId, g.caseIds])).toEqual([[2, [1]], [9, [2]]]);
    });
    it('"all" re-runs passed cases; "mine" keeps only my cases', () => {
        const items = [item({ case_id: 1, status: 'passed', assignee: { id: 7, name: 'Sam' } }), item({ case_id: 2 })];
        expect(automatedRunPlan(items, { scope: 'all', mineOnly: false, me: 7 })[0].caseIds).toEqual([1, 2]);
        expect(automatedRunPlan(items, { scope: 'all', mineOnly: true, me: 7 })[0].caseIds).toEqual([1]);
        expect(automatedRunPlan(items, { scope: 'all', mineOnly: true, me: null })).toEqual([]);
    });
    it('splits a suite with more cases than one run accepts', () => {
        const many = Array.from({ length: 501 }, (_, n) => item({ case_id: n + 1 }));
        expect(automatedRunPlan(many, { scope: 'all', mineOnly: false, me: null }).map((g) => g.caseIds.length)).toEqual([500, 1]);
    });
});

describe('links and dates', () => {
    it('opens a manual case in its manual run and an automated one on its result', () => {
        expect(itemLink({ kind: 'manual', run_id: 4, result_id: 9 })).toBe('/manual-runs/4');
        expect(itemLink({ kind: 'automated', run_id: 4, result_id: 9 })).toBe('/runs/4?result=9');
        expect(itemLink({ kind: 'automated', run_id: null, result_id: null })).toBeNull();
    });
    it('describes the due date relative to today', () => {
        const today = new Date(2026, 10, 11, 15, 0);
        expect(dueText('2026-11-11', today)).toBe('Due today');
        expect(dueText('2026-11-12', today)).toBe('Due tomorrow');
        expect(dueText('2026-11-20', today)).toBe('Due in 9 days');
        expect(dueText('2026-11-09', today)).toBe('Due 2 days ago');
        expect(dueText(null, today)).toBeNull();
    });
});
