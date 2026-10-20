import { describe, expect, it } from 'vitest';
import { mechanismLabel, violationSummary } from './rlsStatus';

describe('rls status helpers', () => {
    it('names the restricted role when that is the mechanism', () => {
        expect(mechanismLabel('role', 'traceiq_rls')).toContain('traceiq_rls');
        expect(mechanismLabel('unavailable', null)).toContain('cannot enforce');
        expect(mechanismLabel('not_installed', null)).toContain('migrations');
    });

    it('says nothing was seen when the count is zero', () => {
        expect(violationSummary({ source: 'instance', by_table: {}, total: 0, since: null }))
            .toBe('No out-of-scope rows seen by log mode.');
    });

    it('warns before enforcing when log mode counted rows, and flags a per-process count', () => {
        const s = violationSummary({ source: 'process', by_table: { testrun: 3 }, total: 3, since: null });
        expect(s).toContain('3 out-of-scope row(s)');
        expect(s).toContain('this API process only');
        expect(s).toContain('before enforcing');
    });
});
