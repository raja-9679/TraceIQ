import { describe, expect, it } from 'vitest';
import { fmtBytes, parseComponents, tokenFromHash } from './trustHelpers';
import { assumptionError, defaultRange, normaliseCurrency } from '../poc/pocHelpers';

describe('trust helpers', () => {
    it('parses components into unique trimmed names', () => {
        expect(parseComponents(' API, Checkout ,API,, ')).toEqual(['API', 'Checkout']);
    });
    it('formats sizes', () => {
        expect(fmtBytes(512)).toBe('512 B');
        expect(fmtBytes(2048)).toBe('2 KB');
        expect(fmtBytes(3 * 1024 * 1024)).toBe('3.0 MB');
    });
    it('reads the token only from the fragment', () => {
        expect(tokenFromHash('#abc_123')).toBe('abc_123');
        expect(tokenFromHash('')).toBeNull();
        expect(tokenFromHash('#%E0%A4%A')).toBeNull();
    });
});

describe('poc helpers', () => {
    it('defaults to the last 30 days including today', () => {
        expect(defaultRange(new Date('2026-10-16T12:00:00Z'))).toEqual({ start: '2026-09-17', end: '2026-10-16' });
    });
    it('normalises a typed currency code', () => {
        expect(normaliseCurrency(' eur ')).toBe('EUR');
        expect(normaliseCurrency('$us')).toBe('US');
    });
    it('mirrors the backend validation', () => {
        expect(assumptionError(5, null, '')).toBeNull();
        expect(assumptionError(0, null, '')).toMatch(/Minutes/);
        expect(assumptionError(5, 40, '')).toMatch(/currency/);
        expect(assumptionError(5, 40, 'GBP')).toBeNull();
    });
});
