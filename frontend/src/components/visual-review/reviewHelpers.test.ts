import { describe, expect, it } from 'vitest';
import type { VisualReviewGroup, VisualReviewItem } from '@/api/visual';
import { arrangeReview, filterItems, formatRatio, kindLabel, sortItems } from './reviewHelpers';

const item = (id: number, over: Partial<VisualReviewItem> = {}): VisualReviewItem => ({
    id, test_case_id: id, test_name: null, case_name: `case ${id}`, step_id: 's1', browser: 'chromium',
    device: null, element_key: '', selector: null, kind: 'full_page', status: 'failed', diff_ratio: 0.1,
    diff_pixels: 10, total_pixels: 100, width: 10, height: 10, size_mismatch: false, tolerance: 0.01,
    regions: [], baseline_id: 1, baseline_url: null, candidate_url: null, has_candidate: true, diff_url: null,
    decision: null, group_key: null, is_change: true, comment_count: 0, open_comment_count: 0, ...over,
});
const group = (key: string, ids: number[]): VisualReviewGroup => ({
    key, item_ids: ids, size: ids.length, case_count: ids.length, region: { x: 0, y: 0, width: 1, height: 1 },
    max_diff_ratio: 0.1, comment_count: 0, open_comment_count: 0,
});

describe('visual review helpers', () => {
    it('formats ratios as readable percentages', () => {
        expect(formatRatio(null)).toBe('—');
        expect(formatRatio(0)).toBe('0%');
        expect(formatRatio(0.0004)).toBe('<0.1%');
        expect(formatRatio(0.0234)).toBe('2.3%');
        expect(formatRatio(0.5)).toBe('50%');
    });

    it('labels element and full-page items', () => {
        expect(kindLabel({ kind: 'element' })).toBe('Element');
        expect(kindLabel({ kind: 'full_page' })).toBe('Full page');
    });

    it('filters to changes (and new captures) or to undecided ones', () => {
        const items = [
            item(1), item(2, { is_change: false, status: 'passed', diff_ratio: 0 }),
            item(3, { status: 'capture_only', is_change: false }),
            item(4, { decision: { action: 'accept', at: '', actor_label: null } }),
        ];
        expect(filterItems(items, 'changed').map((i) => i.id)).toEqual([1, 3, 4]);
        expect(filterItems(items, 'undecided').map((i) => i.id)).toEqual([1, 3]);
        expect(filterItems(items, 'all')).toHaveLength(4);
    });

    it('sorts by diff ratio (largest first) or by case name', () => {
        const items = [item(1, { diff_ratio: 0.1 }), item(2, { diff_ratio: 0.5, case_name: 'a' }), item(3, { diff_ratio: null })];
        expect(sortItems(items, 'diff').map((i) => i.id)).toEqual([2, 1, 3]);
        expect(sortItems(items, 'case').map((i) => i.id)).toEqual([2, 1, 3]);
    });

    it('shows multi-item groups together and one-item groups as plain items', () => {
        const items = [item(1, { group_key: 'g1' }), item(2, { group_key: 'g1' }), item(3, { group_key: 'g3' }),
            item(4, { is_change: false, status: 'passed' })];
        const out = arrangeReview(items, [group('g1', [1, 2]), group('g3', [3])], 'changed', 'diff');
        expect(out.groups.map((g) => [g.group.key, g.items.map((i) => i.id)])).toEqual([['g1', [1, 2]]]);
        expect(out.singles.map((i) => i.id)).toEqual([3]);
    });
});
