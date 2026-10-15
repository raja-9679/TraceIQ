// Form fields for the frontend-quality steps (WP10). Mirrors the
// /api/step-types catalog entries in backend/app/api/agent_reference.py.

export const FRONTEND_QUALITY_STEP_TYPES = ['expect-no-horizontal-overflow', 'expect-rtl-layout', 'check-page-budget'] as const;

export const isFrontendQualityStep = (t: string): boolean =>
    (FRONTEND_QUALITY_STEP_TYPES as readonly string[]).includes(t);

export const FRONTEND_QUALITY_LABELS: Record<string, string> = {
    'expect-no-horizontal-overflow': 'No Horizontal Overflow',
    'expect-rtl-layout': 'Expect RTL Layout',
    'check-page-budget': 'Page Budget',
};

// Budget thresholds shown on the form, in display order. Each is a maximum.
export const BUDGET_FIELDS: { key: string; label: string }[] = [
    { key: 'lcp_ms', label: 'LCP max (ms)' },
    { key: 'cls', label: 'CLS max' },
    { key: 'inp_ms', label: 'INP max (ms)' },
    { key: 'ttfb_ms', label: 'TTFB max (ms)' },
    { key: 'fcp_ms', label: 'FCP max (ms)' },
    { key: 'page_weight_kb', label: 'Page weight max (KB)' },
    { key: 'request_count', label: 'Requests max' },
    { key: 'largest_image_kb', label: 'Largest image max (KB)' },
];

/** "a, b" -> ['a', 'b']; [] -> undefined so the param is dropped. */
export function listParam(text: string): string[] | undefined {
    const items = text.split(',').map(s => s.trim()).filter(Boolean);
    return items.length ? items : undefined;
}

/** params.budgets with one key set (or removed when blank). */
export function withBudget(budgets: Record<string, unknown> | undefined, key: string, raw: string): Record<string, unknown> | undefined {
    const next: Record<string, unknown> = { ...(budgets || {}) };
    if (raw.trim() === '' || !Number.isFinite(Number(raw))) delete next[key];
    else next[key] = Number(raw);
    return Object.keys(next).length ? next : undefined;
}
