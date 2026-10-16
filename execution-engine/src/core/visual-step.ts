/**
 * The `expect-visual-match` step (web). Moved out of TestExecutor's switch in
 * WP14e so the capture → resolve → compare → report path can be tested with a
 * fake page.
 *
 *   1. Capture the candidate: the whole page, or — when the step has a
 *      `selector` — only that element (Playwright locator.screenshot). Both
 *      honour the project's capture-time mask selectors.
 *   2. Resolve the baseline for (case, step, browser, device, element key).
 *      An element baseline has its own key, so it never collides with the
 *      full-page baseline of the same step.
 *   3. pixelmatch; fail the step over tolerance.
 *   4. Report the outcome (diff ratio, changed pixels, change regions) on the
 *      case context's `visual_results`, shipped on the job result for the
 *      backend's review grouping. The report says which key the candidate was
 *      captured under: that, not the step definition, is what the backend
 *      keys on (an older worker ignores `selector` and reports nothing).
 */
import * as path from 'path';
import type { FrameLocator, Page } from 'playwright';
import { elementKey } from './visual-element-key';
import { redactText } from './redact';
import { resolveTemplates, templateContextFrom } from './interpolate';

export interface VisualStepDeps {
    resolveBaseline: (q: { testCaseId?: number; stepId: string; browser?: string; device?: string; elementKey?: string }) => Promise<any>;
    fetchImageBytes: (url: string) => Promise<Buffer>;
    compareScreenshots: (opts: { candidatePath: string; baselineBytes: Buffer; tolerance: number; maskRegions?: any[] }) => Promise<any>;
}

function defaultDeps(): VisualStepDeps {
    // Lazy: a deployment without pixelmatch keeps working (capture-only).
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { compareScreenshots } = require('../visual-diff');
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { resolveBaseline, fetchImageBytes } = require('../baseline-client');
    return { compareScreenshots, resolveBaseline, fetchImageBytes };
}

export function candidateFileName(stepId: string, key: string): string {
    return key ? `visual-${stepId}-${key}.png` : `visual-${stepId}.png`;
}

function report(ctx: any, entry: any) {
    if (ctx && Array.isArray(ctx.visual_results)) ctx.visual_results.push(entry);
}

export async function executeVisualMatch(
    page: Page,
    context: Page | FrameLocator,
    step: any,
    globalSettings: any,
    ctx: any,
    screenshotOptions: (page: Page, settings: any, extra: any) => any,
    deps?: VisualStepDeps,
): Promise<void> {
    const stepId = step.id || `visual-${Date.now()}`;
    const rawSelector = String(step.selector || step.params?.selector || '').trim();
    // Keyed on the selector as authored (before {{templating}}), so a key
    // stays the same from run to run.
    const key = elementKey(rawSelector);
    let dir = ctx?.artifacts_dir;
    if (!dir) {
        const videoPath = await page.video?.()?.path();
        dir = videoPath ? path.dirname(videoPath) : '/tmp';
    }
    const fileName = candidateFileName(stepId, key);
    const candidatePath = path.join(dir, fileName);
    if (key) {
        const selector = resolveTemplates(rawSelector, templateContextFrom(globalSettings, ctx));
        await context.locator(selector).first().screenshot(
            screenshotOptions(page, globalSettings, { path: candidatePath }));
    } else {
        await page.screenshot(screenshotOptions(page, globalSettings, { path: candidatePath, fullPage: true }));
    }

    const entry: any = {
        step_id: String(stepId),
        element_key: key,
        selector: key ? redactText(rawSelector.slice(0, 500)) : null,
        candidate_file: fileName,
        // The baseline slot this was resolved against (a sweep variant's
        // device is `vp:…`), so a promote lands in the same slot.
        browser: ctx?.browser || 'chromium',
        device: ctx?.device || null,
        status: 'capture_only',
    };

    try {
        const d = deps || defaultDeps();
        const testCaseId = ctx?.id || step.params?.test_case_id;
        const baseline = await d.resolveBaseline({
            testCaseId,
            stepId,
            browser: ctx?.browser || 'chromium',
            device: ctx?.device,
            elementKey: key || undefined,
        });
        if (!baseline) {
            console.log(`[visual-match] no baseline for step ${stepId}${key ? ` (${key})` : ''} — capture-only`);
            report(ctx, entry);
            return;
        }
        const tolerance = baseline.tolerance ?? 0.01;
        const baselineBytes = await d.fetchImageBytes(baseline.image_url);
        const result = await d.compareScreenshots({
            candidatePath, baselineBytes, tolerance, maskRegions: baseline.mask_regions || [],
        });
        Object.assign(entry, {
            status: result.passed ? 'passed' : 'failed',
            baseline_id: baseline.id,
            tolerance,
            diff_ratio: result.diffRatio,
            diff_pixels: result.diffPixels,
            total_pixels: result.totalPixels,
            width: result.width,
            height: result.height,
            size_mismatch: Boolean(result.sizeMismatch),
            regions: result.regions || [],
            diff_file: result.diffImagePath ? path.basename(result.diffImagePath) : null,
        });
        report(ctx, entry);
        console.log(`[visual-match] step=${stepId} diffRatio=${result.diffRatio.toFixed(4)} passed=${result.passed}`);
        if (!result.passed) {
            throw new Error(
                `Visual regression: diffRatio=${result.diffRatio.toFixed(4)} > tolerance=${tolerance}` +
                (result.diffImagePath ? ` (diff at ${result.diffImagePath})` : ''),
            );
        }
    } catch (err: any) {
        if (err?.message?.startsWith('Visual regression')) throw err;
        if (!ctx?.visual_results?.includes(entry)) report(ctx, entry);
        console.log(`[visual-match] degraded to capture-only: ${err?.message}`);
    }
}
