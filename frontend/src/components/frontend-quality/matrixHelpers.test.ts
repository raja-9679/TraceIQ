import { describe, it, expect } from 'vitest';
import {
    EMPTY_MATRIX, alignSteps, formatMetric, inheritedSummary, matrixFromSettings, matrixRunContext, parseLocales,
    settingsWithMatrix, variantCount,
} from './matrixHelpers';

describe('frontend matrix helpers', () => {
    it('parses locale lists and reports junk', () => {
        expect(parseLocales('en-US, ar-EG  de en-us bad_tag')).toEqual({ locales: ['en-US', 'ar-EG', 'de'], invalid: ['bad_tag'] });
    });

    it('counts variants as the cartesian product of chosen axes', () => {
        expect(variantCount(EMPTY_MATRIX)).toBe(1);
        expect(variantCount({ viewports: ['mobile', 'desktop', 'wide'], locales: ['en', 'ar'], color_schemes: [], reduced_motion: false })).toBe(6);
        expect(variantCount({ ...EMPTY_MATRIX, color_schemes: ['dark'] })).toBe(1);
    });

    it('builds the POST /runs body only from chosen axes', () => {
        expect(matrixRunContext(EMPTY_MATRIX)).toEqual({});
        expect(matrixRunContext({ viewports: ['mobile'], locales: [], color_schemes: ['dark'], reduced_motion: true }))
            .toEqual({ viewports: ['mobile'], color_schemes: ['dark'], reduced_motion: true });
    });

    it('keeps API-set locale objects when the suite form edits the tag list', () => {
        const stored = { headers: { a: '1' }, locales: [{ locale: 'ar-EG', query: { lang: 'ar' } }, 'en-US'] };
        expect(matrixFromSettings(stored).locales).toEqual(['ar-EG', 'en-US']);
        const next = settingsWithMatrix(stored, { viewports: ['mobile'], locales: ['ar-EG', 'fr'], color_schemes: [], reduced_motion: false });
        expect(next).toEqual({ headers: { a: '1' }, viewports: ['mobile'], locales: [{ locale: 'ar-EG', query: { lang: 'ar' } }, 'fr'] });
        expect(settingsWithMatrix(next, EMPTY_MATRIX)).toEqual({ headers: { a: '1' } });
        expect(inheritedSummary({}, { viewports: ['wide'], color_schemes: ['dark'] })).toBe('wide, dark');
        expect(inheritedSummary({ viewports: ['mobile'] }, { viewports: ['wide'] })).toBe('');
    });

    it('aligns per-variant steps by index for the side-by-side grid', () => {
        const rows = alignSteps([[{ index: 0 }, { index: 1 }], [{ index: 0 }]]);
        expect(rows.map(r => r.index)).toEqual([0, 1]);
        expect(rows[1].cells[1]).toBeNull();
    });

    it('formats metrics by unit', () => {
        expect(formatMetric('cls', 0.12345)).toBe('0.123');
        expect(formatMetric('lcp_ms', 2499.6)).toBe('2500 ms');
        expect(formatMetric('page_weight_kb', 812.2)).toBe('812 KB');
        expect(formatMetric('request_count', null)).toBe('—');
    });
});
