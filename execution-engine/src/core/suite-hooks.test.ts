import { test } from 'node:test';
import assert from 'node:assert/strict';

import { HookFrame, HookRunner, buildCleanup, caseUsesHooks, hookWarnings, withinDeadline } from './suite-hooks';
import { resolveTemplates, templateContextFrom } from './interpolate';

/** A fake world: steps log what ran; `fail` steps throw; extract sets a var. */
function harness(opts: { requestStatus?: number } = {}) {
    const log: string[] = [];
    const requests: Array<{ method: string; url: string; headers: any }> = [];
    const caseCtx: any = { variables: {}, data: {} };
    const settings = { headers: { Authorization: 'Bearer suite' } };
    const runStep = async (step: any, ctx: any) => {
        const label = resolveTemplates(step.value ?? '', templateContextFrom(settings, ctx));
        log.push(`${step.type}:${label}`);
        if (step.type === 'fail') throw new Error(`boom ${label}`);
        if (step.type === 'set') ctx.variables[step.params.name] = step.params.to;
        if (step.type === 'http-request') {
            return { status: step.params?.status ?? 201, url: 'https://api.test/items',
                     request: { url: 'https://api.test/items' }, headers: step.params?.respHeaders || {} };
        }
        return undefined;
    };
    const runner = new HookRunner({
        runStep,
        request: async (req) => {
            requests.push(req);
            log.push(`cleanup:${req.method} ${req.url}`);
            return { status: opts.requestStatus ?? 204 };
        },
        settings,
        caseContext: () => caseCtx,
        stepTimeoutMs: 2000,
    });
    return { log, requests, caseCtx, runner, runStep, settings };
}

const frame = (id: number, setup: string[], teardown: string[] = []): HookFrame => ({
    suite_id: id, suite_name: `S${id}`,
    setup_steps: setup.map(v => ({ type: 'log', value: v })),
    teardown_steps: teardown.map(v => ({ type: 'log', value: v })),
});

test('nested hooks: parent setup first, teardown innermost first', async () => {
    const h = harness();
    const chain = [frame(1, ['p-setup'], ['p-teardown']), frame(2, ['c-setup'], ['c-teardown'])];
    assert.deepEqual(await h.runner.enter(chain), { ok: true });
    h.log.push('CASE');
    await h.runner.finishCase();
    await h.runner.exitAll();
    assert.deepEqual(h.log, ['log:p-setup', 'log:c-setup', 'CASE', 'log:c-teardown', 'log:p-teardown']);
    const report = h.runner.drain();
    assert.deepEqual(report.setup.map(p => p.suite_id), [1, 2]);
    assert.deepEqual(report.teardown.map(p => p.suite_id), [2, 1]);
});

test('continuous order: a shared parent is set up once, siblings swap in between cases', async () => {
    const h = harness();
    const p = frame(1, ['p-up'], ['p-down']);
    const a = frame(2, ['a-up'], ['a-down']);
    const b = frame(3, ['b-up'], ['b-down']);
    await h.runner.enter([p, a]); h.log.push('case1');
    await h.runner.enter([p, a]); h.log.push('case2');
    await h.runner.enter([p, b]); h.log.push('case3');
    await h.runner.exitAll();
    assert.deepEqual(h.log, ['log:p-up', 'log:a-up', 'case1', 'case2', 'log:a-down', 'log:b-up', 'case3',
        'log:b-down', 'log:p-down']);
});

test('setup failure: case must not run, remaining setup skipped, teardown still runs', async () => {
    const h = harness();
    const chain: HookFrame[] = [
        { suite_id: 1, suite_name: 'Outer', setup_steps: [{ type: 'log', value: 'a' }, { type: 'fail', value: 'x' }, { type: 'log', value: 'never' }],
          teardown_steps: [{ type: 'log', value: 'outer-down' }] },
        frame(2, ['inner-up'], ['inner-down']),
    ];
    const entered = await h.runner.enter(chain);
    assert.equal(entered.ok, false);
    assert.match(entered.error!, /Suite setup failed \(Outer\): step 2 \(fail\): boom x/);
    // A second case in the same suite fails fast without re-running setup.
    assert.equal((await h.runner.enter(chain)).ok, false);
    await h.runner.exitAll();
    assert.deepEqual(h.log, ['log:a', 'fail:x', 'log:outer-down']);
    const report = h.runner.drain();
    assert.deepEqual(report.setup[0].steps.map(s => s.status), ['passed', 'failed', 'skipped']);
    assert.equal(report.teardown[0].status, 'passed');
});

