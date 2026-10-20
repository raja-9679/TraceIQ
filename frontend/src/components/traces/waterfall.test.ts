import { describe, expect, it } from 'vitest';
import type { TraceSpan } from '@/api/traces';
import { MIN_WIDTH_PCT, formatMs, hasTraceIds, layoutWaterfall } from './waterfall';

const span = (id: string, parent: string | null, offset: number, duration: number, extra: Partial<TraceSpan> = {}): TraceSpan => ({
    span_id: id, parent_span_id: parent, service: 'api', operation: id,
    offset_ms: offset, duration_ms: duration, error: false, status_message: null, attributes: {}, ...extra,
});

describe('layoutWaterfall', () => {
    it('orders parents before children and scales to the window', () => {
        const { rows, totalMs } = layoutWaterfall([
            span('db', 'api', 50, 20),
            span('api', 'step', 0, 200),
            span('cache', 'api', 10, 5),
        ]);
        expect(totalMs).toBe(200);
        expect(rows.map((r) => [r.span.span_id, r.depth])).toEqual([['api', 0], ['cache', 1], ['db', 1]]);
        expect(rows[0].leftPct).toBe(0);
        expect(rows[0].widthPct).toBe(100);
        expect(rows[2].leftPct).toBe(25);
        expect(rows[2].widthPct).toBe(10);
    });

    it('keeps tiny spans visible and inside the track', () => {
        const { rows } = layoutWaterfall([span('a', null, 0, 1000), span('b', 'a', 1000, 0)]);
        const b = rows.find((r) => r.span.span_id === 'b')!;
        expect(b.widthPct).toBeGreaterThanOrEqual(MIN_WIDTH_PCT);
        expect(b.leftPct + b.widthPct).toBeLessThanOrEqual(100 + 1e-9);
    });

    it('treats spans with unknown parents as roots and survives cycles', () => {
        const { rows } = layoutWaterfall([span('x', 'y', 0, 1), span('y', 'x', 1, 1), span('z', 'gone', 2, 1)]);
        expect(rows.map((r) => r.span.span_id).sort()).toEqual(['x', 'y', 'z']);
    });

    it('handles empty and zero-length windows', () => {
        expect(layoutWaterfall([])).toEqual({ rows: [], totalMs: 0 });
        const { rows } = layoutWaterfall([span('a', null, 5, 0)]);
        expect(rows[0].widthPct).toBe(100);
    });
});

describe('helpers', () => {
    it('formats durations', () => {
        expect(formatMs(0.42)).toBe('0.4 ms');
        expect(formatMs(42.4)).toBe('42 ms');
        expect(formatMs(1500)).toBe('1.5 s');
        expect(formatMs(25000)).toBe('25 s');
    });
    it('detects trace ids in step evidence', () => {
        expect(hasTraceIds([{}, { trace: { trace_id: 'abc' } }])).toBe(true);
        expect(hasTraceIds([{}])).toBe(false);
        expect(hasTraceIds(undefined)).toBe(false);
    });
});
