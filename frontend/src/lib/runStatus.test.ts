import { describe, expect, it } from 'vitest';
import { errorKindLook, hasVerdict, isLive, isNoVerdict, isTerminal, passRate, statusLabel } from './runStatus';

describe('run status', () => {
    it('separates "over" from "has a verdict"', () => {
        for (const s of ['cancelled', 'timed_out', 'CANCELLED', 'TIMED_OUT']) {
            expect(isTerminal(s)).toBe(true);
            expect(hasVerdict(s)).toBe(false);
            expect(isNoVerdict(s)).toBe(true);
        }
        expect(isTerminal('error')).toBe(true);
        expect(hasVerdict('error')).toBe(true);
        expect(isTerminal('running')).toBe(false);
        expect(isLive('pending')).toBe(true);
        expect(isTerminal(undefined)).toBe(false);
    });

    it('labels every status, and unknown ones as given', () => {
        expect(statusLabel('timed_out')).toBe('Timed out');
        expect(statusLabel('CANCELLED')).toBe('Cancelled');
        expect(statusLabel('pending')).toBe('Queued');
        expect(statusLabel('weird')).toBe('weird');
        expect(statusLabel(null)).toBe('Unknown');
    });

    it('pass rate never counts cancelled or timed-out runs', () => {
        expect(passRate(['passed', 'failed', 'cancelled', 'timed_out', 'running'])).toEqual(
            { rate: 50, passed: 1, counted: 2, ignored: 2 });
        expect(passRate(['cancelled']).rate).toBeNull();
    });

    it('explains error kinds', () => {
        expect(errorKindLook('infrastructure')?.label).toBe('Infrastructure');
        expect(errorKindLook('nope')).toBeNull();
        expect(errorKindLook(undefined)).toBeNull();
    });
});
