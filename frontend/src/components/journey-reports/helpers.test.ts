import { describe, expect, it } from 'vitest';
import { errDetail, formatMetric, formatMs, formatViews, healthTone, moveItem, parseList, parseTargets, pct } from './helpers';

describe('journey report helpers', () => {
    it('parses benchmark targets with optional labels', () => {
        const { targets, errors } = parseTargets('https://us.example.com\nRival | https://rival.io/shop\n\nftp://nope\n');
        expect(targets).toEqual([
            { label: '', base_url: 'https://us.example.com' },
            { label: 'Rival', base_url: 'https://rival.io/shop' },
        ]);
        expect(errors).toEqual(['Line 4: "ftp://nope" is not an http(s) URL']);
    });

    it('splits lists on commas and lines without duplicates', () => {
        expect(parseList(' REQ-1, REQ-2\nREQ-1,,\n')).toEqual(['REQ-1', 'REQ-2']);
    });

    it('formats numbers for the reports', () => {
        expect(formatViews(950)).toBe('950');
        expect(formatViews(1234)).toBe('1.2k');
        expect(formatViews(2_500_000)).toBe('2.5M');
        expect(formatViews(undefined)).toBe('—');
        expect(pct(35.5)).toBe('35.5%');
        expect(pct(null)).toBe('—');
        expect(formatMs(450)).toBe('450 ms');
        expect(formatMs(2400)).toBe('2.4 s');
        expect(formatMetric('cls', 0.1)).toBe('0.100');
        expect(formatMetric('lcp_ms', undefined)).toBe('—');
    });

    it('maps health to tones and moves items', () => {
        expect(healthTone('failing')).toBe('peach');
        expect(healthTone('passed')).toBe('mint');
        expect(healthTone('not_run')).toBe('neutral');
        expect(moveItem([1, 2, 3], 0, 1)).toEqual([2, 1, 3]);
        expect(moveItem([1, 2, 3], 0, -1)).toEqual([1, 2, 3]);
    });

    it('reads API error details', () => {
        expect(errDetail({ response: { data: { detail: 'nope' } } })).toBe('nope');
        expect(errDetail({ response: { data: { detail: [{ msg: 'a' }, { msg: 'b' }] } } })).toBe('a; b');
        expect(errDetail(new Error('x'))).toBe('x');
    });
});
