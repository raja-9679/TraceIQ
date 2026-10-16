import type { Tone } from '@/components/redesign/kinds';

// Pure helpers for the inline-SVG report charts (charts.tsx) and the WP12b
// report pages. No charting library: these pages need sparklines and bars.

/** Linear scale from [d0, d1] onto [r0, r1]; a flat domain maps to r0. */
export function linearScale(d0: number, d1: number, r0: number, r1: number) {
    const span = d1 - d0;
    return (v: number) => (span === 0 ? r0 : r0 + ((v - d0) / span) * (r1 - r0));
}

/** Max of the finite values, at least `floor` (keeps an all-zero chart flat). */
export function niceMax(values: (number | null | undefined)[], floor = 1): number {
    const finite = values.filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
    return Math.max(floor, ...finite);
}

/**
 * SVG path for a series inside a width×height box. Null points break the
 * line (a gap means "no data that day", not zero).
 */
export function linePath(values: (number | null)[], width: number, height: number, max?: number, pad = 2): string {
    if (values.length === 0) return '';
    const top = max ?? niceMax(values);
    const x = linearScale(0, Math.max(values.length - 1, 1), pad, width - pad);
    const y = linearScale(0, top, height - pad, pad);
    let d = '';
    let pen = false;
    values.forEach((v, i) => {
        if (v === null || !Number.isFinite(v)) {
            pen = false;
            return;
        }
        d += `${pen ? 'L' : 'M'}${round(x(i))},${round(y(v))}`;
        pen = true;
    });
    return d;
}

/** Closed area under a series (nulls treated as 0). */
export function areaPath(values: (number | null)[], width: number, height: number, max?: number, pad = 2): string {
    if (values.length === 0) return '';
    const filled = values.map((v) => (v === null || !Number.isFinite(v) ? 0 : v));
    const line = linePath(filled, width, height, max ?? niceMax(values), pad);
    const x = linearScale(0, Math.max(values.length - 1, 1), pad, width - pad);
    return `${line}L${round(x(values.length - 1))},${height - pad}L${round(x(0))},${height - pad}Z`;
}

/** Bars for n slots across a width: [{x, width}] with a gap between bars. */
export function barSlots(n: number, width: number, gap = 2): { x: number; w: number }[] {
    if (n <= 0) return [];
    const slot = width / n;
    const w = Math.max(1, slot - gap);
    return Array.from({ length: n }, (_, i) => ({ x: round(i * slot + (slot - w) / 2), w: round(w) }));
}

function round(v: number) {
    return Math.round(v * 100) / 100;
}

/** Share of `part` in `whole`, clamped 0..100, for bar widths. */
export function pctWidth(part: number | null | undefined, whole: number | null | undefined): number {
    if (!part || !whole || whole <= 0) return 0;
    return Math.max(0, Math.min(100, (part / whole) * 100));
}

// --- formatting -------------------------------------------------------------

export function fmtPct(v: number | null | undefined, digits = 1): string {
    return v === null || v === undefined ? '—' : `${v.toFixed(digits)}%`;
}

/** Hours as "45 min", "5.2 h" or "3.1 days". */
export function fmtHours(h: number | null | undefined): string {
    if (h === null || h === undefined) return '—';
    if (h < 1) return `${Math.round(h * 60)} min`;
    if (h < 48) return `${h.toFixed(1)} h`;
    return `${(h / 24).toFixed(1)} days`;
}

export function fmtMs(ms: number | null | undefined): string {
    if (ms === null || ms === undefined) return '—';
    return ms >= 1000 ? `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)} s` : `${Math.round(ms)} ms`;
}

export function fmtRate(v: number | null | undefined): string {
    return v === null || v === undefined ? '—' : `${v.toFixed(2)}×`;
}

// --- tones --------------------------------------------------------------------

export const BAND_LABEL: Record<string, string> = { elite: 'Elite', high: 'High', medium: 'Medium', low: 'Low' };

export function bandTone(band: string | null | undefined): Tone {
    switch (band) {
        case 'elite': return 'mint';
        case 'high': return 'lavender';
        case 'medium': return 'butter';
        case 'low': return 'peach';
        default: return 'neutral';
    }
}

export const SLO_STATUS: Record<string, { tone: Tone; label: string }> = {
    meeting: { tone: 'mint', label: 'Meeting target' },
    at_risk: { tone: 'butter', label: 'At risk (fast burn)' },
    breached: { tone: 'peach', label: 'Target breached' },
    no_data: { tone: 'neutral', label: 'No data' },
};

/** Fill colour for an error-budget bar: green while healthy, red once spent. */
export function budgetColour(remaining: number | null | undefined): string {
    if (remaining === null || remaining === undefined) return '#D9D6CE';
    if (remaining <= 0) return '#E0603A';
    if (remaining < 25) return '#F2C94C';
    return '#8FD3B6';
}

export const CAUSE_LABEL: Record<string, string> = {
    deployment_failed: 'Deployment failed',
    incident: 'Incident within 24 h',
    failed_run: 'Failed run within 24 h',
};

/** Backend datetimes are naive UTC; read them as UTC for display. */
export function fmtDateTime(iso: string | null | undefined): string {
    if (!iso) return '—';
    const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}
