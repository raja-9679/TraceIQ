import { describe, it, expect } from 'vitest';
import { dayTotal, daysLeft, stackDay, validateWaiver, waiverDraftFor, waiverLabel, formatBytes } from './a11yModel';

const day = { date: '2026-10-01', critical: 1, serious: 2, moderate: 0, minor: 4, new: 1, fixed: 0, waived: 0 };

describe('accessibility model', () => {
    it('stacks impacts worst-first from the baseline', () => {
        expect(stackDay(day)).toEqual([
            { impact: 'critical', y0: 0, y1: 1 },
            { impact: 'serious', y0: 1, y1: 3 },
            { impact: 'moderate', y0: 3, y1: 3 },
            { impact: 'minor', y0: 3, y1: 7 },
        ]);
        expect(dayTotal(day)).toBe(7);
    });

    it('labels waivers by state and days left', () => {
        const now = new Date('2026-10-01T00:00:00Z');
        expect(daysLeft('2026-10-11T00:00:00Z', now)).toBe(10);
        expect(waiverLabel({ state: 'active', expires_at: '2026-10-11T00:00:00Z' }, now)).toBe('10 days left');
        expect(waiverLabel({ state: 'expired', expires_at: '2026-09-01T00:00:00Z' }, now)).toBe('Expired — counts again');
    });

    it('drafts a waiver for one violation and validates it like the backend', () => {
        const draft = waiverDraftFor({ rule_id: 'color-contrast', target: '#footer > a' });
        expect(draft).toEqual({ rule_id: 'color-contrast', selector_pattern: '#footer > a', reason: '', days: 30 });
        expect(validateWaiver(draft)).toMatch(/why/);
        expect(validateWaiver({ ...draft, reason: 'vendor widget' })).toBe('');
        expect(validateWaiver({ ...draft, reason: 'x', days: 91 })).toMatch(/1-90/);
        expect(validateWaiver({ ...draft, reason: 'x', rule_id: 'not a rule' })).toMatch(/axe rule/);
    });

    it('formats sizes', () => {
        expect(formatBytes(512)).toBe('512 B');
        expect(formatBytes(2048)).toBe('2.0 KB');
        expect(formatBytes(3 * 1024 * 1024)).toBe('3.0 MB');
    });
});
