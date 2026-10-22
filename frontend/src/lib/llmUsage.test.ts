import { describe, expect, it } from 'vitest';
import { featureLabel, fillDays } from './llmUsage';

describe('featureLabel', () => {
    it('names every feature the backend records', () => {
        expect(featureLabel('assistant')).toBe('Assistant');
        expect(featureLabel('requirement_generation')).toBe('Tests from requirements');
        expect(featureLabel('incident_regression')).toBe('Regression tests from incidents');
    });
    it('never shows a raw id for an unknown one', () => {
        expect(featureLabel('new_thing')).toBe('New thing');
        expect(featureLabel(null)).toBe('Other');
    });
});

describe('fillDays', () => {
    const today = new Date('2026-10-22T09:00:00Z');
    it('gives every day of the window a slot, ending today', () => {
        const out = fillDays([{ date: '2026-10-20', calls: 2, total_tokens: 50 }], 7, today);
        expect(out).toHaveLength(7);
        expect(out[0].date).toBe('2026-10-16');
        expect(out[out.length - 1]).toEqual({ date: '2026-10-22', calls: 0, total_tokens: 0 });
        expect(out.find((d) => d.date === '2026-10-20')?.total_tokens).toBe(50);
    });
    it('keeps a day just before the nominal window', () => {
        const out = fillDays([{ date: '2026-10-15', calls: 1, total_tokens: 9 }], 7, today);
        expect(out[0].date).toBe('2026-10-15');
        expect(out).toHaveLength(8);
    });
});
