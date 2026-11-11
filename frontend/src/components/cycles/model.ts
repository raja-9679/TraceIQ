// Pure helpers for the test-cycle pages (no React), unit-tested in model.test.ts.
import type { Tone } from '@/components/redesign/kinds';
import type { Cycle, CycleItem, CycleState, Progress } from '@/api/cycles';

export const STATES: CycleState[] = ['passed', 'failed', 'blocked', 'skipped', 'not_run'];

export const stateLabel: Record<CycleState, string> = {
    passed: 'Passed', failed: 'Failed', blocked: 'Blocked', skipped: 'Skipped', not_run: 'Not run',
};

export const stateTone: Record<CycleState, Tone> = {
    passed: 'mint', failed: 'peach', blocked: 'butter', skipped: 'lavender', not_run: 'neutral',
};

// Literal classes so Tailwind sees them. The bar uses the pastel palette's ink
// colours, softened: the pale fills are too close to tell passed from failed.
export const stateBar: Record<CycleState, string> = {
    passed: 'bg-pastel-ink-mint/60', failed: 'bg-pastel-ink-peach/70', blocked: 'bg-pastel-ink-butter/50',
    skipped: 'bg-pastel-ink-lavender/40', not_run: 'bg-neutral-200',
};

/** A case still needs attention in the cycle until it has passed. */
export const isRemaining = (s: CycleState) => s !== 'passed';

/** Widths (percent) of the stacked bar; every non-empty state stays visible. */
export function barSegments(p: Progress): { state: CycleState; count: number; width: number }[] {
    if (!p.total) return [];
    return STATES.filter((s) => p.counts[s] > 0)
        .map((s) => ({ state: s, count: p.counts[s], width: Math.max(2, (100 * p.counts[s]) / p.total) }));
}

export function progressText(p: Progress): string {
    if (!p.total) return 'No cases yet';
    return `${p.executed} of ${p.total} tested · ${p.counts.passed} passed`;
}

export type CycleBadge = { label: string; tone: Tone };

export function cycleBadge(c: Pick<Cycle, 'status' | 'overdue' | 'signoff'>): CycleBadge {
    if (c.status === 'closed') {
        if (c.signoff?.verdict === 'approve') return { label: 'Closed · approved', tone: 'mint' };
        if (c.signoff?.verdict === 'reject') return { label: 'Closed · rejected', tone: 'peach' };
        return { label: 'Closed', tone: 'neutral' };
    }
    if (c.overdue) return { label: 'Overdue', tone: 'peach' };
    return { label: 'Open', tone: 'lavender' };
}

export type ItemFilter = {
    state: CycleState | 'all' | 'remaining';
    who: 'all' | 'mine' | 'unassigned' | number;
    kind: 'all' | 'manual' | 'automated';
    text: string;
};

export const NO_FILTER: ItemFilter = { state: 'all', who: 'all', kind: 'all', text: '' };

export function filterItems(items: CycleItem[], f: ItemFilter, me: number | null): CycleItem[] {
    const needle = f.text.trim().toLowerCase();
    return items.filter((i) => {
        if (f.state === 'remaining' ? !isRemaining(i.status) : f.state !== 'all' && i.status !== f.state) return false;
        if (f.kind !== 'all' && i.kind !== f.kind) return false;
        if (f.who === 'mine' && (me === null || i.assignee?.id !== me)) return false;
        if (f.who === 'unassigned' && i.assignee) return false;
        if (typeof f.who === 'number' && i.assignee?.id !== f.who) return false;
        if (needle && !`${i.name} ${i.suite_name ?? ''}`.toLowerCase().includes(needle)) return false;
        return true;
    });
}

/** POST /runs accepts at most this many case_ids. */
export const MAX_CASES_PER_RUN = 500;

export type RunGroup = { suiteId: number; suiteName: string; caseIds: number[] };

/**
 * The automated runs to start for a cycle: one per top-level suite (a
 * targeted run's cases must all sit under its suite), split when a suite has
 * more cases than one run accepts.
 */
export function automatedRunPlan(items: CycleItem[], opts: { scope: 'remaining' | 'all'; mineOnly: boolean; me: number | null }): RunGroup[] {
    const bySuite = new Map<number, RunGroup>();
    for (const i of items) {
        if (i.kind !== 'automated' || i.root_suite_id === null) continue;
        if (opts.scope === 'remaining' && !isRemaining(i.status)) continue;
        if (opts.mineOnly && (opts.me === null || i.assignee?.id !== opts.me)) continue;
        const g = bySuite.get(i.root_suite_id) ?? { suiteId: i.root_suite_id, suiteName: '', caseIds: [] };
        g.caseIds.push(i.case_id);
        bySuite.set(i.root_suite_id, g);
    }
    const out: RunGroup[] = [];
    for (const g of [...bySuite.values()].sort((a, b) => a.suiteId - b.suiteId)) {
        for (let at = 0; at < g.caseIds.length; at += MAX_CASES_PER_RUN) {
            out.push({ ...g, caseIds: g.caseIds.slice(at, at + MAX_CASES_PER_RUN) });
        }
    }
    return out;
}

/** Where a case's current result can be read. */
export function itemLink(i: Pick<CycleItem, 'kind' | 'run_id' | 'result_id'>): string | null {
    if (i.run_id === null) return null;
    if (i.kind === 'manual') return `/manual-runs/${i.run_id}`;
    return i.result_id === null ? `/runs/${i.run_id}` : `/runs/${i.run_id}?result=${i.result_id}`;
}

export function dueText(due: string | null, today: Date = new Date()): string | null {
    if (!due) return null;
    const d = new Date(`${due}T00:00:00`);
    const t = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    const days = Math.round((d.getTime() - t.getTime()) / 86_400_000);
    if (days === 0) return 'Due today';
    if (days === 1) return 'Due tomorrow';
    if (days > 1) return `Due in ${days} days`;
    return days === -1 ? 'Due yesterday' : `Due ${-days} days ago`;
}
