import type { VisualReviewGroup, VisualReviewItem } from '@/api/visual';

// Pure helpers for the visual review page (kept out of component files so
// fast refresh keeps working and the logic is testable).

export type ReviewSort = 'diff' | 'case';
export type ReviewFilter = 'changed' | 'all' | 'undecided';

export const formatRatio = (ratio: number | null | undefined): string => {
    if (ratio === null || ratio === undefined) return '—';
    if (ratio === 0) return '0%';
    const pct = ratio * 100;
    return pct < 0.1 ? '<0.1%' : `${pct < 10 ? pct.toFixed(1) : Math.round(pct)}%`;
};

export const kindLabel = (item: Pick<VisualReviewItem, 'kind'>): string =>
    item.kind === 'element' ? 'Element' : 'Full page';

export const statusLabel = (status: string): string =>
    ({ failed: 'Over tolerance', passed: 'Within tolerance', capture_only: 'No baseline yet' } as Record<string, string>)[status]
    ?? status;

export const itemTitle = (item: VisualReviewItem): string =>
    item.case_name || item.test_name || (item.test_case_id ? `Case #${item.test_case_id}` : 'Unknown case');

export function filterItems(items: VisualReviewItem[], filter: ReviewFilter): VisualReviewItem[] {
    if (filter === 'changed') return items.filter((i) => i.is_change || i.status === 'capture_only');
    if (filter === 'undecided') return items.filter((i) => (i.is_change || i.status === 'capture_only') && !i.decision);
    return items;
}

export function sortItems(items: VisualReviewItem[], sort: ReviewSort): VisualReviewItem[] {
    const copy = [...items];
    if (sort === 'case') {
        copy.sort((a, b) => itemTitle(a).localeCompare(itemTitle(b)) || a.step_id.localeCompare(b.step_id));
    } else {
        copy.sort((a, b) => (b.diff_ratio ?? -1) - (a.diff_ratio ?? -1) || a.id - b.id);
    }
    return copy;
}

export interface GroupView {
    group: VisualReviewGroup;
    items: VisualReviewItem[];
}

/**
 * Groups with their (filtered, sorted) members, plus the items that belong
 * to no multi-item group. A one-item "group" is shown as a plain item: there
 * is nothing to decide together.
 */
export function arrangeReview(
    items: VisualReviewItem[], groups: VisualReviewGroup[], filter: ReviewFilter, sort: ReviewSort,
): { groups: GroupView[]; singles: VisualReviewItem[] } {
    const visible = sortItems(filterItems(items, filter), sort);
    const byGroup = new Map<string, VisualReviewItem[]>();
    for (const i of visible) {
        if (!i.group_key) continue;
        byGroup.set(i.group_key, [...(byGroup.get(i.group_key) ?? []), i]);
    }
    const multi = groups.filter((g) => g.size > 1 && (byGroup.get(g.key)?.length ?? 0) > 0);
    const inMulti = new Set(multi.flatMap((g) => g.item_ids));
    return {
        groups: multi.map((g) => ({ group: g, items: byGroup.get(g.key) ?? [] })),
        singles: visible.filter((i) => !inMulti.has(i.id)),
    };
}

export const regionLabel = (r: { x: number; y: number; width: number; height: number }): string =>
    `${r.width}×${r.height}px at ${r.x}, ${r.y}`;

export const decisionLabel = (d: VisualReviewItem['decision']): string | null => {
    if (!d) return null;
    if (d.action === 'accept') return 'Accepted';
    if (d.action === 'keep') return 'Kept baseline';
    return d.action;
};
