import { describe, expect, it } from 'vitest';
import { describeGateCheck, labelReason } from './gateChecks';

describe('gate check labels', () => {
    it('names every check the backend emits', () => {
        for (const id of ['pass_rate', 'coverage', 'security_high_findings', 'security_medium_findings',
            'monitors_up', 'external_tests', 'perf_lcp', 'perf_cls', 'perf_ttfb', 'flaky_cases',
            'critical_passed', 'evidence_age', 'manual_signoff', 'accessibility_new_violations',
            'api_breaking_changes']) {
            expect(describeGateCheck(id).title).not.toContain('_');
            expect(describeGateCheck(id).explain).not.toBe('');
        }
    });

    it('falls back to the id with spaces for an unknown check', () => {
        expect(describeGateCheck('brand_new_check').title).toBe('brand new check');
    });

    it('relabels readiness reasons and leaves free text alone', () => {
        expect(labelReason('critical_passed: 1 of 2 passed (needs all critical passed)'))
            .toBe('Critical tests passed: 1 of 2 passed (needs all critical passed)');
        expect(labelReason('no finished runs in this project')).toBe('no finished runs in this project');
    });
});
