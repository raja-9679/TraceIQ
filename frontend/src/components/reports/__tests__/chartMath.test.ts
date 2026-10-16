import { describe, expect, it } from 'vitest';
import {
    areaPath, bandTone, barSlots, budgetColour, fmtDateTime, fmtHours, fmtMs, fmtPct, fmtRate,
    linePath, linearScale, niceMax, pctWidth,
} from '../chartMath';

describe('scales and paths', () => {
    it('maps a domain linearly and a flat domain to the start', () => {
        const s = linearScale(0, 10, 0, 100);
        expect(s(5)).toBe(50);
        expect(linearScale(3, 3, 7, 9)(3)).toBe(7);
    });
    it('niceMax ignores nulls and keeps a floor', () => {
        expect(niceMax([null, 2, 5, undefined])).toBe(5);
        expect(niceMax([0, 0])).toBe(1);
        expect(niceMax([], 100)).toBe(100);
    });
    it('linePath breaks at nulls', () => {
        const d = linePath([1, null, 1], 100, 10, 2, 0);
        expect(d).toBe('M0,5M100,5');
        expect(linePath([], 100, 10)).toBe('');
    });
    it('areaPath closes to the baseline', () => {
        const d = areaPath([0, 2], 100, 10, 2, 0);
        expect(d.startsWith('M0,10L100,0')).toBe(true);
        expect(d.endsWith('L100,10L0,10Z')).toBe(true);
    });
    it('barSlots spreads n bars across the width', () => {
        const s = barSlots(4, 100, 2);
        expect(s).toHaveLength(4);
        expect(s[0]).toEqual({ x: 1, w: 23 });
        expect(s[3].x).toBe(76);
        expect(barSlots(0, 100)).toEqual([]);
    });
    it('pctWidth clamps and handles empty', () => {
        expect(pctWidth(5, 10)).toBe(50);
        expect(pctWidth(20, 10)).toBe(100);
        expect(pctWidth(null, 10)).toBe(0);
        expect(pctWidth(1, 0)).toBe(0);
    });
});

describe('formatting and tones', () => {
    it('formats hours, ms, percents and rates', () => {
        expect(fmtHours(0.5)).toBe('30 min');
        expect(fmtHours(5.25)).toBe('5.3 h');
        expect(fmtHours(72)).toBe('3.0 days');
        expect(fmtHours(null)).toBe('—');
        expect(fmtMs(250)).toBe('250 ms');
        expect(fmtMs(1500)).toBe('1.5 s');
        expect(fmtMs(30000)).toBe('30 s');
        expect(fmtPct(12.345)).toBe('12.3%');
        expect(fmtPct(null)).toBe('—');
        expect(fmtRate(2)).toBe('2.00×');
    });
    it('reads naive datetimes as UTC', () => {
        expect(fmtDateTime('2026-10-16T10:00:00')).toBe(new Date('2026-10-16T10:00:00Z').toLocaleString());
        expect(fmtDateTime(null)).toBe('—');
    });
    it('maps bands and budgets to tones', () => {
        expect(bandTone('elite')).toBe('mint');
        expect(bandTone('low')).toBe('peach');
        expect(bandTone(null)).toBe('neutral');
        expect(budgetColour(-5)).toBe('#E0603A');
        expect(budgetColour(10)).toBe('#F2C94C');
        expect(budgetColour(80)).toBe('#8FD3B6');
        expect(budgetColour(null)).toBe('#D9D6CE');
    });
});
