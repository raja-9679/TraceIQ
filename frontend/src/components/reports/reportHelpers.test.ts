import { describe, expect, it } from 'vitest';
import type { PortfolioRow } from '@/api/reports';
import { deltaTone, describeDigestResult, fmtDelta, fmtPct, shareUrl, sortPortfolio, verdictOf } from './reportHelpers';

const row = (name: string, p: Partial<PortfolioRow> = {}): PortfolioRow => ({
    project_id: name.length, project_name: name, pass_rate_7d: null, runs_7d: 0, pass_rate_30d: null,
    runs_30d: 0, failures_7d: 0, last_run: null, verdict: null, flaky_count: 0, open_findings: 0, ...p,
});

describe('report helpers', () => {
    it('formats percentages and changes', () => {
        expect(fmtPct(null)).toBe('—');
        expect(fmtPct(92.5)).toBe('92.5%');
        expect(fmtDelta(3.1, ' pts')).toBe('+3.1 pts');
        expect(fmtDelta(-2)).toBe('-2');
        expect(fmtDelta(0)).toBe('no change');
        expect(fmtDelta(null)).toBe('—');
    });

    it('colours a change by whether up is good', () => {
        expect(deltaTone(2)).toBe('mint');
        expect(deltaTone(-2)).toBe('peach');
        expect(deltaTone(2, false)).toBe('peach');
        expect(deltaTone(0)).toBe('neutral');
        expect(deltaTone(null)).toBe('neutral');
    });

    it('labels verdicts, unknown included', () => {
        expect(verdictOf('not_ready').label).toBe('Not ready');
        expect(verdictOf(null).label).toBe('Unknown');
    });

    it('sorts the portfolio with empty values last in either direction', () => {
        const rows = [row('b', { pass_rate_7d: 50 }), row('a'), row('c', { pass_rate_7d: 90 })];
        expect(sortPortfolio(rows, 'pass_rate_7d', 'asc').map((r) => r.project_name)).toEqual(['b', 'c', 'a']);
        expect(sortPortfolio(rows, 'pass_rate_7d', 'desc').map((r) => r.project_name)).toEqual(['c', 'b', 'a']);
        expect(sortPortfolio(rows, 'project_name', 'asc').map((r) => r.project_name)).toEqual(['a', 'b', 'c']);
        // Worst readiness first when ascending.
        const v = [row('x', { verdict: 'ready' }), row('y', { verdict: 'not_ready' }), row('z')];
        expect(sortPortfolio(v, 'verdict', 'asc').map((r) => r.project_name)).toEqual(['y', 'x', 'z']);
        expect(rows.map((r) => r.project_name)).toEqual(['b', 'a', 'c']); // input untouched
    });

    it('builds share URLs from the app origin', () => {
        expect(shareUrl('/share/abc', 'https://tiq.example.com/')).toBe('https://tiq.example.com/share/abc');
    });

    it('describes the last digest delivery', () => {
        expect(describeDigestResult(null)).toBeNull();
        expect(describeDigestResult({ at: 'x', channels_sent: ['email'], emailed: 2, skipped: { no_access: 1 }, errors: ['no slack webhook set for this project'] }))
            .toBe('sent to email · 2 emails · 1 skipped (no longer have access) · no slack webhook set for this project');
        expect(describeDigestResult({ at: 'x' })).toBe('nothing was sent');
    });
});
