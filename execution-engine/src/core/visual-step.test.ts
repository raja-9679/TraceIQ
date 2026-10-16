/**
 * The `expect-visual-match` step with a fake page: element vs full-page
 * capture, the key passed to baseline resolution, and the reported outcome.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { executeVisualMatch, candidateFileName, VisualStepDeps } from './visual-step';
import { elementKey } from './visual-element-key';
import { TestExecutor } from './test-executor';

class FakePage {
    calls: any[] = [];
    locator(sel: string) {
        const page = this;
        return {
            first() { return this; },
            async screenshot(opts: any) { page.calls.push({ kind: 'element', sel, opts }); },
        };
    }
    async screenshot(opts: any) { this.calls.push({ kind: 'page', opts }); }
    video() { return null; }
    isClosed() { return false; }
}

function deps(baseline: any, compare: any = null) {
    const seen: any = {};
    const d: VisualStepDeps = {
        resolveBaseline: async (q) => { seen.query = q; return baseline; },
        fetchImageBytes: async () => Buffer.from('png'),
        compareScreenshots: async (opts) => { seen.compare = opts; return compare; },
    };
    return { d, seen };
}

const ctx = () => ({ id: 7, browser: 'chromium', device: null, artifacts_dir: '/tmp/a', visual_results: [] as any[],
                     variables: { region: 'eu' } });

test('a selector screenshots only that element under the element key', async () => {
    const page = new FakePage();
    const c = ctx();
    const { d, seen } = deps(null);
    await executeVisualMatch(page as any, page as any,
        { id: 's1', type: 'expect-visual-match', selector: '#banner-{{region}}' }, {}, c,
        TestExecutor.screenshotOptions, d);
    const key = elementKey('#banner-{{region}}');
    assert.equal(page.calls.length, 1);
    assert.equal(page.calls[0].kind, 'element');
    assert.equal(page.calls[0].sel, '#banner-eu');            // templated for the locator
    assert.equal(page.calls[0].opts.path, `/tmp/a/visual-s1-${key}.png`);
    assert.equal(page.calls[0].opts.fullPage, undefined);
    assert.equal(seen.query.elementKey, key);                  // keyed on the authored selector
    assert.deepEqual(c.visual_results, [{
        step_id: 's1', element_key: key, selector: '#banner-{{region}}',
        candidate_file: `visual-s1-${key}.png`, browser: 'chromium', device: null, status: 'capture_only',
    }]);
});

test('no selector keeps the full-page capture and the full-page key', async () => {
    const page = new FakePage();
    const c = ctx();
    const { d, seen } = deps(null);
    await executeVisualMatch(page as any, page as any, { id: 's1', type: 'expect-visual-match' }, {}, c,
        TestExecutor.screenshotOptions, d);
    assert.equal(page.calls[0].kind, 'page');
    assert.equal(page.calls[0].opts.fullPage, true);
    assert.equal(page.calls[0].opts.path, '/tmp/a/visual-s1.png');
    assert.equal(seen.query.elementKey, undefined);
    assert.equal(c.visual_results[0].element_key, '');
    assert.equal(candidateFileName('s1', ''), 'visual-s1.png');
});

test('a failed comparison is reported with its stats, then fails the step', async () => {
    const page = new FakePage();
    const c = ctx();
    const regions = [{ x: 0, y: 0, width: 10, height: 5, pixels: 50, hash_candidate: 'ff00ff00ff00ff00', hash_baseline: '00ff00ff00ff00ff' }];
    const { d, seen } = deps(
        { id: 3, image_url: 'http://x', tolerance: 0.01, mask_regions: [{ x: 1, y: 1, width: 2, height: 2 }] },
        { passed: false, diffRatio: 0.05, diffPixels: 50, totalPixels: 1000, width: 40, height: 25,
          sizeMismatch: false, regions, diffImagePath: '/tmp/a/visual-s1-x.diff.png' });
    await assert.rejects(
        executeVisualMatch(page as any, page as any, { id: 's1', type: 'expect-visual-match', selector: '.card' }, {}, c,
            TestExecutor.screenshotOptions, d),
        /Visual regression: diffRatio=0.0500/);
    assert.deepEqual(seen.compare.maskRegions, [{ x: 1, y: 1, width: 2, height: 2 }]);   // masks still apply
    const r = c.visual_results[0];
    assert.equal(r.status, 'failed');
    assert.equal(r.baseline_id, 3);
    assert.equal(r.diff_pixels, 50);
    assert.deepEqual(r.regions, regions);
    assert.equal(r.diff_file, 'visual-s1-x.diff.png');
});

test('project mask selectors apply to element screenshots too', async () => {
    const page = new FakePage();
    const { d } = deps(null);
    await executeVisualMatch(page as any, page as any, { id: 's1', type: 'expect-visual-match', selector: '.card' },
        { data_policy: { mask_selectors: ['.email'] } }, ctx(), TestExecutor.screenshotOptions, d);
    const opts = page.calls[0].opts;
    assert.equal(opts.mask.length, 1);
});
