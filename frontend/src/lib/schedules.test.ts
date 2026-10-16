import { describe, expect, it } from 'vitest';
import { listTimezones, parseServerDate } from './schedules';

describe('schedule helpers', () => {
    it('reads a naive server timestamp as UTC', () => {
        expect(parseServerDate('2026-10-19T03:30:00').toISOString()).toBe('2026-10-19T03:30:00.000Z');
    });

    it('leaves timestamps that carry a zone alone', () => {
        expect(parseServerDate('2026-10-19T03:30:00Z').toISOString()).toBe('2026-10-19T03:30:00.000Z');
        expect(parseServerDate('2026-10-19T09:00:00+05:30').toISOString()).toBe('2026-10-19T03:30:00.000Z');
    });

    it('always offers UTC', () => {
        const zones = listTimezones();
        expect(zones).toContain('UTC');
        expect(zones.length).toBeGreaterThan(1);
    });
});
