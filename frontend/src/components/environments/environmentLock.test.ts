import { describe, expect, it } from 'vitest';
import { asUtc, holderText, runLockText } from './environmentLock';

describe('environment lock text', () => {
    it('treats backend timestamps as UTC', () => {
        expect(asUtc('2026-10-20T10:00:00')?.toISOString()).toBe('2026-10-20T10:00:00.000Z');
        expect(asUtc('2026-10-20T10:00:00Z')?.toISOString()).toBe('2026-10-20T10:00:00.000Z');
        expect(asUtc(null)).toBeNull();
    });
    it('describes the holder', () => {
        expect(holderText(null)).toBe('Free');
        expect(holderText({ run_id: null, since: null, error: 'x' })).toBe('Lock state unavailable');
        const t = holderText({ run_id: 12, since: '2026-10-20T10:00:00' }, new Date('2026-10-20T10:05:00Z'));
        expect(t).toMatch(/^In use by run #12 since .* \(5 min ago\)$/);
    });
    it('describes a run that waits or holds', () => {
        expect(runLockText({ run_id: 2, state: 'none' })).toBeNull();
        expect(runLockText({ run_id: 2, state: 'waiting', environment: 'staging', holder_run_id: 1 }))
            .toBe('Waiting for environment staging, held by run #1. It starts automatically when that run finishes.');
        expect(runLockText({ run_id: 1, state: 'holding', environment: 'staging' })).toMatch(/^Holding environment staging/);
    });
});
