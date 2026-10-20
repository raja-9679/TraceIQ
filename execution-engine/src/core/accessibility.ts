/**
 * Structured `check-accessibility` outcomes (WP15d). The step pushes one entry
 * per run of the step onto the case context's `accessibility_results`; the
 * worker ships the list on the job result (like `page_budgets`) and the backend
 * stores it in the project's accessibility register.
 *
 * Only what identifies a problem leaves the page: rule id, impact, help URL,
 * node count and each node's target SELECTOR. axe's `html` and
 * `failureSummary` quote page content and are never copied. Selectors are
 * reduced further by `redactAccessibilityResults` (redact.ts) before shipping.
 */

export const MAX_TARGETS_PER_RULE = 25;
export const MAX_RULES = 100;

/** axe `node.target`: one entry per frame, each a selector or (shadow DOM) a list. */
export function targetSelector(target: any): string {
    if (!Array.isArray(target)) return typeof target === 'string' ? target : '';
    return target.map(t => Array.isArray(t) ? t.map(String).join(' >> ') : String(t)).join(' >>> ');
}

export interface A11yViolationEntry {
    id: string;
    impact: string;
    help_url: string | null;
    node_count: number;
    targets: { selector: string; nodes: number }[];
}

export interface A11yResultEntry {
    step_id: string | null;
    url: string | null;
    violations: A11yViolationEntry[];
}

/** Reduce an axe `analyze()` result to the shipped shape. ALL impacts are kept
 * (the register trends minor ones too); the step decides separately what fails. */
export function summariseAxe(results: any, stepId: any, url: any): A11yResultEntry {
    const violations: A11yViolationEntry[] = [];
    for (const v of (Array.isArray(results?.violations) ? results.violations : []).slice(0, MAX_RULES)) {
        const nodes: any[] = Array.isArray(v?.nodes) ? v.nodes : [];
        const counts = new Map<string, number>();
        for (const n of nodes) {
            const sel = targetSelector(n?.target);
            if (!counts.has(sel) && counts.size >= MAX_TARGETS_PER_RULE) continue;
            counts.set(sel, (counts.get(sel) || 0) + 1);
        }
        violations.push({
            id: String(v?.id || ''),
            impact: String(v?.impact || 'minor'),
            help_url: typeof v?.helpUrl === 'string' ? v.helpUrl : null,
            node_count: nodes.length,
            targets: Array.from(counts, ([selector, n]) => ({ selector, nodes: n })),
        });
    }
    return {
        step_id: stepId ? String(stepId) : null,
        url: typeof url === 'string' ? url : null,
        violations,
    };
}

/** Push onto the case context's list (created on first use). */
export function reportAccessibility(ctx: any, entry: A11yResultEntry): void {
    if (!ctx || typeof ctx !== 'object') return;
    if (!Array.isArray(ctx.accessibility_results)) ctx.accessibility_results = [];
    ctx.accessibility_results.push(entry);
}
