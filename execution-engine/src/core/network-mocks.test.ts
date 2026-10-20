import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sequenceResponder, fulfillOptions, executeMockResponse, executeHarReplay, harOptions } from './network-mocks';
import { TestExecutor } from './test-executor';

test('sequence answers the nth call with the nth entry, then repeats the last', () => {
    const next = sequenceResponder([{ status: 500 }, { status: 200, json: { ok: true } }]);
    const d1 = next(); const d2 = next(); const d3 = next();
    assert.deepEqual([d1.kind, d2.kind, d3.kind], ['fulfill', 'fulfill', 'fulfill']);
    assert.equal((d1 as any).entry.status, 500);
    assert.equal((d3 as any).entry.status, 200);
    assert.equal((d3 as any).index, 1);
});

test('sequence with fallthrough hands later calls on', () => {
    const next = sequenceResponder([{ status: 503 }], 'fallthrough');
    assert.equal(next().kind, 'fulfill');
    assert.equal(next().kind, 'fallback');
    assert.equal(next().kind, 'fallback');
});

test('sequence rejects an empty list and an unknown mode', () => {
    assert.throws(() => sequenceResponder([]), /non-empty/);
    assert.throws(() => sequenceResponder([{}], 'loop'), /after_sequence/);
});

test('fulfillOptions mirrors the single-response shape', () => {
    assert.deepEqual(fulfillOptions({ json: { a: 1 } }),
        { status: 200, contentType: 'application/json', headers: {}, body: '{"a":1}' });
    assert.deepEqual(fulfillOptions({ status: 404, body: 'nope' }),
        { status: 404, contentType: 'text/plain', headers: {}, body: 'nope' });
});

class FakeRoute {
    public fulfilled: any[] = []; public fallbacks = 0;
    async fulfill(o: any) { this.fulfilled.push(o); }
    async fallback() { this.fallbacks++; }
}

test('mock-response with sequence registers one route that walks the list', async () => {
    let handler: any = null;
    const page: any = { route: async (_p: string, h: any) => { handler = h; } };
    await executeMockResponse(page, {
        selector: '**/api/job', params: { sequence: [{ json: { state: 'pending' } }, { json: { state: 'done' } }], after_sequence: 'fallthrough' },
    }, (v: any) => v);
    const r = new FakeRoute();
    await handler(r); await handler(r); await handler(r);
    assert.deepEqual(r.fulfilled.map(f => JSON.parse(f.body).state), ['pending', 'done']);
    assert.equal(r.fallbacks, 1);
});

test('mock-response without sequence keeps the old single response (via the executor)', async () => {
    let handler: any = null;
    const page: any = { route: async (_p: string, h: any) => { handler = h; } };
    await TestExecutor.executeStep(page, page, { type: 'mock-response', selector: '**/x', params: { status: 201, json: { a: 1 } } });
    const r = new FakeRoute();
    await handler(r); await handler(r);
    assert.equal(r.fulfilled.length, 2);
    assert.equal(r.fulfilled[1].status, 201);
});

test('harOptions validates not_found and passes the URL filter', () => {
    assert.deepEqual(harOptions({ params: {} }), { notFound: 'abort' });
    assert.deepEqual(harOptions({ selector: '**/api/**', params: { not_found: 'fallback' } }),
        { notFound: 'fallback', url: '**/api/**' });
    assert.throws(() => harOptions({ params: { not_found: 'ignore' } }), /not_found/);
});

test('har-replay fetches by run+case, registers routeFromHAR and removes the temp file', async () => {
    const fs = require('fs');
    let seen: any = null; let fileExistedAtCall = false;
    const page: any = { routeFromHAR: async (file: string, opts: any) => { fileExistedAtCall = fs.existsSync(file); seen = { file, opts }; } };
    const calls: any[] = [];
    const fetcher = async (id: number, run: any, kase: any) => { calls.push([id, run, kase]); return '{"log":{"entries":[]}}'; };
    const out = await executeHarReplay(page, { selector: '**/api/**', params: { har_id: 7, har: { id: 7 }, not_found: 'fallback' } },
        { id: 33, run_id: 900 }, {}, fetcher);
    assert.deepEqual(calls, [[7, 900, 33]]);
    assert.ok(fileExistedAtCall);
    assert.ok(!fs.existsSync(seen.file));
    assert.deepEqual(seen.opts, { notFound: 'fallback', url: '**/api/**' });
    assert.equal(out.har_id, 7);
});

test('har-replay fails clearly on a dispatch-time refusal or a missing id', async () => {
    const page: any = { routeFromHAR: async () => { throw new Error('should not be called'); } };
    await assert.rejects(executeHarReplay(page, { params: { har_id: 9, har_error: 'HAR 9 is not an uploaded HAR of this project' } }, { id: 1, run_id: 2 }, {}),
        /not an uploaded HAR of this project/);
    await assert.rejects(executeHarReplay(page, { params: {} }, { id: 1, run_id: 2 }, {}), /needs params.har_id/);
    await assert.rejects(executeHarReplay(page, { params: { har_id: 3 } }, { id: 1, run_id: 2 }, {}, async () => 'not json'),
        /not valid JSON/);
});

test('har-replay is a supported step type', () => {
    assert.ok(TestExecutor.SUPPORTED_STEP_TYPES.includes('har-replay'));
});
