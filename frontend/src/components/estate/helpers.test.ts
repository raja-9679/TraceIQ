import { describe, it, expect } from 'vitest';
import {
    certTone, daysLeft, formatElementChecks, justificationProblem, pageInput, parseElementChecks,
    scoreTone, sparklinePoints, splitList,
} from './helpers';

describe('estate helpers', () => {
    it('tones scores and certificate windows', () => {
        expect(scoreTone(95)).toContain('emerald');
        expect(scoreTone(80)).toContain('amber');
        expect(scoreTone(40)).toContain('rose');
        expect(scoreTone(null)).toContain('slate');
        expect(certTone(5)).toContain('rose');
        expect(certTone(20)).toContain('amber');
        expect(certTone(null)).toContain('slate');
    });

    it('reads naive backend datetimes as UTC', () => {
        const now = new Date('2026-10-15T00:00:00Z');
        expect(daysLeft('2026-10-25T00:00:00', now)).toBe(10);
        expect(daysLeft('2026-10-25T00:00:00Z', now)).toBe(10);
        expect(daysLeft(null, now)).toBeNull();
        expect(daysLeft('garbage', now)).toBeNull();
    });

    it('draws sparklines on a 0..100 scale, clamped', () => {
        expect(sparklinePoints([], 80, 20)).toBe('');
        expect(sparklinePoints([50], 80, 20)).toBe('0,10 80,10');
        expect(sparklinePoints([0, 100, 150], 80, 20)).toBe('0,20 40,0 80,0');
    });

    it('round-trips element checks', () => {
        const checks = parseElementChecks('h1 | Checkout\n#pay | Pay | hacked; owned\n\n#footer');
        expect(checks).toEqual([
            { selector: 'h1', must_contain: 'Checkout', must_not_contain: [] },
            { selector: '#pay', must_contain: 'Pay', must_not_contain: ['hacked', 'owned'] },
            { selector: '#footer', must_contain: null, must_not_contain: [] },
        ]);
        expect(parseElementChecks(formatElementChecks(checks))).toEqual(checks);
    });

    it('splits lists and requires a real justification', () => {
        expect(splitList('a@x.com, b@x.com\n\n c ')).toEqual(['a@x.com', 'b@x.com', 'c']);
        expect(justificationProblem('  ')).toMatch(/required/);
        expect(justificationProblem('ok')).toMatch(/at least/);
        expect(justificationProblem('Card tokenisation iframe')).toBeNull();
    });

    it('defaults a new page to weekly checks (PCI 11.6.1)', () => {
        expect(pageInput(null).cron_expression).toBe('0 6 * * 1');
    });
});
