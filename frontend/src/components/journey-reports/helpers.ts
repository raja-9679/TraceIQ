import type { Tone } from '@/components/redesign/kinds';
import type { BenchmarkTarget, Criticality, Health } from '@/api/journeyReports';

// Pure helpers for the WP12c report pages (journey coverage, business
// journeys, flag coverage, benchmarks). Kept out of the .tsx files so those
// export only components.

type ApiError = { response?: { data?: { detail?: unknown } }; message?: string } | null | undefined;

export const errDetail = (err: unknown): string => {
    const e = err as ApiError;
    const d = e?.response?.data?.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map((x: { msg?: string }) => x?.msg || String(x)).join('; ');
    return e?.message || 'Request failed';
};

export const HEALTH_LABEL: Record<Health | 'uncovered', string> = {
    passing: 'Passing', failing: 'Failing', flaky: 'Flaky', not_run: 'Not run', uncovered: 'No case',
};

export function healthTone(h: Health | 'uncovered' | string): Tone {
    switch (h) {
        case 'passing': case 'passed': return 'mint';
        case 'failing': case 'failed': case 'error': return 'peach';
        case 'flaky': return 'butter';
        default: return 'neutral';
    }
}

export function criticalityTone(c: Criticality): Tone {
    return c === 'critical' ? 'peach' : c === 'high' ? 'pink' : c === 'medium' ? 'lavender' : 'neutral';
}

/** 1234 -> "1.2k", 2_500_000 -> "2.5M". */
export function formatViews(n: number | null | undefined): string {
    if (n === null || n === undefined) return '—';
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}k`;
    return String(n);
}

export const pct = (v: number | null | undefined): string => (v === null || v === undefined ? '—' : `${v}%`);

export function formatMs(v: number | null | undefined): string {
    if (v === null || v === undefined) return '—';
    return v >= 1000 ? `${(v / 1000).toFixed(v >= 10_000 ? 0 : 1)} s` : `${Math.round(v)} ms`;
}

export const METRIC_LABEL: Record<string, string> = {
    lcp_ms: 'LCP', fcp_ms: 'FCP', cls: 'CLS', inp_ms: 'INP', ttfb_ms: 'TTFB', load_ms: 'Load',
};

export function formatMetric(key: string, v: number | undefined): string {
    if (v === undefined || v === null) return '—';
    return key === 'cls' ? v.toFixed(3) : formatMs(v);
}

/** Split a free-text list on commas / new lines, trimmed and de-duplicated. */
export function parseList(text: string): string[] {
    const out: string[] = [];
    for (const raw of text.split(/[\n,]/)) {
        const s = raw.trim();
        if (s && !out.includes(s)) out.push(s);
    }
    return out;
}

/**
 * Benchmark targets, one per line: `https://site` or `Label | https://site`.
 * Shape is checked here for quick feedback; the server re-validates (and
 * applies net_guard and the network policy).
 */
export function parseTargets(text: string): { targets: BenchmarkTarget[]; errors: string[] } {
    const targets: BenchmarkTarget[] = [];
    const errors: string[] = [];
    text.split('\n').forEach((line, i) => {
        const s = line.trim();
        if (!s) return;
        const bar = s.lastIndexOf('|');
        const label = bar >= 0 ? s.slice(0, bar).trim() : '';
        const url = (bar >= 0 ? s.slice(bar + 1) : s).trim();
        if (!/^https?:\/\/[^\s/]+/i.test(url)) {
            errors.push(`Line ${i + 1}: "${url}" is not an http(s) URL`);
            return;
        }
        targets.push({ label, base_url: url });
    });
    return { targets, errors };
}

export function moveItem<T>(arr: T[], index: number, delta: -1 | 1): T[] {
    const to = index + delta;
    if (to < 0 || to >= arr.length) return arr;
    const next = arr.slice();
    [next[index], next[to]] = [next[to], next[index]];
    return next;
}

export function shortDate(iso: string | null | undefined): string {
    if (!iso) return '—';
    const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}
