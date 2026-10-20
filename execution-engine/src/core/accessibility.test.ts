import { test } from 'node:test';
import assert from 'node:assert/strict';
import { summariseAxe, targetSelector, reportAccessibility, MAX_TARGETS_PER_RULE } from './accessibility';
import { TestExecutor } from './test-executor';

test('targetSelector joins frame and shadow levels', () => {
    assert.equal(targetSelector(['#a']), '#a');
    assert.equal(targetSelector(['iframe#f', '#b']), 'iframe#f >>> #b');
    assert.equal(targetSelector([['host-el', 'button']]), 'host-el >> button');
    assert.equal(targetSelector(undefined), '');
});

test('summariseAxe keeps rule, impact, help URL, counts and selectors — no html', () => {
    const entry = summariseAxe({
        violations: [{
            id: 'image-alt', impact: 'critical', helpUrl: 'https://dequeuniversity.com/rules/axe/image-alt',
            help: 'Images must have alternate text',
            nodes: [{ target: ['img.a'], html: '<img src=x>' }, { target: ['img.a'] }, { target: ['img.b'] }],
        }, { id: 'region', impact: 'moderate', nodes: [] }],
    }, 'step-1', 'https://app.example/');
    assert.equal(entry.step_id, 'step-1');
    assert.equal(entry.violations[0].node_count, 3);
    assert.deepEqual(entry.violations[0].targets, [{ selector: 'img.a', nodes: 2 }, { selector: 'img.b', nodes: 1 }]);
    assert.ok(!JSON.stringify(entry).includes('<img'));
    assert.ok(!JSON.stringify(entry).includes('alternate text'));
    assert.equal(entry.violations[1].impact, 'moderate');
});

test('summariseAxe caps targets per rule but keeps the node count', () => {
    const nodes = Array.from({ length: 40 }, (_, i) => ({ target: [`li:nth-child(${i})`] }));
    const entry = summariseAxe({ violations: [{ id: 'list', impact: 'serious', nodes }] }, null, null);
    assert.equal(entry.violations[0].targets.length, MAX_TARGETS_PER_RULE);
    assert.equal(entry.violations[0].node_count, 40);
});

test('reportAccessibility appends to the case context list', () => {
    const ctx: any = {};
    reportAccessibility(ctx, { step_id: 'a', url: null, violations: [] });
    reportAccessibility(ctx, { step_id: 'b', url: null, violations: [] });
    assert.equal(ctx.accessibility_results.length, 2);
    reportAccessibility(null, { step_id: 'c', url: null, violations: [] });
});

test('check-accessibility reports to the register even when the step fails', async () => {
    // Stand in for @axe-core/playwright (the dev tree may not have it installed).
    const Module = require('module');
    const origLoad = Module._load;
    const fake = { default: class { constructor(_: any) {} async analyze() {
        return { violations: [{ id: 'button-name', impact: 'critical', nodes: [{ target: ['button.x'] }] }] };
    } } };
    Module._load = function (request: string, ...rest: any[]) {
        return request === '@axe-core/playwright' ? fake : origLoad.call(this, request, ...rest);
    };
    try {
        const ctx: any = { accessibility_results: [] };
        const page: any = { url: () => 'https://app.example/' };
        await assert.rejects(TestExecutor.executeStep(page, page, { id: 's9', type: 'check-accessibility' }, {}, ctx),
            /Accessibility check failed/);
        assert.equal(ctx.accessibility_results.length, 1);
        assert.equal(ctx.accessibility_results[0].violations[0].id, 'button-name');
        // Report-only mode records too and does not throw.
        await TestExecutor.executeStep(page, page, { id: 's10', type: 'check-accessibility', params: { fail: false } }, {}, ctx);
        assert.equal(ctx.accessibility_results.length, 2);
    } finally {
        Module._load = origLoad;
    }
});