test('teardown runs after a case failure; its own failure is a warning, not an exception', async () => {
    const h = harness();
    const chain: HookFrame[] = [{ suite_id: 1, setup_steps: [],
        teardown_steps: [{ type: 'fail', value: 'td' }, { type: 'log', value: 'still-runs' }] }];
    await h.runner.enter(chain);
    // The worker calls these from its finally after the case threw.
    await h.runner.finishCase();
    await h.runner.exitAll();
    assert.deepEqual(h.log, ['fail:td', 'log:still-runs']);
    const report = h.runner.drain();
    assert.equal(report.teardown[0].status, 'failed');
    assert.deepEqual(hookWarnings(report), ['Teardown failed (suite 1): step 1 (fail): boom td']);
});

test('setup exports reach the case as {{setup.X}} and teardown sees case variables', async () => {
    const h = harness();
    const chain: HookFrame[] = [{
        suite_id: 1,
        setup_steps: [{ type: 'set', params: { name: 'token', to: 't-1' } }, { type: 'log', value: 'use {{token}}' }],
        teardown_steps: [{ type: 'log', value: 'del {{order}} with {{setup.token}}' }],
    }];
    await h.runner.enter(chain);
    assert.deepEqual(h.runner.exports(), { token: 't-1' });
    h.caseCtx.variables.order = 'o-9';
    await h.runner.exitAll();
    assert.deepEqual(h.log, ['set:', 'log:use t-1', 'log:del o-9 with t-1']);
});

test('cleanup registration: case cleanups run LIFO after the case, setup ones after their teardown', async () => {
    const h = harness();
    const chain: HookFrame[] = [{
        suite_id: 1,
        setup_steps: [{ type: 'http-request', params: { cleanup: { url: '/projects/{{pid}}' } } },
                      { type: 'set', params: { name: 'pid', to: 'P1' } }],
        teardown_steps: [{ type: 'log', value: 'teardown' }],
    }];
    await h.runner.enter(chain);
    // `{{pid}}` was not set when the setup step registered -> skipped, reported.
    for (const id of ['A', 'B']) {
        h.caseCtx.variables.item = id;
        await h.runner.track({ type: 'http-request', params: { cleanup: { url: '/items/{{item}}' } } },
            h.caseCtx, () => h.runStep({ type: 'http-request', params: {} }, h.caseCtx));
    }
    await h.runner.finishCase();
    await h.runner.exitAll();
    assert.deepEqual(h.log.filter(l => l.startsWith('cleanup') || l === 'log:teardown'), [
        'cleanup:DELETE https://api.test/items/B',
        'cleanup:DELETE https://api.test/items/A',
        'log:teardown',
    ]);
    const report = h.runner.drain();
    assert.deepEqual(report.cleanup.map(c => [c.origin, c.status, c.url]), [
        ['case', 'ok', '/items/{{item}}'],
        ['case', 'ok', '/items/{{item}}'],
        ['setup', 'skipped', '/projects/{{pid}}'],
    ]);
    // Suite headers ride along so the DELETE is authorised like the POST.
    assert.equal(h.requests[0].headers.Authorization, 'Bearer suite');
});

