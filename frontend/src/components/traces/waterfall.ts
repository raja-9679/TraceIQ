import type { TraceSpan } from '@/api/traces';

/** One bar of a step's span waterfall, positioned in percent of the step's span window. */
export interface WaterfallRow {
    span: TraceSpan;
    depth: number;
    leftPct: number;
    widthPct: number;
}

/** Smallest visible bar, so a 0.1 ms span is still clickable/readable. */
export const MIN_WIDTH_PCT = 0.75;

/**
 * Lay out a step's spans as a waterfall: parent-before-child order (siblings
 * by start time), depth from the parent chain, and bars scaled to the window
 * from the first span's start to the last span's end. Spans whose parent is
 * not in the list (the step's own span, which the backend never stores) are
 * roots. Pure, so it is unit-tested.
 */
export function layoutWaterfall(spans: TraceSpan[]): { rows: WaterfallRow[]; totalMs: number } {
    if (!spans.length) return { rows: [], totalMs: 0 };
    const ids = new Set(spans.map((s) => s.span_id));
    const children = new Map<string, TraceSpan[]>();
    const roots: TraceSpan[] = [];
    for (const s of spans) {
        if (s.parent_span_id && ids.has(s.parent_span_id) && s.parent_span_id !== s.span_id) {
            const list = children.get(s.parent_span_id) ?? [];
            list.push(s);
            children.set(s.parent_span_id, list);
        } else {
            roots.push(s);
        }
    }
    const byStart = (a: TraceSpan, b: TraceSpan) => a.offset_ms - b.offset_ms || a.span_id.localeCompare(b.span_id);
    const start = Math.min(...spans.map((s) => s.offset_ms));
    const end = Math.max(...spans.map((s) => s.offset_ms + s.duration_ms));
    const totalMs = Math.max(end - start, 0);
    const rows: WaterfallRow[] = [];
    const seen = new Set<string>();
    const walk = (s: TraceSpan, depth: number) => {
        if (seen.has(s.span_id)) return;           // hostile data: a cycle
        seen.add(s.span_id);
        const rawWidth = totalMs > 0 ? (s.duration_ms / totalMs) * 100 : 100;
        const widthPct = Math.min(Math.max(rawWidth, MIN_WIDTH_PCT), 100);
        // A minimum-width bar at the very end is shifted left to stay in the track.
        const leftPct = Math.min(totalMs > 0 ? ((s.offset_ms - start) / totalMs) * 100 : 0, 100 - widthPct);
        rows.push({ span: s, depth, leftPct, widthPct });
        for (const c of (children.get(s.span_id) ?? []).sort(byStart)) walk(c, depth + 1);
    };
    for (const r of roots.sort(byStart)) walk(r, 0);
    // Anything only reachable through a cycle still gets listed, at the root.
    for (const s of [...spans].sort(byStart)) if (!seen.has(s.span_id)) walk(s, 0);
    return { rows, totalMs };
}

export function formatMs(ms: number): string {
    if (!Number.isFinite(ms)) return '—';
    if (ms >= 1000) return `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)} s`;
    if (ms >= 10) return `${Math.round(ms)} ms`;
    return `${Math.round(ms * 10) / 10} ms`;
}

/** Does this result's step evidence carry trace ids at all? */
export function hasTraceIds(steps: { trace?: { trace_id: string } }[] | undefined | null): boolean {
    return !!steps?.some((s) => !!s.trace?.trace_id);
}
