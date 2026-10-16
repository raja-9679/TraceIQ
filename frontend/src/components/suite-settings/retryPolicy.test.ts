import { describe, expect, it } from 'vitest';
import { applyRetry, inheritedSummary, retryCount, retryMode } from './retryPolicy';

describe('suite retry policy', () => {
    it('reads the three modes', () => {
        expect(retryMode(undefined)).toBe('inherit');
        expect(retryMode({})).toBe('inherit');
        expect(retryMode({ auto_retry: true })).toBe('on');
        expect(retryMode({ auto_retry: false })).toBe('off');
        expect(retryMode({ auto_retry: 'true' })).toBe('inherit');   // only a real boolean counts, like dispatch
    });

    it('turning retries on stores a bounded count and keeps other settings', () => {
        expect(applyRetry({ har_capture: true }, 'on', 3)).toEqual({ har_capture: true, auto_retry: true, max_retries: 3 });
        expect(applyRetry({}, 'on', 99)).toEqual({ auto_retry: true, max_retries: 5 });
        expect(applyRetry({}, 'on', 0)).toEqual({ auto_retry: true, max_retries: 1 });
        expect(applyRetry({ max_retries: 3 }, 'on')).toEqual({ auto_retry: true, max_retries: 3 });
    });

    it('off and inherit clear the count; inherit clears the switch too', () => {
        const own = { auto_retry: true, max_retries: 3, har_capture: true };
        expect(applyRetry(own, 'off')).toEqual({ auto_retry: false, har_capture: true });
        expect(applyRetry(own, 'inherit')).toEqual({ har_capture: true });
    });

    it('count defaults and clamps like the backend', () => {
        expect(retryCount({})).toBe(2);
        expect(retryCount({ max_retries: '4' })).toBe(4);
        expect(retryCount({ max_retries: 50 })).toBe(5);
        expect(retryCount({ max_retries: -1 })).toBe(0);
    });

    it('explains what an inheriting suite gets', () => {
        expect(inheritedSummary({ auto_retry: true, max_retries: 1 })).toContain('up to 1 retry');
        expect(inheritedSummary({ auto_retry: true })).toContain('up to 2 retries');
        expect(inheritedSummary({})).toContain('Currently off');
    });
});
