import { describe, expect, it } from 'vitest';
import type { Control } from '@/api/compliance';
import { auditParams, auditQuery } from '@/api/compliance';
import { NO_FILTERS, dayEnd, dayStart, defaultPeriod, filterControls, fmtBytes, historyLabel, maxExceptionDay, sortControls } from './helpers';

function control(over: Partial<Control>): Control {
    return {
        id: 1, workspace_id: 1, framework: 'SOC 2', reference: 'CC6.1', title: 't', description: null,
        owner_id: null, owner: null, evidence_sources: [], freshness_days: 90, seeded: false,
        created_at: '', updated_at: '', status: 'passing', status_reason: '', newest_pass_at: null,
        results: [], manual_evidence_count: 0, active_exception: null, pending_exceptions: 0, ...over,
    };
}

describe('compliance helpers', () => {
    const list = [
        control({ id: 1, status: 'passing', owner_id: 5, reference: 'CC6.10' }),
        control({ id: 2, status: 'failing', framework: 'ISO 27001', reference: 'A.5' }),
        control({ id: 3, status: 'stale', reference: 'CC6.2' }),
    ];

    it('filters by framework, status and owner', () => {
        expect(filterControls(list, NO_FILTERS)).toHaveLength(3);
        expect(filterControls(list, { ...NO_FILTERS, framework: 'SOC 2' }).map((c) => c.id)).toEqual([1, 3]);
        expect(filterControls(list, { ...NO_FILTERS, status: 'failing' }).map((c) => c.id)).toEqual([2]);
        expect(filterControls(list, { ...NO_FILTERS, owner: '5' }).map((c) => c.id)).toEqual([1]);
        expect(filterControls(list, { ...NO_FILTERS, owner: 'none' }).map((c) => c.id)).toEqual([2, 3]);
    });

    it('sorts failing first, then stale, then passing; references numerically', () => {
        expect(sortControls(list).map((c) => c.id)).toEqual([2, 3, 1]);
        const refs = sortControls([control({ id: 9, reference: 'CC6.10' }), control({ id: 8, reference: 'CC6.2' })]);
        expect(refs.map((c) => c.reference)).toEqual(['CC6.2', 'CC6.10']);
    });

    it('formats and bounds dates', () => {
        expect(dayStart('2026-10-01')).toBe('2026-10-01T00:00:00Z');
        expect(dayEnd('2026-10-01')).toBe('2026-10-01T23:59:59Z');
        const now = new Date('2026-10-16T12:00:00Z');
        expect(defaultPeriod(now)).toEqual({ start: '2026-07-18', end: '2026-10-16' });
        expect(maxExceptionDay(now)).toBe('2027-10-15');
        expect(fmtBytes(2048)).toBe('2.0 KB');
        expect(historyLabel('exception_approve')).toBe('Exception approved');
        expect(historyLabel('custom_thing')).toBe('custom thing');
    });

    it('builds audit queries without empty filters', () => {
        expect(auditParams({ entity_type: 'auth', action: '', user_id: undefined })).toEqual({ entity_type: 'auth' });
        expect(auditQuery({})).toBe('');
        expect(auditQuery({ action: 'login', user_id: 3 })).toBe('?action=login&user_id=3');
    });
});
