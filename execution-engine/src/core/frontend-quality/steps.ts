/**
 * Frontend-quality step types (WP10), dispatched from TestExecutor's switch:
 *   expect-no-horizontal-overflow, expect-rtl-layout, check-page-budget.
 */
import type { FrameLocator, Page } from 'playwright';
import { checkHorizontalOverflow, directionFailure, overflowFailure, readDirection } from './layout-checks';
import { executeBudgetStep } from './page-budget';

export const FRONTEND_QUALITY_STEP_TYPES = [
    'expect-no-horizontal-overflow', 'expect-rtl-layout', 'check-page-budget',
] as const;

function overflowOptions(step: any) {
    const p = step?.params || {};
    return {
        tolerance_px: p.tolerance_px,
        ignore: Array.isArray(p.ignore) ? p.ignore : (typeof p.ignore === 'string' && p.ignore ? [p.ignore] : []),
        scope: step?.selector || null,
        max_offenders: p.max_offenders,
    };
}

export async function executeFrontendQualityStep(
    page: Page, context: Page | FrameLocator, step: any, testCaseContext?: any,
): Promise<any> {
    switch (step.type) {
        case 'expect-no-horizontal-overflow': {
            const report = await checkHorizontalOverflow(page, overflowOptions(step));
            const failure = overflowFailure(report);
            console.log(`  [Layout] viewport ${report.viewport_width}px: ${failure ? 'OVERFLOW' : 'no overflow'}`);
            if (failure) throw new Error(failure);
            return { type: step.type, ...report };
        }
        case 'expect-rtl-layout': {
            const expected = String(step.params?.expect_dir || 'rtl').toLowerCase() === 'ltr' ? 'ltr' : 'rtl';
            // `selector` here names an element whose direction must match;
            // the overflow part always covers the whole page.
            const dir = await readDirection(page, context, step.selector || undefined);
            const dirFailure = directionFailure(dir, expected);
            if (dirFailure) throw new Error(dirFailure);
            const report = await checkHorizontalOverflow(page, { ...overflowOptions(step), scope: null });
            const failure = overflowFailure(report);
            if (failure) throw new Error(`${expected.toUpperCase()} layout: ${failure}`);
            return { type: step.type, direction: dir, ...report };
        }
        case 'check-page-budget': {
            // Outcomes accumulate on the per-case context; the worker ships
            // them as the result's `page_budgets` for the trend.
            if (testCaseContext && !Array.isArray(testCaseContext.page_budgets)) testCaseContext.page_budgets = [];
            return executeBudgetStep(page, step, testCaseContext?.page_budgets);
        }
        default:
            throw new Error(`not a frontend-quality step: ${step.type}`);
    }
}