test('cleanup is registered when a request created something then failed an assertion', async () => {
    const h = harness();
    const step = { type: 'http-request', params: { cleanup: true } };
    const err: any = new Error('assertion failed');
    err.stepResult = { status: 201, request: { url: 'https://api.test/items' }, headers: { Location: '/items/77' } };
    await assert.rejects(h.runner.track(step, h.caseCtx, async () => { throw err; }), /assertion failed/);
    // ...but not when the request itself failed (nothing was created).
    const err500: any = new Error('500');
    err500.stepResult = { status: 500, request: { url: 'https://api.test/items' }, headers: { location: '/x' } };
    await assert.rejects(h.runner.track(step, h.caseCtx, async () => { throw err500; }));
    await h.runner.finishCase();
    assert.deepEqual(h.requests.map(r => r.url), ['https://api.test/items/77']);
});

test('a failing cleanup is reported with its status; 404 counts as already gone', async () => {
    const failing = harness({ requestStatus: 500 });
    await failing.runner.track({ type: 'http-request', params: { cleanup: { url: 'https://api.test/x' } } },
        failing.caseCtx, async () => ({ status: 201 }));
    await failing.runner.finishCase();
    const r = failing.runner.drain();
    assert.equal(r.cleanup[0].status, 'failed');
    assert.equal(r.cleanup[0].http_status, 500);
    assert.equal(hookWarnings(r).length, 1);

    const gone = harness({ requestStatus: 404 });
    await gone.runner.track({ type: 'http-request', params: { cleanup: { url: 'https://api.test/x' } } },
        gone.caseCtx, async () => ({ status: 201 }));
    await gone.runner.finishCase();
    assert.equal(gone.runner.drain().cleanup[0].status, 'ok');
});

test('buildCleanup: Location header, extract-value URLs, custom method and expected status', () => {
    const settings = { headers: { A: '1' }, environment: { base_url: 'https://app.test' } };
    const ctx = { variables: { link: '/api/things/5' } };
    const loc = buildCleanup({ type: 'http-request', params: { cleanup: true, headers: { B: '2' } } },
        { request: { url: 'https://api.test/a?k=v' }, headers: { location: 'b/3' } }, settings, ctx);
    assert.equal(loc!.url, 'https://api.test/b/3');
    assert.deepEqual(loc!.headers, { A: '1', B: '2' });
    const ev = buildCleanup({ type: 'extract-value', value: 'link', params: { cleanup: true } }, undefined, settings, ctx);
    assert.equal(ev!.url, 'https://app.test/api/things/5');
    const custom = buildCleanup({ type: 'click', params: { cleanup: { method: 'post', url: '/reset', expect_status: [200, '202'] } } },
        undefined, settings, ctx);
    assert.equal(custom!.method, 'POST');
    assert.deepEqual(custom!.okStatus, [200, 202]);
    assert.match(buildCleanup({ type: 'click', params: { cleanup: true } }, undefined, settings, ctx)!.skip!, /only understood/);
    assert.match(buildCleanup({ type: 'x', params: { cleanup: { url: '/a', method: 'TRACE' } } }, undefined, settings, ctx)!.skip!, /unsupported/);
    assert.equal(buildCleanup({ type: 'x', params: {} }, undefined, settings, ctx), null);
});

test('caseUsesHooks only switches the machinery on when there is something to do', () => {
    assert.equal(caseUsesHooks({ steps: [{ type: 'goto' }] }), false);
    assert.equal(caseUsesHooks({ steps: [], suite_hooks: [{ suite_id: 1, setup_steps: [], teardown_steps: [] }] }), false);
    assert.equal(caseUsesHooks({ steps: [], suite_hooks: [{ suite_id: 1, teardown_steps: [{ type: 'goto' }] }] }), true);
    assert.equal(caseUsesHooks({ steps: [{ type: 'http-request', params: { cleanup: true } }] }), true);
});

test('withinDeadline fails a step once the budget is gone, so teardown can still run', async () => {
    await assert.rejects(withinDeadline(Date.now() - 1, async () => 'x'), /time budget is exhausted/);
    assert.equal(await withinDeadline(undefined, async () => 'x'), 'x');
    await assert.rejects(withinDeadline(Date.now() + 20, () => new Promise(r => setTimeout(r, 500))), /timed out/);
});
