// Pure helpers for the frontend matrix pickers (kept out of the .tsx files
// so those export only components).

export const VIEWPORT_PRESET_NAMES = ['mobile', 'tablet', 'desktop', 'wide'] as const;
export const COLOR_SCHEMES = ['light', 'dark'] as const;
const LOCALE_RE = /^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}$/;
const RTL = new Set(['ar', 'he', 'fa', 'ur', 'ps', 'sd', 'yi', 'dv', 'ckb', 'ug']);

export interface MatrixChoice {
    viewports: string[];
    locales: string[];
    color_schemes: string[];
    reduced_motion: boolean;
}

export const EMPTY_MATRIX: MatrixChoice = { viewports: [], locales: [], color_schemes: [], reduced_motion: false };

/** "en-US, ar-EG  de" -> valid tags (deduped) + the rejected tokens. */
export function parseLocales(text: string): { locales: string[]; invalid: string[] } {
    const locales: string[] = [];
    const invalid: string[] = [];
    for (const raw of text.split(/[\s,]+/)) {
        const t = raw.trim();
        if (!t) continue;
        if (!LOCALE_RE.test(t)) invalid.push(t);
        else if (!locales.some(l => l.toLowerCase() === t.toLowerCase())) locales.push(t);
    }
    return { locales, invalid };
}

export function isRtlLocale(tag: string): boolean {
    return RTL.has(tag.split('-')[0].toLowerCase());
}

/** How many runs per case the choice fans out to (before browsers/devices). */
export function variantCount(m: MatrixChoice): number {
    const any = m.viewports.length || m.locales.length || m.color_schemes.length || m.reduced_motion;
    if (!any) return 1;
    return Math.max(1, m.viewports.length) * Math.max(1, m.locales.length) * Math.max(1, m.color_schemes.length);
}

/** The POST /runs body fields for a choice; empty when nothing is chosen. */
export function matrixRunContext(m: MatrixChoice): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    if (m.viewports.length) out.viewports = m.viewports;
    if (m.locales.length) out.locales = m.locales;
    if (m.color_schemes.length) out.color_schemes = m.color_schemes;
    if (m.reduced_motion) out.reduced_motion = true;
    return out;
}

export function toggle<T>(list: T[], item: T, on: boolean): T[] {
    return on ? (list.includes(item) ? list : [...list, item]) : list.filter(x => x !== item);
}

/** Steps of every variant aligned by step index, for the side-by-side grid. */
export function alignSteps<S extends { index: number }>(columns: S[][]): { index: number; cells: (S | null)[] }[] {
    const indexes = new Set<number>();
    columns.forEach(col => col.forEach(s => indexes.add(s.index)));
    return [...indexes].sort((a, b) => a - b).map(index => ({
        index,
        cells: columns.map(col => col.find(s => s.index === index) || null),
    }));
}

export function formatMetric(metric: string, value: number | null | undefined): string {
    if (value === null || value === undefined) return '—';
    if (metric === 'cls') return value.toFixed(3);
    if (metric.endsWith('_kb')) return `${Math.round(value)} KB`;
    if (metric.endsWith('_ms')) return `${Math.round(value)} ms`;
    return String(Math.round(value * 100) / 100);
}

export function errorDetail(e: unknown): string {
    const err = e as { response?: { data?: { detail?: unknown } }; message?: string } | null;
    const detail = err?.response?.data?.detail;
    return typeof detail === 'string' ? detail : err?.message || 'Unknown error';
}

// Suite settings as stored: locales may be plain tags or objects carrying
// query / cookie / url_template (set through the API).
export type LocaleEntry = string | { locale: string; [k: string]: unknown };
export interface MatrixSettings {
    viewports?: unknown[];
    locales?: LocaleEntry[];
    color_schemes?: string[];
    reduced_motion?: boolean;
    [k: string]: unknown;
}

const tagOf = (l: LocaleEntry): string => (typeof l === 'string' ? l : l?.locale);

export function matrixFromSettings(s: MatrixSettings | null | undefined): MatrixChoice {
    return {
        viewports: (s?.viewports || []).filter((v): v is string => typeof v === 'string'),
        locales: (s?.locales || []).map(tagOf).filter(Boolean),
        color_schemes: s?.color_schemes || [],
        reduced_motion: !!s?.reduced_motion,
    };
}

/** Settings with the matrix replaced; locale objects survive for tags kept. */
export function settingsWithMatrix(s: MatrixSettings | null | undefined, next: MatrixChoice): MatrixSettings {
    const out: MatrixSettings = { ...(s || {}) };
    const objects = new Map<string, LocaleEntry>((s?.locales || [])
        .filter(l => typeof l === 'object' && l !== null).map(l => [tagOf(l).toLowerCase(), l]));
    const locales: LocaleEntry[] = next.locales.map(t => objects.get(t.toLowerCase()) || t);
    if (next.viewports.length) out.viewports = next.viewports; else delete out.viewports;
    if (locales.length) out.locales = locales; else delete out.locales;
    if (next.color_schemes.length) out.color_schemes = next.color_schemes; else delete out.color_schemes;
    if (next.reduced_motion) out.reduced_motion = true; else delete out.reduced_motion;
    return out;
}

/** "mobile, ar-EG, dark" for an inherited matrix, or '' when nothing is inherited. */
export function inheritedSummary(own: MatrixSettings | null | undefined, effective: MatrixSettings | null | undefined): string {
    if (own?.viewports || own?.locales || own?.color_schemes) return '';
    const m = matrixFromSettings(effective);
    return [...m.viewports, ...m.locales, ...m.color_schemes, ...(m.reduced_motion ? ['reduced motion'] : [])].join(', ');
}
